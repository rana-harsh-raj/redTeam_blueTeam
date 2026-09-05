package httpclient

import (
	"context"
	"fmt"
	"net/http"
	"time"

	"net/http/httptrace"

	"github.com/gojektech/heimdall/v6"
	"github.com/gojektech/heimdall/v6/hystrix"
	"github.com/razorpay/goutils/request"
	"github.com/razorpay/goutils/request/httpclient"

	"github.com/opentracing/opentracing-go"
	"github.com/razorpay/goutils/tracing/integrations"
)

const (
	defaultHTTPClientURL = "http://localhost:8081"
)

// Requester is a wrapper struct around request.IRequester,
// We will use this Requester for any http communication with Config Server
// httpClient for this Requester will be specified by clients while Creating new Client
// Usage:
//
//	httpReq, err := httpclient.NewRequest(ctx, http.MethodPost, url, in)
//	if err != nil {
//		return nil, err
//	}
//
//	reqType.SetAuthorizationHeader(httpReq, token)
//
//	byteRes, statusCode, err := httpReq.MakeRequest(httpClient)
//	if err != nil {
//		return nil, err
//	}
type Requester struct {
	request.IRequester
}

type Config struct {
	Command       string
	ConnPool      httpclient.ConnPoolConfig
	HystrixConfig httpclient.HystrixResiliencyConfig
	// Create a new retry mechanism with the backoff
	RetryCount int
	// First set a backoff mechanism. Constant backoff increases the backoff at a constant rate
	BackoffInterval time.Duration
	// Define a maximum jitter interval. It must be more than 1*time.Millisecond
	MaximumJitterInterval time.Duration
}

// DefaultHTTPClient will return go-utils http-client with specified config
func DefaultHTTPClient(cfg Config) *hystrix.Client {
	// First set a backoff mechanism. Constant backoff increases the backoff at a constant rate
	backoffInterval := cfg.BackoffInterval * time.Millisecond

	// Define a maximum jitter interval. It must be more than 1*time.Millisecond
	maximumJitterInterval := cfg.MaximumJitterInterval * time.Millisecond

	backoff := heimdall.NewConstantBackoff(backoffInterval, maximumJitterInterval)

	// Create a new retry mechanism with the backoff
	retrier := heimdall.NewRetrier(backoff)
	httpClient := httpclient.InitializeClient(
		cfg.Command,
		httpclient.ConnPoolConfig{
			Timeout:            cfg.ConnPool.Timeout,
			KeepAliveTimeout:   cfg.ConnPool.KeepAliveTimeout,
			MaxIdleConnections: cfg.ConnPool.MaxIdleConnections,
		},
		httpclient.HystrixResiliencyConfig{
			MaxConcurrentRequests:     cfg.HystrixConfig.MaxConcurrentRequests,
			RequestVolumeThreshold:    cfg.HystrixConfig.RequestVolumeThreshold,
			CircuitBreakerSleepWindow: cfg.HystrixConfig.CircuitBreakerSleepWindow,
			ErrorPercentThreshold:     cfg.HystrixConfig.ErrorPercentThreshold,
			CircuitBreakerTimeout:     cfg.HystrixConfig.CircuitBreakerTimeout,
		},
		retrier,
		cfg.RetryCount,
		nil,
		httpclient.PrometheusRequestInstrumenter(),
	)
	return httpClient
}

// DefaultConfig will return config with default Hystrix config and default connection pool config
func DefaultConfig() Config {
	return Config{
		Command: "client_dcs",
		ConnPool: httpclient.ConnPoolConfig{
			Timeout:            250,
			KeepAliveTimeout:   40000,
			MaxIdleConnections: 15,
		},
		HystrixConfig: httpclient.HystrixResiliencyConfig{
			MaxConcurrentRequests:     100,
			RequestVolumeThreshold:    30,
			CircuitBreakerSleepWindow: 5 * 100,
			ErrorPercentThreshold:     100,
			CircuitBreakerTimeout:     300,
		},
		RetryCount:            3,
		BackoffInterval:       100,
		MaximumJitterInterval: 100,
	}
}

// NewRequest creates a new http Json Request with specified method, url and body
// It returns an instance of pointer to the Requester while will be used to MakeRequest
func NewRequest(ctx context.Context, method string, url string, body interface{}) (*Requester, error) {
	req, err := request.NewJsonRequest(ctx, method, url, body)
	if err != nil {
		return nil, err
	}

	return &Requester{req}, nil
}

// SetBearerToken is used to set Bearer Authorization Header
// It is a missing function from request library
// Update's Requester pointer in the receiver and return the same object pointer
func (r *Requester) SetBearerToken(token string) request.IRequester {
	r.SetHeaders(map[string]string{
		"Authorization": "Bearer " + token,
	})

	return r
}

// ExecuteRequest is a common function for all the http communication with the DCS Sever
// Using httpclient.NewRequest it Generates the NewJsonRequest
// It returns the responseBytes and error.
func ExecuteRequest(ctx context.Context, baseURL string,
	httpClient heimdall.Doer, token string, in interface{}, reqType RequestType) ([]byte, error) {
	if baseURL == "" {
		baseURL = defaultHTTPClientURL
	}

	clientSpan, _ := opentracing.StartSpanFromContext(ctx, "DcsHttpRequest")
	defer clientSpan.Finish()
	url := fmt.Sprintf("%s/%s", baseURL, reqType.URI())
	var trace *httptrace.ClientTrace
	trace = integrations.NewClientTrace(clientSpan)
	ctx = httptrace.WithClientTrace(ctx, trace)

	httpReq, err := NewRequest(ctx, http.MethodPost, url, in)
	if err != nil {
		return nil, err
	}

	reqType.SetAuthorizationHeader(httpReq, token)

	byteRes, statusCode, err := httpReq.MakeRequest(httpClient)
	if err != nil {
		return nil, err
	}

	if statusCode == http.StatusUnauthorized {
		return nil, fmt.Errorf(
			"unauthorized to make the request to %s, status code:%v",
			url,
			statusCode,
		)
	}

	if statusCode != http.StatusOK {
		return nil, fmt.Errorf(
			"internal server error in making request to %s, status code:%v",
			url,
			statusCode,
		)
	}

	return byteRes, nil
}
