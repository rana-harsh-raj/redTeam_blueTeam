package login

import (
	"context"
	"time"

	"github.com/gojektech/heimdall/v6"
	"github.com/razorpay/goutils/dcs/config"
	"github.com/razorpay/goutils/dcs/credentials"
	"github.com/razorpay/goutils/dcs/httpclient"
	"github.com/razorpay/goutils/dcs/logger"
	authrpc "github.com/razorpay/goutils/dcs/rpc/dcs/auth/v1"
	"google.golang.org/protobuf/encoding/protojson"
)

// A login represents a DCS Sever Credential Manager
// which holds the credentials, expiry details
// Usage:
// Client.creds := credentials.NewCredentials(NewLoginer(ctx, c.config.UserCreds, c.config.ServerURL, c.httpClient, c.log))
//
// creds, err := Client.creds.Get()
//
//	if err != nil {
//		c.log.Errorf(ctx, "error while login to server: %+v\n", err)
//		return nil, err
//	}
//
// Client.creds.Get() fetches JWT token from Cache or internally calls registered
// calls Retrieve function which fetches Token from DCS Server and Update the Cache.
type loginer struct {
	credentials.Expiry
	ExpiryWindow time.Duration
	creds        *config.UserCredentials
	httpClient   heimdall.Doer
	log          logger.Logger
	ServerURL    string
}

// NewLoginer will return a login struct which will be used for login into
// server majorly used for JWT Token generation
func NewLoginer(_ context.Context, creds *config.UserCredentials, url string,
	httpClient heimdall.Doer, log logger.Logger) *loginer {
	clientToken := &loginer{
		creds:      creds,
		httpClient: httpClient,
		log:        log,
		ServerURL:  url,
	}

	return clientToken
}

func buildLoginRequest(config *config.UserCredentials) *authrpc.LoginRequest {
	request := &authrpc.LoginRequest{
		Username: config.Username,
		Password: config.Password,
	}
	return request
}

// Login fetches the JWT token from DCS Sever.
// Usage:
// resp, err := l.Login(ctx, buildLoginRequest(l.creds))
//
//	if err != nil {
//		l.log.Errorf(ctx, "Error while Retrieving the Credentials: %+v\n", err)
//		return credentials.Value{}, err
//	}
func (l *loginer) Login(ctx context.Context, in *authrpc.LoginRequest) (*authrpc.LoginResponse, error) {
	out := new(authrpc.LoginResponse)

	l.log.Infof(ctx, "Login(): %+v\n ", in.Username)

	byteResponse, err := httpclient.ExecuteRequest(ctx, l.ServerURL, l.httpClient, "", in, httpclient.LOGIN)
	if err != nil {
		l.log.Errorf(ctx, "error in executing GetLogin request: %+v\n", err)
		return nil, err
	}

	err = protojson.Unmarshal(byteResponse, out)
	if err != nil {
		l.log.Errorf(ctx, "error while unmarshalling GetLogin response: %+v\n", err)
		return nil, err
	}

	return out, nil
}

// Retrieve will Fetch token from DCS Sever
// It implements credentials.Expiry Retrieve function
func (l *loginer) Retrieve(ctx context.Context) (credentials.Value, error) {
	resp, err := l.Login(ctx, buildLoginRequest(l.creds))
	if err != nil {
		l.log.Errorf(ctx, "Error while Retrieving the Credentials: %+v\n", err)
		return credentials.Value{}, err
	}

	l.SetExpiration(time.Now().Add(6*time.Hour), l.ExpiryWindow)

	return credentials.Value{
		SessionToken: resp.AccessToken,
	}, nil
}
