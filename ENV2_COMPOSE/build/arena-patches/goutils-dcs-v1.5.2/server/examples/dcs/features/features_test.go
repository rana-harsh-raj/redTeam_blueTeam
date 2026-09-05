package features

import (
	"reflect"
	"testing"

	"github.com/razorpay/goutils/dcs/server/features"
)

func TestNew(t *testing.T) {
	tests := []struct {
		name string
		want features.Features
	}{
		{
			name: "success",
			want: &feature{},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := New(); !reflect.DeepEqual(got, tt.want) {
				t.Errorf("New() = %v, want %v", got, tt.want)
			}
		})
	}
}

func Test_feature_DcsFieldNameFromApiName(t *testing.T) {
	type args struct {
		name features.ApiName
	}
	tests := []struct {
		name string
		args args
		want features.DcsName
	}{
		{
			name: "success_refund_enabled",
			args: args{name: RefundEnabled},
			want: DcsRefundEnabled,
		},
		{
			name: "success_eligibility_enabled",
			args: args{name: EligibilityEnabled},
			want: DcsEligibilityEnabled,
		},
		{
			name: "success_disable_auto_refund",
			args: args{name: DisableAutoRefund},
			want: DcsDisableAutoRefund,
		},
		{
			name: "success_default_case",
			args: args{name: "test"},
			want: "test",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			f := &feature{}
			if got := f.DcsFieldNameFromApiName(tt.args.name); got != tt.want {
				t.Errorf("DcsFieldNameFromApiName() = %v, want %v", got, tt.want)
			}
		})
	}
}

func Test_feature_EnabledFeaturesForKeyFromResponse(t *testing.T) {
	type args struct {
		key   string
		value []byte
	}
	tests := []struct {
		name string
		args args
		want []string
	}{
		{
			name: "success",
			args: args{
				key:   "example/pg/merchant/refund/Features",
				value: []byte{0x8, 0x1},
			},
			want: []string{RefundEnabled.String()},
		},
		{
			name: "failure",
			args: args{
				key:   "example/pg/merchant/Features",
				value: []byte{0x8, 0x1},
			},
			want: nil,
		},
		{
			name: "failure_fn_failure",
			args: args{
				key:   "example/pg/merchant/refund/Features",
				value: []byte{0x0, 0x8},
			},
			want: nil,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			f := &feature{}
			if got := f.EnabledFeaturesForKeyFromResponse(tt.args.key, tt.args.value); !reflect.DeepEqual(got, tt.want) {
				t.Errorf("EnabledFeaturesForKeyFromResponse() = %v, want %v", got, tt.want)
			}
		})
	}
}

func Test_feature_Key(t *testing.T) {
	type args struct {
		name string
	}
	tests := []struct {
		name string
		args args
		want features.DcsKey
	}{
		{
			name: "success_refund_enabled",
			args: args{name: RefundEnabled.String()},
			want: features.DcsKey("example/pg/merchant/refund/Features"),
		},
		{
			name: "success_eligibility",
			args: args{name: EligibilityEnabled.String()},
			want: features.DcsKey("rzp/pg/merchant/affordability/EligibilityFeatures"),
		},
		{
			name: "success_disable_auto_refund",
			args: args{name: DisableAutoRefund.String()},
			want: features.DcsKey("example/pg/merchant/refund/Features"),
		},
		{
			name: "success_default",
			args: args{name: "test"},
			want: features.DcsKey(""),
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			f := &feature{}
			if got := f.Key(tt.args.name); got != tt.want {
				t.Errorf("Key() = %v, want %v", got, tt.want)
			}
		})
	}
}
