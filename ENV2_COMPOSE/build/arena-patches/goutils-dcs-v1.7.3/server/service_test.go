package server

import (
	"context"
	"encoding/json"
	"github.com/stretchr/testify/assert"
	"reflect"
	"testing"

	"github.com/razorpay/goutils/dcs"
	contracts "github.com/razorpay/goutils/dcs/server/contracts/rpc/client/dcs/v1"
	featuresexample "github.com/razorpay/goutils/dcs/server/examples/dcs/features"
	dcsexample "github.com/razorpay/goutils/dcs/server/examples/pkg/dcs/dual_mode"
	"github.com/razorpay/goutils/dcs/server/features"
	"github.com/razorpay/goutils/dcs/server/mock"
)

func TestNewDcs(t *testing.T) {
	type args struct {
		client dcs.ConfigClient
		fn     MarshalFn
	}
	clt := &mock.Client{
		Client:         nil,
		ReturnFailure:  false,
		ReturnDisabled: false,
	}
	fn := dcsexample.Marshal
	tests := []struct {
		name string
		args args
	}{
		{
			name: "success",
			args: args{
				client: clt,
				fn:     fn,
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := NewDcs(tt.args.client, tt.args.fn); !reflect.DeepEqual(got.client, clt) {
				t.Errorf("NewDcs() = %v, want %v", got.client, clt)
			}
		})
	}
}

func TestService_EnabledFeatures(t *testing.T) {
	type fields struct {
		client dcs.ConfigClient
		fn     MarshalFn
	}
	type args struct {
		ctx      context.Context
		in       features.Features
		flags    []string
		entityID string
	}
	type mockValues struct {
		ReturnFailure  bool
		ReturnDisabled bool
	}
	tests := []struct {
		name    string
		fields  fields
		args    args
		mock    mockValues
		want    []string
		wantErr bool
	}{
		{
			name: "success",
			fields: fields{
				fn: dcsexample.Marshal,
			},
			args: args{
				ctx: context.Background(),
				in:  featuresexample.New(),
				flags: []string{
					featuresexample.RefundEnabled.String(),
					featuresexample.EligibilityEnabled.String(),
					featuresexample.DisableAutoRefund.String(),
				},
				entityID: "mid_swiggy",
			},
			mock: mockValues{
				ReturnFailure:  false,
				ReturnDisabled: false,
			},
			want:    []string{featuresexample.RefundEnabled.String(), featuresexample.EligibilityEnabled.String()},
			wantErr: false,
		},
		{
			name: "failure",
			fields: fields{
				fn: dcsexample.Marshal,
			},
			args: args{
				ctx: context.Background(),
				in:  featuresexample.New(),
				flags: []string{
					featuresexample.RefundEnabled.String(),
					featuresexample.EligibilityEnabled.String(),
					featuresexample.DisableAutoRefund.String(),
				},
				entityID: "mid_swiggy",
			},
			mock: mockValues{
				ReturnFailure:  true,
				ReturnDisabled: false,
			},
			want:    nil,
			wantErr: true,
		},
		{
			name: "failure_all_features_disabled",
			fields: fields{
				fn: dcsexample.Marshal,
			},
			args: args{
				ctx: context.Background(),
				in:  featuresexample.New(),
				flags: []string{
					featuresexample.RefundEnabled.String(),
					featuresexample.EligibilityEnabled.String(),
					featuresexample.DisableAutoRefund.String(),
				},
				entityID: "mid_swiggy",
			},
			mock: mockValues{
				ReturnFailure:  false,
				ReturnDisabled: true,
			},
			want:    nil,
			wantErr: false,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			clt := &mock.Client{
				Client:         nil,
				ReturnFailure:  tt.mock.ReturnFailure,
				ReturnDisabled: tt.mock.ReturnDisabled,
			}

			c := &Service{
				client: clt,
				fn:     tt.fields.fn,
			}
			got, err := c.EnabledFeatures(tt.args.ctx, tt.args.in, tt.args.flags, tt.args.entityID, "live")
			if (err != nil) != tt.wantErr {
				t.Errorf("EnabledFeatures() error = %v, wantErr %v", err, tt.wantErr)
				return
			}

			if !assert.ElementsMatch(t, got, tt.want) {
				t.Errorf("EnabledFeatures() got = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestService_Set(t *testing.T) {
	b, _ := json.Marshal(map[string]bool{"refund_enabled": true})
	type mockValues struct {
		ReturnFailure  bool
		ReturnDisabled bool
	}
	type fields struct {
		client dcs.ConfigClient
		fn     MarshalFn
	}
	type args struct {
		ctx context.Context
		req *contracts.SetRequest
	}
	tests := []struct {
		name    string
		fields  fields
		args    args
		mock    mockValues
		wantErr bool
	}{
		{
			name: "success",
			fields: fields{
				fn: dcsexample.Marshal,
			},
			args: args{
				ctx: context.Background(),
				req: &contracts.SetRequest{
					Key:      "example/pg/merchant/refund/Features",
					EntityId: "mid_swiggy",
					Value:    string(b),
					LiveMode: false,
					AuditLog: &contracts.AuditLog{
						ChangeBy:         "kamepelly.pranith@razorpay.com",
						ChangeReason:     "Testing the Swiggy",
						ChangeApprovedBy: "alok.s@razorpay.com",
					},
				},
			},
			mock: mockValues{
				ReturnFailure:  false,
				ReturnDisabled: false,
			},
			wantErr: false,
		},
		{
			name: "failure_key_error",
			fields: fields{
				fn: dcsexample.Marshal,
			},
			args: args{
				ctx: context.Background(),
				req: &contracts.SetRequest{
					Key:      "example/pg/merchant/Features",
					EntityId: "mid_swiggy",
					Value:    string(b),
					LiveMode: false,
					AuditLog: &contracts.AuditLog{
						ChangeBy:         "kamepelly.pranith@razorpay.com",
						ChangeReason:     "Testing the Swiggy",
						ChangeApprovedBy: "alok.s@razorpay.com",
					},
				},
			},
			mock: mockValues{
				ReturnFailure:  false,
				ReturnDisabled: false,
			},
			wantErr: true,
		},
		{
			name: "failure_request_nil",
			fields: fields{
				fn: dcsexample.Marshal,
			},
			args: args{
				ctx: context.Background(),
				req: nil,
			},
			mock: mockValues{
				ReturnFailure:  false,
				ReturnDisabled: false,
			},
			wantErr: true,
		},
		{
			name: "failure_server_error",
			fields: fields{
				fn: dcsexample.Marshal,
			},
			args: args{
				ctx: context.Background(),
				req: &contracts.SetRequest{
					Key:      "example/pg/merchant/refund/Features",
					EntityId: "mid_swiggy",
					Value:    string(b),
					LiveMode: false,
					AuditLog: &contracts.AuditLog{
						ChangeBy:         "kamepelly.pranith@razorpay.com",
						ChangeReason:     "Testing the Swiggy",
						ChangeApprovedBy: "alok.s@razorpay.com",
					},
				},
			},
			mock: mockValues{
				ReturnFailure:  true,
				ReturnDisabled: false,
			},
			wantErr: true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			clt := &mock.Client{
				Client:         nil,
				ReturnFailure:  tt.mock.ReturnFailure,
				ReturnDisabled: tt.mock.ReturnDisabled,
			}

			c := &Service{
				client: clt,
				fn:     tt.fields.fn,
			}
			if err := c.Set(tt.args.ctx, tt.args.req); (err != nil) != tt.wantErr {
				t.Errorf("Set() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}
