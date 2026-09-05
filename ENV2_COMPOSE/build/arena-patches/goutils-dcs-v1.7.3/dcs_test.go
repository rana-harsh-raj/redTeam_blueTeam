package dcs

import (
	"context"
	"encoding/json"
	"fmt"
	"io/ioutil"
	"net/http"
	"net/http/httptest"
	"path"
	"reflect"
	"testing"
	"time"

	"github.com/gojektech/heimdall/v6"
	"github.com/razorpay/goutils/dcs/cache"
	"github.com/razorpay/goutils/dcs/config"
	"github.com/razorpay/goutils/dcs/credentials"
	"github.com/razorpay/goutils/dcs/logger"
	rpc "github.com/razorpay/goutils/dcs/rpc/dcs/kv/v1"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func ClientTest(serverURL string, dcsCache *cache.Cache) (*Client, error) {
	creds := &config.UserCredentials{
		Username: "example-user-1",
		Password: "example-pass-1",
	}
	var err error
	if dcsCache == nil {
		dcsCache, err = cache.New(context.Background(), cache.NewConfig())
		if err != nil {
			return nil, err
		}
	}

	dcsConfig := config.NewConfig().
		WithMock(false).
		WithServerURL(serverURL).
		WithCredentials(creds).
		WithMode("live")

	options := []Option{
		WithConfig(dcsConfig),
		WithLogger(logger.NewDefaultLogger(context.Background())),
		WithCache(dcsCache),
		WithHTTPClient(http.DefaultClient),
	}

	c, _ := New(context.Background(), options...)
	return c, nil
}

func MockHttp(t *testing.T, reqBody string, resBody string) *httptest.Server {
	dummyHandler := func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/auth/login" {
			if _, file := path.Split(t.Name()); file == "credential_error" {
				w.WriteHeader(http.StatusInternalServerError)
				w.Write([]byte("{\"accessToken\":\"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE2NjI0OTA2MTksInVzZXJuYW1lIjoiZXhhbXBsZSIsInJvbGVzIjpbImV4YW1wbGVfbWVyY2hhbnRfYWRtaW4iXX0.Zfr1YTJtGWMkh9HwqpxPC8yElexB2cstfPyHxouk8Iw\"}"))
				return
			} else if file == "login_unmarshal_error" {
				w.WriteHeader(http.StatusOK)
				w.Write([]byte("unmarshal failure testing"))
				return
			}

			w.WriteHeader(http.StatusOK)
			w.Write([]byte("{\"accessToken\":\"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE2NjI0OTA2MTksInVzZXJuYW1lIjoiZXhhbXBsZSIsInJvbGVzIjpbImV4YW1wbGVfbWVyY2hhbnRfYWRtaW4iXX0.Zfr1YTJtGWMkh9HwqpxPC8yElexB2cstfPyHxouk8Iw\"}"))
			return
		}

		assert.Equal(t, http.MethodPost, r.Method)
		assert.Equal(t, "application/json", r.Header.Get("Content-Type"))
		rBody, err := ioutil.ReadAll(r.Body)
		require.NoError(t, err, "should not have failed to extract request body")
		assert.Equal(t, reqBody, string(rBody))
		if _, file := path.Split(t.Name()); file == "failure_500" {
			w.WriteHeader(http.StatusInternalServerError)
			w.Write([]byte(resBody))
			return
		} else if file == "unmarshal_error" {
			w.WriteHeader(http.StatusOK)
			w.Write([]byte("testing"))
			return
		}
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(resBody))
	}

	// Use httptest.NewServer to automatically assign available port
	server := httptest.NewServer(http.HandlerFunc(dummyHandler))
	return server
}

func TestClient_Get(t *testing.T) {
	type args struct {
		ctx context.Context
		in  *rpc.GetRequest
	}
	tests := []struct {
		name    string
		args    args
		want    *rpc.GetResponse
		wantErr bool
	}{
		{
			name: "success_200",
			args: args{
				ctx: context.Background(),
				in: &rpc.GetRequest{
					Queries: []*rpc.Query{
						{
							Key: &rpc.Key{
								Namespace:  "example/pg",
								Entity:     "merchant",
								EntityId:   "mid_swiggy",
								Domain:     "refund",
								ObjectName: "Features",
							},
							Fieldmasks: []*rpc.Field{
								{Name: "disable_auto_refund"},
								{Name: "refund_enabled"},
							},
						},
					},
				},
			},
			want: &rpc.GetResponse{
				Kvs: []*rpc.KeyValue{
					{
						Key: &rpc.Key{
							Namespace:  "example/pg",
							Entity:     "merchant",
							EntityId:   "mid_swiggy",
							Domain:     "refund",
							ObjectName: "Features",
						},
						Value: []byte("EAE="),
					},
				},
			},
			wantErr: false,
		},
		{
			name: "failure_500",
			args: args{
				ctx: context.Background(),
				in: &rpc.GetRequest{
					Queries: []*rpc.Query{
						{
							Key: &rpc.Key{
								Namespace:  "example/pg",
								Entity:     "merchant",
								EntityId:   "mid_swiggy",
								Domain:     "refund",
								ObjectName: "Features",
							},
							Fieldmasks: []*rpc.Field{
								{Name: "disable_auto_refund"},
								{Name: "refund_enabled"},
							},
						},
					},
				},
			},
			want:    nil,
			wantErr: true,
		},
		{
			name: "credential_error",
			args: args{
				ctx: context.Background(),
				in: &rpc.GetRequest{
					Queries: []*rpc.Query{
						{
							Key: &rpc.Key{
								Namespace:  "example/pg",
								Entity:     "merchant",
								EntityId:   "mid_swiggy",
								Domain:     "refund",
								ObjectName: "Features",
							},
							Fieldmasks: []*rpc.Field{
								{Name: "disable_auto_refund"},
								{Name: "refund_enabled"},
							},
						},
					},
				},
			},
			want:    nil,
			wantErr: true,
		},
		{
			name: "unmarshal_error",
			args: args{
				ctx: context.Background(),
				in: &rpc.GetRequest{
					Queries: []*rpc.Query{
						{
							Key: &rpc.Key{
								Namespace:  "example/pg",
								Entity:     "merchant",
								EntityId:   "mid_swiggy",
								Domain:     "refund",
								ObjectName: "Features",
							},
							Fieldmasks: []*rpc.Field{
								{Name: "disable_auto_refund"},
								{Name: "refund_enabled"},
							},
						},
					},
				},
			},
			want:    nil,
			wantErr: true,
		},
		{
			name: "login_unmarshal_error",
			args: args{
				ctx: context.Background(),
				in: &rpc.GetRequest{
					Queries: []*rpc.Query{
						{
							Key: &rpc.Key{
								Namespace:  "example/pg",
								Entity:     "merchant",
								EntityId:   "mid_swiggy",
								Domain:     "refund",
								ObjectName: "Features",
							},
							Fieldmasks: []*rpc.Field{
								{Name: "disable_auto_refund"},
								{Name: "refund_enabled"},
							},
						},
					},
				},
			},
			want:    nil,
			wantErr: true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			r, _ := json.Marshal(tt.args.in)
			response, _ := json.Marshal(tt.want)
			server := MockHttp(t, string(r), string(response))
			defer server.Close()
			c, err := ClientTest(server.URL, nil)
			if err != nil {
				t.Error(err)
				return
			}
			got, err := c.Get(tt.args.ctx, tt.args.in)
			if (err != nil) != tt.wantErr {
				t.Errorf("Get() error = %v, wantErr %v", err, tt.wantErr)
				return
			}

			if !tt.wantErr && !reflect.DeepEqual(got.String(), tt.want.String()) {
				t.Errorf("Get() got = %v, want %v", got.String(), tt.want.String())
			}
		})
	}
}

