package dcs

import (
	"os"
	"context"
	"errors"
	"fmt"
	"math"
	"sort"

	"github.com/gojektech/heimdall/v6"
	"github.com/razorpay/goutils/dcs/cache"
	"github.com/razorpay/goutils/dcs/config"
	"github.com/razorpay/goutils/dcs/credentials"
	"github.com/razorpay/goutils/dcs/httpclient"
	"github.com/razorpay/goutils/dcs/logger"
	"github.com/razorpay/goutils/dcs/login"
	rpc "github.com/razorpay/goutils/dcs/rpc/dcs/kv/v1"
	"google.golang.org/protobuf/encoding/protojson"
)

// ConfigClient provides an interface to enable mocking the
type ConfigClient interface {
	// Put adds or replace a key with a value
	Put(ctx context.Context, in *rpc.PutRequest) (*rpc.PutResponse, error)
	// Get gets a list of keys's values This is like a graphql request
	// than a restapi's single resource GET. Clients can specify partial objects
	// by using field masks
	Get(ctx context.Context, in *rpc.GetRequest) (*rpc.GetResponse, error)
	// Patch is used to change few fields of an object in one request
	// If you wish to replace the complete object in the kv store, use Put
	Patch(ctx context.Context, in *rpc.PatchRequest) (*rpc.PatchResponse, error)
	// GetAuditHistory is used to get the audit history of a key
	GetAuditHistory(ctx context.Context, in *rpc.GetAuditHistoryRequest) (*rpc.GetAuditHistoryResponse, error)
}

type modeKey string

// ModeContextKey will add this key in the context for every request
// while processing to select the url dynamically based on test/ live mode
const ModeContextKey = modeKey("dcs_mode_context_key")

type urlKey string

// UrlContextKey will add this key in the context for every request
// while processing to select the url.
const UrlContextKey = urlKey("dcs_url_context_key")

// Defined for to avoid unused variable lint error
var _ ConfigClient = (*Client)(nil)

// Client provides the API operation methods for making requests to
// DCS Server. See this Readme for details on the service.
type Client struct {
	httpClient heimdall.Doer
	config     *config.Config
	log        logger.Logger
	cache      *cache.Cache
	creds      *credentials.Credentials
	dualMode   bool
}

// New will initialize the Client with http client and login tokens
func New(ctx context.Context, opts ...Option) (*Client, error) {
	defaultCache, err := cache.New(ctx, cache.NewConfig())
	if err != nil {
		return nil, err
	}

	c := &Client{
		config:     config.DefaultConfig(),
		log:        logger.NewDefaultLogger(ctx),
		cache:      defaultCache,
		httpClient: httpclient.DefaultHTTPClient(httpclient.DefaultConfig()),
	}

	// Loop through each option
	for _, opt := range opts {
		// Call the option giving the instantiated
		// *client as the argument
		opt(c)
	}

	// sets dual mode if c.config.modes has both Test and Live modes
	c.setDualModeIfApplicable()
	uri, err := c.getLoginURI(ctx)
	if err != nil {
		c.log.Errorf(ctx, "error while getting login  uri: %+v\n", err)
		return c, err
	}

	c.creds = credentials.NewCredentials(
		login.NewLoginer(
			ctx,
			c.config.UserCreds,
			uri,
			c.httpClient,
			c.log,
		),
	)
	_, err = c.creds.Get()
	if err != nil {
		c.log.Errorf(ctx, "error while login to server: %+v\n", err)
		return c, err
	}

	return c, nil
}

func (c *Client) getLoginURI(ctx context.Context) (string, error) {
	if url := GetContextUrl(ctx); url != "" {
		return url, nil
	}

	var url string
	var mode config.Mode
	switch {
	case c.dualMode:
		mode = config.Live
	case len(c.config.Modes) == 1:
		if _, err := c.config.Modes[0].String(); err != nil {
			return "", err
		}
		mode = c.config.Modes[0]
	case c.config.ServerURL != "":
		return c.config.ServerURL, nil
	default:
		return "", fmt.Errorf("%s", "modes are not defined properly in config")
	}

	u, err := config.URIFromEnvAndMode(c.config.Env, mode)
	if err != nil {
		return "", err
	}
	url, err = u.String()
	if err != nil {
		return "", err
	}
	if url == "" {
		url = c.config.ServerURL
	}

	return url, nil
}

func (c *Client) setDualModeIfApplicable() {
	ct := 0.0
	for _, k := range c.config.Modes {
		ct += math.Pow(10, float64(int(k)))
	}

	// ct will be 110 only if live mode and test mode both are in the modes, since modes are iota
	if ct == 110 {
		c.dualMode = true
	}
}

// SetContextMode sets the mode in the context key for using in-case of dual mode
func SetContextMode(ctx context.Context, mode string) context.Context {
	return context.WithValue(ctx, ModeContextKey, mode)
}

