package dcs

import (
	"strings"

	"github.com/gojektech/heimdall/v6"
	"github.com/razorpay/goutils/dcs/cache"
	"github.com/razorpay/goutils/dcs/config"
	"github.com/razorpay/goutils/dcs/logger"
)

// Option is a functional option that can augment or modify a Client Variables.
type Option func(c *Client)

// WithHTTPClient builds a request Option which will set httpClient on Client
//
// This Option can be used only once.
//
//	clt := dcs.New(ctx, dcs.WithHTTPClient(http.DefaultClient))
func WithHTTPClient(hc heimdall.Doer) Option {
	return func(c *Client) {
		c.httpClient = hc
	}
}

// WithLogger builds a request Option which will set Logger on Client
//
// This Option can be used only once.
//
//	clt := dcs.New(ctx,
//	dcs.WithLogger(logger.DefaultLogger))
func WithLogger(l logger.Logger) Option {
	return func(c *Client) {
		c.log = l
	}
}

// WithCache creates as the cache as set by the user
func WithCache(cache *cache.Cache) Option {
	return func(c *Client) {
		c.cache = cache
	}
}

// WithConfig builds a request Option which will set Logger on Client
//
// This Option can be used only once.
//
//		cfg := dcs.NewConfig().
//				WithServerURL("http://localhost:8081").
//				WithCredentials(&dcs.UserCredentials{
//					Username: "user1",
//					Password: "whatever",
//				}).
//				WithMock(false).
//	                     WithMode("test")
//
//		clt := dcs.New(ctx,
//		dcs.WithConfig(cfg))
func WithConfig(cfg *config.Config) Option {
	return func(c *Client) {
		cfg.ServerURL = strings.TrimSuffix(cfg.ServerURL, "/")
		c.config = cfg
	}
}