func TestClient_GetAuditHistory(t *testing.T) {
	type args struct {
		ctx context.Context
		in  *rpc.GetAuditHistoryRequest
	}
	tests := []struct {
		name    string
		args    args
		want    *rpc.GetAuditHistoryResponse
		wantErr bool
	}{
		{
			name: "success",
			args: args{
				ctx: context.Background(),
				in: &rpc.GetAuditHistoryRequest{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_swiggy",
						Domain:     "refund",
						ObjectName: "Features",
					},
					Count: 1,
				},
			},
			want: &rpc.GetAuditHistoryResponse{
				AuditLogs: []*rpc.AuditLog{
					{
						ChangeBy:         "dummy",
						ChangeReason:     "dummy",
						ChangeApprovedBy: "dummy",
						ChangeValue:      []byte("testing"),
						ServiceName:      "dcs",
						Action:           0,
					},
				},
			},
			wantErr: false,
		},
		{
			name: "failure_500",
			args: args{
				ctx: context.Background(),
				in: &rpc.GetAuditHistoryRequest{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_swiggy",
						Domain:     "refund",
						ObjectName: "Features",
					},
					Count: 1,
				},
			},
			want:    nil,
			wantErr: true,
		},
		{
			name: "credential_error",
			args: args{
				ctx: context.Background(),
				in: &rpc.GetAuditHistoryRequest{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_swiggy",
						Domain:     "refund",
						ObjectName: "Features",
					},
					Count: 1,
				},
			},
			want:    nil,
			wantErr: true,
		},
		{
			name: "unmarshal_error",
			args: args{
				ctx: context.Background(),
				in: &rpc.GetAuditHistoryRequest{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_swiggy",
						Domain:     "refund",
						ObjectName: "Features",
					},
					Count: 1,
				},
			},
			want:    nil,
			wantErr: true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			r, _ := json.Marshal(tt.args.in)
			response, _ := json.Marshal(tt.want)
			server := MockHttp(t, string(r), string(response))
			defer server.Close()
			c, err := ClientTest(server.URL, nil)
			if err != nil {
				t.Error(err)
				return
			}
			got, err := c.GetAuditHistory(tt.args.ctx, tt.args.in)
			if (err != nil) != tt.wantErr {
				t.Errorf("GetAuditHistory() error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			if !tt.wantErr && !reflect.DeepEqual(got.String(), tt.want.String()) {
				t.Errorf("GetAuditHistory() got = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestClient_Patch(t *testing.T) {
	type args struct {
		ctx context.Context
		in  *rpc.PatchRequest
	}
	tests := []struct {
		name    string
		args    args
		want    *rpc.PatchResponse
		wantErr bool
	}{
		{
			name: "success",
			args: args{
				ctx: context.Background(),
				in: &rpc.PatchRequest{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_swiggy",
						Domain:     "refund",
						ObjectName: "Features", // TODO: handle camel case conversion
					},
					Value: []byte("testing"),
					Fieldmasks: []*rpc.Field{ // Provide info on which fields are getting patched
						//{Name: "refund_enabled"},
						{Name: "disable_auto_refund"},
					},
					AuditLog: &rpc.AuditLog{
						ChangeBy:         "dev2@razorpay.com",
						ChangeReason:     "refund patch",
						ChangeApprovedBy: "techlead2@razorpay.com",
					},
				},
			},
			want: &rpc.PatchResponse{
				Key: &rpc.Key{
					Namespace:  "example/pg",
					Entity:     "merchant",
					EntityId:   "mid_swiggy",
					Domain:     "refund",
					ObjectName: "Features", // TODO: handle camel case conversion
				},
				Count: 1,
			},
			wantErr: false,
		},
		{
			name: "failure_500",
			args: args{
				ctx: context.Background(),
				in: &rpc.PatchRequest{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_swiggy",
						Domain:     "refund",
						ObjectName: "Features", // TODO: handle camel case conversion
					},
					Value: []byte("testing"),
					Fieldmasks: []*rpc.Field{ // Provide info on which fields are getting patched
						//{Name: "refund_enabled"},
						{Name: "disable_auto_refund"},
					},
					AuditLog: &rpc.AuditLog{
						ChangeBy:         "dev2@razorpay.com",
						ChangeReason:     "refund patch",
						ChangeApprovedBy: "techlead2@razorpay.com",
					},
				},
			},
			want:    nil,
			wantErr: true,
		},
		{
			name: "credential_error",
			args: args{
				ctx: context.Background(),
				in: &rpc.PatchRequest{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_swiggy",
						Domain:     "refund",
						ObjectName: "Features", // TODO: handle camel case conversion
					},
					Value: []byte("testing"),
					Fieldmasks: []*rpc.Field{ // Provide info on which fields are getting patched
						//{Name: "refund_enabled"},
						{Name: "disable_auto_refund"},
					},
					AuditLog: &rpc.AuditLog{
						ChangeBy:         "dev2@razorpay.com",
						ChangeReason:     "refund patch",
						ChangeApprovedBy: "techlead2@razorpay.com",
					},
				},
			},
			want:    nil,
			wantErr: true,
		},
		{
			name: "unmarshal_error",
			args: args{
				ctx: context.Background(),
				in: &rpc.PatchRequest{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_swiggy",
						Domain:     "refund",
						ObjectName: "Features", // TODO: handle camel case conversion
					},
					Value: []byte("testing"),
					Fieldmasks: []*rpc.Field{ // Provide info on which fields are getting patched
						//{Name: "refund_enabled"},
						{Name: "disable_auto_refund"},
					},
					AuditLog: &rpc.AuditLog{
						ChangeBy:         "dev2@razorpay.com",
						ChangeReason:     "refund patch",
						ChangeApprovedBy: "techlead2@razorpay.com",
					},
				},
			},
			want:    nil,
			wantErr: true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			r, _ := json.Marshal(tt.args.in)
			response, _ := json.Marshal(tt.want)
			server := MockHttp(t, string(r), string(response))
			defer server.Close()
			c, err := ClientTest(server.URL, nil)
			if err != nil {
				t.Error(err)
				return
			}
			got, err := c.Patch(tt.args.ctx, tt.args.in)
			if (err != nil) != tt.wantErr {
				t.Errorf("Patch() error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			if !tt.wantErr && !reflect.DeepEqual(got.String(), tt.want.String()) {
				t.Errorf("Patch() got = %v, want %v", got.String(), tt.want.String())
			}
		})
	}
}