// GetContextMode get the mode from the context key for using in-case of dual mode
func GetContextMode(ctx context.Context) string {
	mode := ctx.Value(ModeContextKey)
	if mode == nil {
		return ""
	}
	return mode.(string)
}

// SetContextUrl get the url from the context key,
// this will take priority over default configured in SDK
func SetContextUrl(ctx context.Context, uri string) context.Context {
	return context.WithValue(ctx, UrlContextKey, uri)
}

// GetContextUrl get the url from the context key,
// this will take priority over default configured in SDK
func GetContextUrl(ctx context.Context) string {
	u := ctx.Value(UrlContextKey)
	if u == nil {
		// ARENA PATCH (build-time module replace, arena-only): the SDK derives its host from a hardcoded
		// env->hostname map when no context URL is set; in the arena every call must go to the DCS stub.
		// ARENA_DCS_URL is never set in production images, so this returns "" there (unchanged behaviour).
		return os.Getenv("ARENA_DCS_URL")
	}
	return u.(string)
}

func (c *Client) getURLAndMode(ctx context.Context) (string, string, error) {
	if url := GetContextUrl(ctx); url != "" {
		if mode := GetContextMode(ctx); mode != "" {
			return url, mode, nil
		}
		if len(c.config.Modes) == 1 {
			p, err := c.config.Modes[0].String()
			if err == nil {
				return url, p, nil
			}
		}
		return url, c.config.Mode, nil
	}

	uri := c.config.ServerURL
	mode := c.config.Mode
	if c.dualMode {
		var m config.Mode
		var u config.URI
		var err error

		if m, err = config.GetMode(GetContextMode(ctx)); err != nil {
			return "", "", err
		}
		mode, err = m.String()
		if err != nil {
			return "", "", err
		}

		if u, err = config.URIFromEnvAndMode(c.config.Env, m); err != nil {
			return "", "", err
		}
		uri, err = u.String()
		if err != nil {
			return "", "", err
		}
		return uri, mode, nil
	}
	if len(c.config.Modes) == 1 {
		p, err := c.config.Modes[0].String()
		if err == nil {
			mode = p
		}
	}

	if uri == "" {
		if m, err := config.GetMode(mode); err == nil {
			u, err := config.URIFromEnvAndMode(c.config.Env, m)
			if err == nil {
				v, err := u.String()
				if err == nil {
					uri = v
				}
			}
		}
	}
	if uri == "" {
		return "", "", fmt.Errorf("%s", "mode and uri can't be empty")
	}
	return uri, mode, nil
}

// Put adds or replace a key with a value
func (c *Client) Put(ctx context.Context, in *rpc.PutRequest) (*rpc.PutResponse, error) {
	out := new(rpc.PutResponse)

	creds, err := c.creds.Get()
	if err != nil {
		c.log.Errorf(ctx, "error while login to server: %+v\n", err)
		return nil, err
	}
	url, mode, err := c.getURLAndMode(ctx)
	if err != nil {
		c.log.Errorf(ctx, "error in fetch mode and url for dual mode \n", err)
		return nil, err
	}
	byteResponse, err := httpclient.ExecuteRequest(
		ctx, url, c.httpClient, creds.SessionToken, in, httpclient.PUT)
	if err != nil {
		c.log.Errorf(ctx, "error in executing put request: %+v\n", err)
		return nil, err
	}

	err = protojson.Unmarshal(byteResponse, out)
	if err != nil {
		c.log.Errorf(ctx, "error while unmarshalling put response: %+v\n", err)
		return nil, err
	}

	err = c.cache.Set(cacheKey(in.Key, nil, mode), in.Value)
	if err != nil {
		c.log.Errorf(ctx, "error in setting cache for key: %+v, err:%v\n", in.Key, err)
	}

	return out, nil
}

