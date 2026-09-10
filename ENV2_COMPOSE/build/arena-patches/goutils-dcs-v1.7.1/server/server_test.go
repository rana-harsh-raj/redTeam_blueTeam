package server

import (
	"context"
	"encoding/json"
	"reflect"
	"testing"

	contracts "github.com/razorpay/goutils/dcs/server/contracts/rpc/client/dcs/v1"
	dcs "github.com/razorpay/goutils/dcs/server/examples/pkg/dcs/dual_mode"
	"github.com/razorpay/goutils/dcs/server/mock"
)

func TestNewServer(t *testing.T) {
	clt := &mock.Client{
		Client:         nil,
		ReturnFailure:  false,
		ReturnDisabled: false,
	}

	svc := NewDcs(clt, dcs.Marshal)

	type args struct {
		svc *Service
	}
	tests := []struct {
		name string
		args args
		want *Server
	}{
		{
			name: "success",
			args: args{svc: svc},
			want: &Server{
				service: svc,
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := NewServer(tt.args.svc); !reflect.DeepEqual(got, tt.want) {
				t.Errorf("NewServer() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestServer_Set(t *testing.T) {
	b, _ := json.Marshal(map[string]bool{"refund_enabled": true})

	type mockValues struct {
		ReturnFailure  bool
		ReturnDisabled bool
	}

	type fields struct {
		DcsAPIServer contracts.DcsAPIServer
		service      *Service
	}
	type args struct {
		ctx context.Context
		req *contracts.SetRequest
	}
	tests := []struct {
		name    string
		fields  fields
		args    args
		want    *contracts.SetResponse
		mock    mockValues
		wantErr bool
	}{
		{
			name:   "success",
			fields: fields{},
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
				ReturnFailure: false,
			},
			want: &contracts.SetResponse{
				Key:     "example/pg/merchant/refund/Features",
				Success: true,
				Error:   nil,
			},
			wantErr: false,
		},
		{
			name:   "failure",
			fields: fields{},
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
				ReturnFailure: true,
			},
			want: &contracts.SetResponse{
				Key:     "example/pg/merchant/refund/Features",
				Success: false,
				Error: &contracts.Error{
					Code:        "INTERNAL_SERVER_ERROR",
					Description: "return error from DCS server",
				},
			},
			wantErr: true,
		},
		{
			name:   "failure_request_nil",
			fields: fields{},
			args: args{
				ctx: context.Background(),
				req: nil,
			},
			mock: mockValues{
				ReturnFailure: true,
			},
			want: &contracts.SetResponse{
				Success: false,
				Error: &contracts.Error{
					Code:        "INVALID_REQUEST",
					Description: "set request cannot be nil",
				},
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

			svc := NewDcs(clt, dcs.Marshal)
			tt.fields.service = svc

			s := &Server{
				DcsAPIServer: tt.fields.DcsAPIServer,
				service:      tt.fields.service,
			}

			got, err := s.Set(tt.args.ctx, tt.args.req)
			if (err != nil) != tt.wantErr {
				t.Errorf("Set() error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			if !reflect.DeepEqual(got, tt.want) {
				t.Errorf("Set() got = %v, want %v", got, tt.want)
			}
		})
	}
}