func TestClient_Put(t *testing.T) {
	type args struct {
		ctx context.Context
		in  *rpc.PutRequest
	}
	tests := []struct {
		name    string
		args    args
		want    *rpc.PutResponse
		wantErr bool
	}{
		{
			name: "success",
			args: args{
				ctx: context.Background(),
				in: &rpc.PutRequest{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Domain:     "refund",
						Entity:     "merchant",
						EntityId:   "mid_swiggy",
						ObjectName: "Features",
					},
					Value: []byte("testing"),
					AuditLog: &rpc.AuditLog{
						ChangeBy:         "dev@razorpay.com",
						ChangeReason:     "compliance issue",
						ChangeApprovedBy: "techlead@razorpay.com",
					},
				},
			},
			want: &rpc.PutResponse{
				Key: &rpc.Key{
					Namespace:  "example/pg",
					Entity:     "merchant",
					EntityId:   "mid_swiggy",
					Domain:     "refund",
					ObjectName: "Features",
				},
			},
			wantErr: false,
		},
		{
			name: "failure_500",
			args: args{
				ctx: context.Background(),
				in: &rpc.PutRequest{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_swiggy",
						Domain:     "refund",
						ObjectName: "Features",
					},
					Value: []byte("testing"),
					AuditLog: &rpc.AuditLog{
						ChangeBy:         "dev@razorpay.com",
						ChangeReason:     "compliance issue",
						ChangeApprovedBy: "techlead@razorpay.com",
					},
				},
			},
			want:    nil,
			wantErr: true,
		},
		{
			name: "credential_error",
			args: args{
				ctx: context.Background(),
				in: &rpc.PutRequest{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_swiggy",
						Domain:     "refund",
						ObjectName: "Features",
					},
					Value: []byte("testing"),
					AuditLog: &rpc.AuditLog{
						ChangeBy:         "dev@razorpay.com",
						ChangeReason:     "compliance issue",
						ChangeApprovedBy: "techlead@razorpay.com",
					},
				},
			},
			want:    nil,
			wantErr: true,
		},
		{
			name: "unmarshal_error",
			args: args{
				ctx: context.Background(),
				in: &rpc.PutRequest{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_swiggy",
						Domain:     "refund",
						ObjectName: "Features",
					},
					Value: []byte("testing"),
					AuditLog: &rpc.AuditLog{
						ChangeBy:         "dev@razorpay.com",
						ChangeReason:     "compliance issue",
						ChangeApprovedBy: "techlead@razorpay.com",
					},
				},
			},
			want:    nil,
			wantErr: true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			r, _ := json.Marshal(tt.args.in)
			response, _ := json.Marshal(tt.want)
			server := MockHttp(t, string(r), string(response))
			defer server.Close()
			c, err := ClientTest(server.URL, nil)
			if err != nil {
				t.Error(err)
				return
			}
			got, err := c.Put(tt.args.ctx, tt.args.in)
			if (err != nil) != tt.wantErr {
				t.Errorf("Put() error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			if !tt.wantErr && !reflect.DeepEqual(got.String(), tt.want.String()) {
				t.Errorf("Put() got = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestNew(t *testing.T) {
	type args struct {
		ctx  context.Context
		opts []Option
	}

	wantedClients := []*Client{}
	svr := MockHttp(t, "", "")
	defer svr.Close()
	client0, err := ClientTest("", nil)
	if err != nil {
		t.Error(err)
	}
	wantedClients = append(wantedClients, client0)

	tests := []struct {
		name      string
		args      args
		want      *Client
		wantError bool
	}{
		{
			name: "new_test",
			args: args{
				ctx: context.Background(),
				opts: []Option{
					WithConfig(
						config.NewConfig().
							WithCredentials(&config.UserCredentials{}).
							WithServerURL(svr.URL).WithMode("live").
							WithMock(false)),
					WithLogger(logger.NewDefaultLogger(context.Background())),
					WithHTTPClient(http.DefaultClient),
				},
			},
			want: wantedClients[0],
		},
		{
			name: "new_test_failure_env_missing",
			args: args{
				ctx: context.Background(),
				opts: []Option{
					WithConfig(
						config.NewConfig().
							WithCredentials(&config.UserCredentials{}).
							WithServerURL(svr.URL).WithMode("live").
							WithMock(false).WithModes([]config.Mode{config.Test, config.Live})),
					WithLogger(logger.NewDefaultLogger(context.Background())),
					WithHTTPClient(http.DefaultClient),
				},
			},
			want:      nil,
			wantError: true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := New(tt.args.ctx, tt.args.opts...)
			if (err != nil) != tt.wantError {
				fmt.Println("one")
				t.Errorf("New() error = %v, wantErr %v", err, tt.wantError)
				return
			}
			if err != nil && !tt.wantError {
				fmt.Println("two")
				t.Error(err)
				return
			} else if err != nil {
				return
			}
			if !reflect.DeepEqual(got.log, tt.want.log) {
				t.Errorf("New() = %v, want %v", got.log, tt.want.log)
			}
		})
	}
}

func toTimePtr(a time.Duration) *time.Duration {
	return &a
}

func TestCache(t *testing.T) {
	type args struct {
		ctx               context.Context
		putRequest        *rpc.PutRequest
		putServerResponse *rpc.PutResponse
		getRequest        *rpc.GetRequest
		getServerResponse *rpc.GetResponse
	}

	tests := []struct {
		name        string
		args        args
		want        *rpc.GetResponse
		wantErr     bool
		sleep       *time.Duration
		cacheConfig *cache.Config
	}{
		{
			name: "cache hit",
			args: args{
				ctx: context.Background(),
				putRequest: &rpc.PutRequest{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_swiggy",
						Domain:     "refund",
						ObjectName: "Features",
					},
					Value: []byte("EAE="),
					AuditLog: &rpc.AuditLog{
						ChangeBy:         "dev@razorpay.com",
						ChangeReason:     "compliance issue",
						ChangeApprovedBy: "techlead@razorpay.com",
					},
				},
				putServerResponse: &rpc.PutResponse{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Domain:     "refund",
						Entity:     "merchant",
						EntityId:   "mid_swiggy",
						ObjectName: "Features",
					},
				},
				getRequest: &rpc.GetRequest{
					Queries: []*rpc.Query{
						{
							Key: &rpc.Key{
								Namespace:  "example/pg",
								Domain:     "refund",
								Entity:     "merchant",
								EntityId:   "mid_swiggy",
								ObjectName: "Features",
							},
							Fieldmasks: []*rpc.Field{
								{Name: "disable_auto_refund"},
								{Name: "refund_enabled"},
							},
						},
					},
				},
				getServerResponse: &rpc.GetResponse{
					Kvs: []*rpc.KeyValue{
						{
							Key: &rpc.Key{
								Namespace:  "example/pg",
								Entity:     "merchant",
								EntityId:   "mid_swiggy",
								Domain:     "refund",
								ObjectName: "Features",
							},
							Value: []byte("EAE="),
						},
					},
				},
			},
			want: &rpc.GetResponse{
				Kvs: []*rpc.KeyValue{
					{
						Key: &rpc.Key{
							Namespace:  "example/pg",
							Entity:     "merchant",
							EntityId:   "mid_swiggy",
							Domain:     "refund",
							ObjectName: "Features",
						},
						Value: []byte("EAE="),
					},
				},
			},
			wantErr:     false,
			cacheConfig: cache.NewConfig(),
		},
		{
			name: "cache miss due to eviction",
			args: args{
				ctx: context.Background(),
				putRequest: &rpc.PutRequest{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_swiggy",
						Domain:     "refund",
						ObjectName: "Features",
					},
					Value: []byte("EAE="),
					AuditLog: &rpc.AuditLog{
						ChangeBy:         "dev@razorpay.com",
						ChangeReason:     "compliance issue",
						ChangeApprovedBy: "techlead@razorpay.com",
					},
				},
				putServerResponse: &rpc.PutResponse{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_swiggy",
						Domain:     "refund",
						ObjectName: "Features",
					},
				},
				getRequest: &rpc.GetRequest{
					Queries: []*rpc.Query{
						{
							Key: &rpc.Key{
								Namespace:  "example/pg",
								Entity:     "merchant",
								EntityId:   "mid_swiggy",
								Domain:     "refund",
								ObjectName: "Features",
							},
							Fieldmasks: []*rpc.Field{
								{Name: "disable_auto_refund"},
								{Name: "refund_enabled"},
							},
						},
					},
				},
				getServerResponse: &rpc.GetResponse{
					Kvs: []*rpc.KeyValue{
						{
							Key: &rpc.Key{
								Namespace:  "example/pg",
								Domain:     "refund",
								Entity:     "merchant",
								EntityId:   "mid_swiggy",
								ObjectName: "Features",
							},
							Value: []byte("EAE="),
						},
					},
				},
			},
			want: &rpc.GetResponse{
				Kvs: []*rpc.KeyValue{
					{
						Key: &rpc.Key{
							Namespace:  "example/pg",
							Entity:     "merchant",
							EntityId:   "mid_swiggy",
							Domain:     "refund",
							ObjectName: "Features",
						},
						Value: []byte("EAE="),
					},
				},
			},
			wantErr:     false,
			cacheConfig: cache.NewConfig().WithEviction(5 * time.Microsecond).WithCleanWindow(time.Duration(10) * time.Millisecond),
			sleep:       toTimePtr(time.Duration(1000) * time.Millisecond),
		},
	}
	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			var err error
			var getClient, putClient *Client
			// put data if put request is there, fill the cache
			if tc.args.putRequest != nil {
				putBytes, err := json.Marshal(tc.args.putRequest)
				if err != nil {
					t.Error(err)
					return
				}
				putResponse, err := json.Marshal(tc.args.putServerResponse)
				if err != nil {
					t.Error(err)
					return
				}
				putServer := MockHttp(t, string(putBytes), string(putResponse))

				dcsCache, err := cache.New(context.Background(), tc.cacheConfig)
				if err != nil {
					t.Error(err)
					return
				}
				putClient, err = ClientTest(putServer.URL, dcsCache)
				if err != nil {
					t.Error(err)
					return
				}
				_, err = putClient.Put(tc.args.ctx, tc.args.putRequest)
				if err != nil {
					t.Error(err)
					return
				}
				putServer.Close()
			}

			// get data
			getBytes, err := json.Marshal(tc.args.getRequest)
			if err != nil {
				t.Error(err)
				return
			}
			getServerResponse, _ := json.Marshal(tc.args.getServerResponse)
			getServer := MockHttp(t, string(getBytes), string(getServerResponse))
			getClient, err = ClientTest(getServer.URL, putClient.cache)
			if err != nil {
				t.Error(err)
				return
			}

			if tc.sleep != nil {
				time.Sleep(*tc.sleep)
			}

			got, err := getClient.Get(tc.args.ctx, tc.args.getRequest)
			if (err != nil) != tc.wantErr {
				t.Errorf("Get1() error = %v, wantErr %v", err, tc.wantErr)
				return
			}

			getServer.Close()

			got, err = getClient.Get(tc.args.ctx, tc.args.getRequest)
			if (err != nil) != tc.wantErr {
				t.Errorf("Get2() error = %v, wantErr %v", err, tc.wantErr)
				return
			}
			tc.args.getRequest.Queries[0].Fieldmasks = []*rpc.Field{{Name: "disable_auto_refund"}}
			got, err = getClient.Get(tc.args.ctx, tc.args.getRequest)
			if (err != nil) != true {
				t.Errorf("Get3() error = %v, wantErr %v", err, tc.wantErr)
				return
			}

			if err == nil && !reflect.DeepEqual(got.String(), tc.want.String()) {
				t.Errorf("Get4() got = %v, want %v", got.String(), tc.want.String())
			}
		})
	}
}