// Get gets a list of keys's values This is like a graphql requst
// than a restapi's single resource GET. Clients can specify partial objects
// by using field masks
func (c *Client) Get(ctx context.Context, in *rpc.GetRequest) (*rpc.GetResponse, error) {
	creds, err := c.creds.Get()
	if err != nil {
		c.log.Errorf(ctx, "error while login to server: %+v\n", err)
		return nil, err
	}
	url, mode, err := c.getURLAndMode(ctx)
	if err != nil {
		c.log.Errorf(ctx, "error in fetching mode and url\n", err)
		return nil, err
	}
	kvs := make([]*rpc.KeyValue, len(in.Queries))
	extIndexes := []int{}
	extQueries := []*rpc.Query{} // external queries are the queries to dcs server
	for i, query := range in.Queries {
		b, err := c.cache.Get(cacheKey(query.Key, query.Fieldmasks, mode))
		if err == nil {
			// c.log.Infof(ctx, "cache hit for key:%+v", query.Key)
			kvs[i] = &rpc.KeyValue{Key: query.Key, Value: b}
			continue
		}
		if !errors.Is(err, cache.ErrKeyNotFound) {
			c.log.Errorf(ctx, "error fetching cache for query:%+v, err:%v", query, err)
		}
		// in case of key not present in cache or error getting from cache
		// add in the external queries so that new data can be fetched even if cache face issues
		extQueries = append(extQueries, query)
		extIndexes = append(extIndexes, i)
	}

	out := new(rpc.GetResponse)

	if len(extQueries) == 0 {
		out.Kvs = kvs
		// c.log.Infof(ctx, "no external queries made, total cached response")
		return out, nil
	}

	in.Queries = extQueries

	byteResponse, err := httpclient.ExecuteRequest(
		ctx, url, c.httpClient, creds.SessionToken, in, httpclient.GET)
	if err != nil {
		c.log.Errorf(ctx, "error in executing GET request: %+v\n", err)
		return nil, err
	}
	err = protojson.Unmarshal(byteResponse, out)
	if err != nil {
		c.log.Errorf(ctx, "error while unmarshalling GET response: %+v\n", err)
		return nil, err
	}

	for i, extKv := range out.Kvs {
		// set cache for the external queries only
		if i < len(extQueries) {
			kvs[extIndexes[i]] = extKv
			err = c.cache.Set(cacheKey(extKv.Key, extQueries[i].Fieldmasks, mode), extKv.Value)
			if err != nil {
				c.log.Errorf(ctx, "error in setting cache for kv: %+v, err:%v\n", extKv, err)
			}
		}
	}
	out.Kvs = kvs

	return out, nil
}

// Patch updates a key with a value
// For Example if you want to Update the Refund Config of swiggy merchant
func (c *Client) Patch(ctx context.Context, in *rpc.PatchRequest) (*rpc.PatchResponse, error) {
	out := new(rpc.PatchResponse)

	creds, err := c.creds.Get()
	if err != nil {
		c.log.Errorf(ctx, "error while login to server: %+v\n", err)
		return nil, err
	}
	url, _, err := c.getURLAndMode(ctx)
	if err != nil {
		c.log.Errorf(ctx, "error in fetching mode and url\n", err)
		return nil, err
	}

	byteResponse, err := httpclient.ExecuteRequest(
		ctx, url, c.httpClient, creds.SessionToken, in, httpclient.PATCH)
	if err != nil {
		c.log.Errorf(ctx, "error in executing PATCH request: %+v\n", err)
		return nil, err
	}

	err = protojson.Unmarshal(byteResponse, out)
	if err != nil {
		c.log.Errorf(ctx, "error while unmarshalling PATCH response: %+v\n", err)
		return nil, err
	}
	return out, nil
}

// GetAuditHistory will helps in fetching the latest n audit logs of a Key
func (c *Client) GetAuditHistory(
	ctx context.Context, in *rpc.GetAuditHistoryRequest) (*rpc.GetAuditHistoryResponse, error) {
	out := new(rpc.GetAuditHistoryResponse)

	creds, err := c.creds.Get()
	if err != nil {
		c.log.Errorf(ctx, "error while login to server: %+v\n", err)
		return nil, err
	}

	url, _, err := c.getURLAndMode(ctx)
	if err != nil {
		c.log.Errorf(ctx, "error in fetching mode and url\n", err)
		return nil, err
	}

	byteResponse, err := httpclient.ExecuteRequest(
		ctx, url, c.httpClient, creds.SessionToken, in, httpclient.GETAUDITHISTORY)
	if err != nil {
		c.log.Errorf(ctx, "error in executing GetAuditHistory request: %+v\n", err)
		return nil, err
	}

	err = protojson.Unmarshal(byteResponse, out)
	if err != nil {
		c.log.Errorf(ctx, "error while unmarshalling GetAuditHistory response: %+v\n", err)
		return nil, err
	}
	return out, nil
}

// cacheKey returns the rpc key as a string
// which is used to store key's data in cache
func cacheKey(key *rpc.Key, fieldMasks []*rpc.Field, mode string) string {
	if key == nil {
		return ""
	}
	var fmNames []string
	for _, field := range fieldMasks {
		fmNames = append(fmNames, field.Name)
	}
	sort.Strings(fmNames)
	var fieldskey string
	for _, k := range fmNames {
		fieldskey = fmt.Sprintf("-%s-%s", fieldskey, k)
	}
	return fmt.Sprintf(
		"%s/%s/%s/%s/%s/%s/%s",
		mode,
		key.Namespace,
		key.Entity,
		key.EntityId,
		key.Domain,
		key.ObjectName,
		fieldskey,
	)
}
