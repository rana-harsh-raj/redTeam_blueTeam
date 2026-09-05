package dcs

import (
	"testing"

	rpc "github.com/razorpay/goutils/dcs/rpc/dcs/kv/v1"
	"github.com/stretchr/testify/assert"
)

func TestFlattenedKeyWithOutID(t *testing.T) {
	type args struct {
		key *rpc.Key
	}
	tests := []struct {
		name string
		args args
		want string
	}{
		{
			name: "success",
			args: args{key: &rpc.Key{
				Namespace:  "example/pg",
				Entity:     "merchant",
				EntityId:   "mid_swiggy",
				Domain:     "refund",
				ObjectName: "Features",
			}},
			want: "example/pg/merchant/refund/Features",
		},
		{
			name: "success",
			args: args{key: nil},
			want: "",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			assert.Equalf(t, tt.want, FlattenedKeyWithOutID(tt.args.key), "FlattenedKey(%v)", tt.args.key)
		})
	}
}

func TestUnflattenKey(t *testing.T) {
	type args struct {
		key      string
		entityId string
	}
	tests := []struct {
		name string
		args args
		want *rpc.Key
	}{
		{
			name: "success",
			args: args{key: "example/pg/merchant/refund/Features", entityId: "swiggy_mid"},
			want: &rpc.Key{
				Namespace:  "example/pg",
				Entity:     "merchant",
				EntityId:   "swiggy_mid",
				Domain:     "refund",
				ObjectName: "Features",
			},
		},
		{
			name: "failure",
			args: args{key: "example/merchant/refund/Features", entityId: "swiggy_mid"},
			want: &rpc.Key{},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			assert.Equalf(t, tt.want, UnflattenKey(tt.args.key, tt.args.entityId), "GetKvKey(%v)", tt.args.key)
		})
	}
}

func TestFlattenedKeyWithID(t *testing.T) {
	type args struct {
		key *rpc.Key
	}
	tests := []struct {
		name string
		args args
		want string
	}{
		{
			name: "success",
			args: args{key: &rpc.Key{
				Namespace:  "example/pg",
				Entity:     "merchant",
				EntityId:   "mid_swiggy",
				Domain:     "refund",
				ObjectName: "Features",
			}},
			want: "example/pg/merchant/mid_swiggy/refund/Features",
		},
		{
			name: "failure",
			args: args{key: nil},
			want: "",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			assert.Equalf(t, tt.want, FlattenedKeyWithID(tt.args.key), "FlattenedKeyWithID(%v)", tt.args.key)
		})
	}
}