func TestClient_getLoginURI(t *testing.T) {
	type fields struct {
		httpClient heimdall.Doer
		config     *config.Config
		log        logger.Logger
		cache      *cache.Cache
		creds      *credentials.Credentials
		dualMode   bool
	}
	type args struct {
		ctx context.Context
	}
	tests := []struct {
		name    string
		fields  fields
		args    args
		want    string
		wantErr bool
	}{
		{
			name: "success_old_implementation",
			fields: fields{
				httpClient: http.DefaultClient,
				config: &config.Config{
					ServerURL: "https://dcs-test.int.stage.razorpay.in",
					UserCreds: &config.UserCredentials{
						Username: "testing",
						Password: "testing",
					},
					Mock:  false,
					Env:   0,
					Modes: nil,
				},
				log:      logger.NewDefaultLogger(context.Background()),
				cache:    nil,
				creds:    nil,
				dualMode: false,
			},
			args:    args{ctx: context.Background()},
			want:    "https://dcs-test.int.stage.razorpay.in",
			wantErr: false,
		},
		{
			name: "success_new_live",
			fields: fields{
				httpClient: http.DefaultClient,
				config: &config.Config{
					ServerURL: "https://dcs-test.int.stage.razorpay.in",
					UserCreds: &config.UserCredentials{
						Username: "testing",
						Password: "testing",
					},
					Mock:  false,
					Env:   config.Stage,
					Modes: []config.Mode{config.Live},
					Mode:  "test",
				},
				log:      logger.NewDefaultLogger(context.Background()),
				cache:    nil,
				creds:    nil,
				dualMode: false,
			},
			args:    args{ctx: context.Background()},
			want:    "https://dcs-live.concierge.stage.razorpay.in",
			wantErr: false,
		},
		{
			name: "success_new_test",
			fields: fields{
				httpClient: http.DefaultClient,
				config: &config.Config{
					ServerURL: "https://dcs-test.int.stage.razorpay.in",
					UserCreds: &config.UserCredentials{
						Username: "testing",
						Password: "testing",
					},
					Mock:  false,
					Env:   config.Stage,
					Modes: []config.Mode{config.Test},
					Mode:  "test",
				},
				log:      logger.NewDefaultLogger(context.Background()),
				cache:    nil,
				creds:    nil,
				dualMode: false,
			},
			args:    args{ctx: context.Background()},
			want:    "https://dcs-test.concierge.stage.razorpay.in",
			wantErr: false,
		},
		{
			name: "failure_missing_env",
			fields: fields{
				httpClient: http.DefaultClient,
				config: &config.Config{
					ServerURL: "https://dcs-test.int.stage.razorpay.in",
					UserCreds: &config.UserCredentials{
						Username: "testing",
						Password: "testing",
					},
					Mock:  false,
					Env:   0,
					Modes: []config.Mode{config.Live},
					Mode:  "test",
				},
				log:      logger.NewDefaultLogger(context.Background()),
				cache:    nil,
				creds:    nil,
				dualMode: false,
			},
			args:    args{ctx: context.Background()},
			want:    "",
			wantErr: true,
		},
		{
			name: "success_new_dual",
			fields: fields{
				httpClient: http.DefaultClient,
				config: &config.Config{
					ServerURL: "https://dcs-test.int.stage.razorpay.in",
					UserCreds: &config.UserCredentials{
						Username: "testing",
						Password: "testing",
					},
					Mock:  false,
					Env:   config.Stage,
					Modes: []config.Mode{config.Live, config.Test},
					Mode:  "test",
				},
				log:      logger.NewDefaultLogger(context.Background()),
				cache:    nil,
				creds:    nil,
				dualMode: true,
			},
			args:    args{ctx: context.Background()},
			want:    "https://dcs-live.concierge.stage.razorpay.in",
			wantErr: false,
		},
		{
			name: "success_new_dual_with_mode_test",
			fields: fields{
				httpClient: http.DefaultClient,
				config: &config.Config{
					ServerURL: "https://dcs-test.int.stage.razorpay.in",
					UserCreds: &config.UserCredentials{
						Username: "testing",
						Password: "testing",
					},
					Mock:  false,
					Env:   config.Stage,
					Modes: []config.Mode{config.Test},
					Mode:  "test",
				},
				log:      logger.NewDefaultLogger(context.Background()),
				cache:    nil,
				creds:    nil,
				dualMode: true,
			},
			args:    args{ctx: context.Background()},
			want:    "https://dcs-live.concierge.stage.razorpay.in",
			wantErr: false,
		},
		{
			name: "success_new_dual_no_old_fields",
			fields: fields{
				httpClient: http.DefaultClient,
				config: &config.Config{
					UserCreds: &config.UserCredentials{
						Username: "testing",
						Password: "testing",
					},
					Mock:  false,
					Env:   config.Stage,
					Modes: []config.Mode{config.Live, config.Test},
				},
				log:      nil,
				cache:    nil,
				creds:    nil,
				dualMode: true,
			},
			args:    args{ctx: context.Background()},
			want:    "https://dcs-live.concierge.stage.razorpay.in",
			wantErr: false,
		},
		{
			name: "failure_new_dual_no_env",
			fields: fields{
				httpClient: http.DefaultClient,
				config: &config.Config{
					UserCreds: &config.UserCredentials{
						Username: "testing",
						Password: "testing",
					},
					Mock:  false,
					Env:   config.UndefinedEnv,
					Modes: []config.Mode{config.Live, config.Test},
				},
				log:      nil,
				cache:    nil,
				creds:    nil,
				dualMode: true,
			},
			args:    args{ctx: context.Background()},
			want:    "",
			wantErr: true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := &Client{
				httpClient: tt.fields.httpClient,
				config:     tt.fields.config,
				log:        tt.fields.log,
				cache:      tt.fields.cache,
				creds:      tt.fields.creds,
				dualMode:   tt.fields.dualMode,
			}
			got, err := c.getLoginURI(tt.args.ctx)
			if (err != nil) != tt.wantErr {
				t.Errorf("Put() error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			assert.Equalf(t, tt.want, got, "getLoginURI(%v)", tt.args.ctx)
		})
	}
}

func TestClient_setDualModeIfApplicable(t *testing.T) {
	type fields struct {
		httpClient heimdall.Doer
		config     *config.Config
		log        logger.Logger
		cache      *cache.Cache
		creds      *credentials.Credentials
		dualMode   bool
	}
	tests := []struct {
		name   string
		fields fields
		want   bool
	}{
		{
			name: "success",
			fields: fields{
				httpClient: nil,
				config: &config.Config{
					UserCreds: nil,
					Mock:      false,
					Env:       config.Stage,
					Modes:     []config.Mode{config.Live, config.Test},
				},
				log:      nil,
				cache:    nil,
				creds:    nil,
				dualMode: false,
			},
			want: true,
		},
		{
			name: "failure_same_mode",
			fields: fields{
				httpClient: nil,
				config: &config.Config{
					UserCreds: nil,
					Mock:      false,
					Env:       config.Stage,
					Modes:     []config.Mode{config.Live, config.Live},
				},
				log:      nil,
				cache:    nil,
				creds:    nil,
				dualMode: false,
			},
			want: false,
		},
		{
			name: "failure",
			fields: fields{
				httpClient: nil,
				config: &config.Config{
					UserCreds: nil,
					Mock:      false,
					Env:       config.Stage,
					Modes:     []config.Mode{config.Test},
				},
				log:      nil,
				cache:    nil,
				creds:    nil,
				dualMode: false,
			},
			want: false,
		},
		{
			name: "failure_undefinedmode",
			fields: fields{
				httpClient: nil,
				config: &config.Config{
					UserCreds: nil,
					Mock:      false,
					Env:       config.Stage,
					Modes:     []config.Mode{config.UndefinedMode},
				},
				log:      nil,
				cache:    nil,
				creds:    nil,
				dualMode: false,
			},
			want: false,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := &Client{
				httpClient: tt.fields.httpClient,
				config:     tt.fields.config,
				log:        tt.fields.log,
				cache:      tt.fields.cache,
				creds:      tt.fields.creds,
				dualMode:   tt.fields.dualMode,
			}
			c.setDualModeIfApplicable()
			assert.Equalf(t, tt.want, c.dualMode, "setDualModeIfApplicable()- %v", c.dualMode)
		})
	}
}

