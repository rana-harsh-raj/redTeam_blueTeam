package dcs

import (
	"context"
	"net/http"

	"github.com/razorpay/config-proto/gen"
	"github.com/razorpay/goutils/dcs"
	"github.com/razorpay/goutils/dcs/config"
	"github.com/razorpay/goutils/dcs/logger"
)

// Config stores the dcs configuration
// that needs to be provided for every environment
type Config struct {
	// Username specifies the user that is used to talk to dcs server
	Username string
	// Password for interacting the dcs server
	Password string
	// Env will represent dcs pod env which will be hit
	Env string
}

// Client is the wrapper over dcs client
// the users of this package interact with
// this Client instead of dcs.Client
type Client struct {
	*dcs.Client
}

func options(ctx context.Context, cnf Config, env config.Env) []dcs.Option {
	creds := &config.UserCredentials{
		Username: cnf.Username,
		Password: cnf.Password,
	}

	cfg := config.NewConfig().
		WithMock(false).
		WithModes([]config.Mode{config.Live, config.Test}). // since it is dual mode add both modes
		WithEnv(env).                                       // fetch env from os environment variables
		WithCredentials(creds)

	return []dcs.Option{
		dcs.WithConfig(cfg),
		dcs.WithLogger(logger.NewDefaultLogger(ctx)),
		dcs.WithHTTPClient(http.DefaultClient),
	}
}

// New creates the new client for dcs
func New(ctx context.Context, cnf Config) (*Client, error) {
	env, err := config.GetEnv(cnf.Env)
	if err != nil {
		return nil, err
	}
	client, err := dcs.New(ctx, options(ctx, cnf, env)...)
	if err != nil {
		return nil, err
	}

	return &Client{
		client,
	}, nil
}

// Marshal takes in the key and the field's map
// and returns a marshalled key's object
// it also returns the list of fields that makes the bytes
func Marshal(key string, fields []byte) ([]byte, []string, error) {
	fn, err := gen.FetchMarshalFn(key)
	if err != nil {
		return nil, []string{}, err
	}

	return fn(fields)
}