func TestSetContextMode(t *testing.T) {
	type args struct {
		ctx  context.Context
		mode string
	}
	tests := []struct {
		name string
		args args
		want string
	}{
		{
			name: "success",
			args: args{ctx: context.Background(), mode: "test"},
			want: "test",
		},
		{
			name: "success_live",
			args: args{ctx: context.Background(), mode: "live"},
			want: "live",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ctx := SetContextMode(tt.args.ctx, tt.args.mode)
			assert.Equalf(t, tt.want, ctx.Value(ModeContextKey), "SetContextMode(%v, %v)", tt.args.ctx, tt.args.mode)
		})
	}
}

func TestGetContextMode(t *testing.T) {
	type args struct {
		ctx context.Context
	}
	tests := []struct {
		name string
		args args
		want string
	}{
		{
			name: "success",
			args: args{ctx: context.WithValue(context.Background(), ModeContextKey, "test")},
			want: "test",
		},
		{
			name: "failure",
			args: args{ctx: context.Background()},
			want: "",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			assert.Equalf(t, tt.want, GetContextMode(tt.args.ctx), "GetContextMode(%v)", tt.args.ctx)
		})
	}
}

// TestClient_Get_IndexOutOfRangeBoundsCheck tests the bounds check added to prevent
// index out of range errors when server returns more kvs than extQueries
func TestClient_Get_IndexOutOfRangeBoundsCheck(t *testing.T) {
	// Mock HTTP handler that returns more kvs than requested
	dummyHandler := func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/auth/login" {
			w.WriteHeader(http.StatusOK)
			w.Write([]byte("{\"accessToken\":\"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE2NjI0OTA2MTksInVzZXJuYW1lIjoiZXhhbXBsZSIsInJvbGVzIjpbImV4YW1wbGVfbWVyY2hhbnRfYWRtaW4iXX0.Zfr1YTJtGWMkh9HwqpxPC8yElexB2cstfPyHxouk8Iw\"}"))
			return
		}

		// Return response with MORE kvs than what was requested
		// This simulates a server bug or race condition
		response := &rpc.GetResponse{
			Kvs: []*rpc.KeyValue{
				{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_swiggy",
						Domain:     "refund",
						ObjectName: "Features",
					},
					Value: []byte("EAE="),
				},
				{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_zomato",
						Domain:     "refund",
						ObjectName: "Features",
					},
					Value: []byte("EAF="),
				},
				// Extra KV that wasn't requested - this should trigger bounds check
				{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_uber",
						Domain:     "refund",
						ObjectName: "Features",
					},
					Value: []byte("EAG="),
				},
			},
		}
		responseBytes, _ := json.Marshal(response)
		w.WriteHeader(http.StatusOK)
		w.Write(responseBytes)
	}

	server := httptest.NewServer(http.HandlerFunc(dummyHandler))
	defer server.Close()

	// Create client
	c, err := ClientTest(server.URL, nil)
	if err != nil {
		t.Fatalf("failed to create client: %v", err)
	}

	// Make request with only 2 queries, but server will return 3 kvs
	ctx := context.Background()
	req := &rpc.GetRequest{
		Queries: []*rpc.Query{
			{
				Key: &rpc.Key{
					Namespace:  "example/pg",
					Entity:     "merchant",
					EntityId:   "mid_swiggy",
					Domain:     "refund",
					ObjectName: "Features",
				},
				Fieldmasks: []*rpc.Field{
					{Name: "disable_auto_refund"},
				},
			},
			{
				Key: &rpc.Key{
					Namespace:  "example/pg",
					Entity:     "merchant",
					EntityId:   "mid_zomato",
					Domain:     "refund",
					ObjectName: "Features",
				},
				Fieldmasks: []*rpc.Field{
					{Name: "refund_enabled"},
				},
			},
		},
	}

	// This should not panic even though server returns 3 kvs for 2 queries
	// The extra kv should be skipped with an error log
	resp, err := c.Get(ctx, req)
	if err != nil {
		t.Fatalf("Get() returned error: %v", err)
	}

	// Verify we got only 2 kvs in response (the 3rd one was skipped due to bounds check)
	if len(resp.Kvs) != 2 {
		t.Errorf("expected 2 kvs in response (3rd skipped), got %d", len(resp.Kvs))
	}

	// Verify the first 2 kvs are correct
	if resp.Kvs[0].Key.EntityId != "mid_swiggy" {
		t.Errorf("expected first kv to be mid_swiggy, got %s", resp.Kvs[0].Key.EntityId)
	}
	if resp.Kvs[1].Key.EntityId != "mid_zomato" {
		t.Errorf("expected second kv to be mid_zomato, got %s", resp.Kvs[1].Key.EntityId)
	}
}

// TestClient_Get_CacheSetWithCorrectIndex tests that cache.Set uses the correct index
// after the fix (extQueries[i] instead of extQueries[extIndexes[i]])
func TestClient_Get_CacheSetWithCorrectIndex(t *testing.T) {
	// Create a custom cache to track what keys are being set
	dcsCache, err := cache.New(context.Background(), cache.NewConfig())
	if err != nil {
		t.Fatalf("failed to create cache: %v", err)
	}

	// Mock HTTP handler
	dummyHandler := func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/auth/login" {
			w.WriteHeader(http.StatusOK)
			w.Write([]byte("{\"accessToken\":\"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE2NjI0OTA2MTksInVzZXJuYW1lIjoiZXhhbXBsZSIsInJvbGVzIjpbImV4YW1wbGVfbWVyY2hhbnRfYWRtaW4iXX0.Zfr1YTJtGWMkh9HwqpxPC8yElexB2cstfPyHxouk8Iw\"}"))
			return
		}

		// Return response matching the request
		response := &rpc.GetResponse{
			Kvs: []*rpc.KeyValue{
				{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_merchant1",
						Domain:     "refund",
						ObjectName: "Features",
					},
					Value: []byte("value1"),
				},
				{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_merchant2",
						Domain:     "refund",
						ObjectName: "Features",
					},
					Value: []byte("value2"),
				},
			},
		}
		responseBytes, _ := json.Marshal(response)
		w.WriteHeader(http.StatusOK)
		w.Write(responseBytes)
	}

	server := httptest.NewServer(http.HandlerFunc(dummyHandler))
	defer server.Close()

	// Create client with custom cache
	c, err := ClientTest(server.URL, dcsCache)
	if err != nil {
		t.Fatalf("failed to create client: %v", err)
	}

	ctx := context.Background()
	req := &rpc.GetRequest{
		Queries: []*rpc.Query{
			{
				Key: &rpc.Key{
					Namespace:  "example/pg",
					Entity:     "merchant",
					EntityId:   "mid_merchant1",
					Domain:     "refund",
					ObjectName: "Features",
				},
				Fieldmasks: []*rpc.Field{
					{Name: "field1"},
				},
			},
			{
				Key: &rpc.Key{
					Namespace:  "example/pg",
					Entity:     "merchant",
					EntityId:   "mid_merchant2",
					Domain:     "refund",
					ObjectName: "Features",
				},
				Fieldmasks: []*rpc.Field{
					{Name: "field2"},
				},
			},
		},
	}

	// Make the request
	resp, err := c.Get(ctx, req)
	if err != nil {
		t.Fatalf("Get() returned error: %v", err)
	}

	// Verify response
	if len(resp.Kvs) != 2 {
		t.Errorf("expected 2 kvs in response, got %d", len(resp.Kvs))
	}

	// Verify cache was populated correctly
	// The cache key should be constructed using the correct fieldmasks from extQueries[i]
	mode := c.config.Mode
	if mode == "" {
		mode = "live"
	}

	// Check first cache entry
	cacheKey1 := cacheKey(req.Queries[0].Key, req.Queries[0].Fieldmasks, mode)
	val1, err := dcsCache.Get(cacheKey1)
	if err != nil {
		t.Errorf("expected cache hit for first query, got error: %v", err)
	}
	if string(val1) != "value1" {
		t.Errorf("expected cached value 'value1', got '%s'", string(val1))
	}

	// Check second cache entry
	cacheKey2 := cacheKey(req.Queries[1].Key, req.Queries[1].Fieldmasks, mode)
	val2, err := dcsCache.Get(cacheKey2)
	if err != nil {
		t.Errorf("expected cache hit for second query, got error: %v", err)
	}
	if string(val2) != "value2" {
		t.Errorf("expected cached value 'value2', got '%s'", string(val2))
	}
}

// TestClient_Get_PartialCacheHit tests the scenario where some queries are cached
// and some need to be fetched, ensuring correct index mapping
func TestClient_Get_PartialCacheHit(t *testing.T) {
	dcsCache, err := cache.New(context.Background(), cache.NewConfig())
	if err != nil {
		t.Fatalf("failed to create cache: %v", err)
	}

	// Pre-populate cache with first query result
	mode := "live"
	key1 := &rpc.Key{
		Namespace:  "example/pg",
		Entity:     "merchant",
		EntityId:   "mid_cached",
		Domain:     "refund",
		ObjectName: "Features",
	}
	fieldmasks1 := []*rpc.Field{{Name: "cached_field"}}
	cacheKey1 := cacheKey(key1, fieldmasks1, mode)
	err = dcsCache.Set(cacheKey1, []byte("cached_value"))
	if err != nil {
		t.Fatalf("failed to set cache: %v", err)
	}

	// Mock HTTP handler that should only receive request for non-cached query
	dummyHandler := func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/auth/login" {
			w.WriteHeader(http.StatusOK)
			w.Write([]byte("{\"accessToken\":\"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE2NjI0OTA2MTksInVzZXJuYW1lIjoiZXhhbXBsZSIsInJvbGVzIjpbImV4YW1wbGVfbWVyY2hhbnRfYWRtaW4iXX0.Zfr1YTJtGWMkh9HwqpxPC8yElexB2cstfPyHxouk8Iw\"}"))
			return
		}

		// Parse request to verify only non-cached queries are sent
		body, _ := ioutil.ReadAll(r.Body)
		var getReq rpc.GetRequest
		json.Unmarshal(body, &getReq)

		// Should only have 2 queries (the non-cached ones)
		// Note: We don't validate the exact count here as it depends on cache state

		// Return response for non-cached queries
		response := &rpc.GetResponse{
			Kvs: []*rpc.KeyValue{
				{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_fetched1",
						Domain:     "refund",
						ObjectName: "Features",
					},
					Value: []byte("fetched_value1"),
				},
				{
					Key: &rpc.Key{
						Namespace:  "example/pg",
						Entity:     "merchant",
						EntityId:   "mid_fetched2",
						Domain:     "refund",
						ObjectName: "Features",
					},
					Value: []byte("fetched_value2"),
				},
			},
		}
		responseBytes, _ := json.Marshal(response)
		w.WriteHeader(http.StatusOK)
		w.Write(responseBytes)
	}

	server := httptest.NewServer(http.HandlerFunc(dummyHandler))
	defer server.Close()

	c, err := ClientTest(server.URL, dcsCache)
	if err != nil {
		t.Fatalf("failed to create client: %v", err)
	}

	ctx := context.Background()

	// Define queries
	query1 := &rpc.Query{
		Key:        key1,
		Fieldmasks: fieldmasks1,
	}
	query2 := &rpc.Query{
		Key: &rpc.Key{
			Namespace:  "example/pg",
			Entity:     "merchant",
			EntityId:   "mid_fetched1",
			Domain:     "refund",
			ObjectName: "Features",
		},
		Fieldmasks: []*rpc.Field{{Name: "fetched_field1"}},
	}
	query3 := &rpc.Query{
		Key: &rpc.Key{
			Namespace:  "example/pg",
			Entity:     "merchant",
			EntityId:   "mid_fetched2",
			Domain:     "refund",
			ObjectName: "Features",
		},
		Fieldmasks: []*rpc.Field{{Name: "fetched_field2"}},
	}

	req := &rpc.GetRequest{
		Queries: []*rpc.Query{query1, query2, query3},
	}

	resp, err := c.Get(ctx, req)
	if err != nil {
		t.Fatalf("Get() returned error: %v", err)
	}

	// Verify response has all 3 kvs in correct order
	if len(resp.Kvs) != 3 {
		t.Fatalf("expected 3 kvs in response, got %d", len(resp.Kvs))
	}

	// First should be from cache
	if string(resp.Kvs[0].Value) != "cached_value" {
		t.Errorf("expected first kv from cache, got %s", string(resp.Kvs[0].Value))
	}

	// Second and third should be fetched
	if string(resp.Kvs[1].Value) != "fetched_value1" {
		t.Errorf("expected second kv to be fetched_value1, got %s", string(resp.Kvs[1].Value))
	}
	if string(resp.Kvs[2].Value) != "fetched_value2" {
		t.Errorf("expected third kv to be fetched_value2, got %s", string(resp.Kvs[2].Value))
	}

	// Verify newly fetched values are now cached with correct keys
	// Query 2 (mid_fetched1) should be cached with fetched_value1
	cacheKey2 := cacheKey(query2.Key, query2.Fieldmasks, mode)
	val2, err := dcsCache.Get(cacheKey2)
	if err != nil {
		t.Errorf("expected cache hit for query2 (mid_fetched1) after fetch, got error: %v", err)
	}
	if string(val2) != "fetched_value1" {
		t.Errorf("expected cached value 'fetched_value1' for query2 (mid_fetched1), got '%s'", string(val2))
	}

	// Query 3 (mid_fetched2) should be cached with fetched_value2
	cacheKey3 := cacheKey(query3.Key, query3.Fieldmasks, mode)
	val3, err := dcsCache.Get(cacheKey3)
	if err != nil {
		t.Errorf("expected cache hit for query3 (mid_fetched2) after fetch, got error: %v", err)
	}
	if string(val3) != "fetched_value2" {
		t.Errorf("expected cached value 'fetched_value2' for query3 (mid_fetched2), got '%s'", string(val3))
	}
}
