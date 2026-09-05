package features

import (
	"github.com/razorpay/config-proto/gen"
	featurespkg "github.com/razorpay/goutils/dcs/server/features"
	"google.golang.org/protobuf/reflect/protoreflect"
)

type feature struct{}

const (
	// RefundEnabled is the api feature constant
	RefundEnabled      = featurespkg.ApiName("refund_enabled")
	DisableAutoRefund  = featurespkg.ApiName("disable_auto_refund")
	EligibilityEnabled = featurespkg.ApiName("eligibility_enabled")
)

const (
	// DcsRefundEnabled is the dcs feature constant
	DcsRefundEnabled      = featurespkg.DcsName("refund_enabled")
	DcsDisableAutoRefund  = featurespkg.DcsName("disable_auto_refund")
	DcsEligibilityEnabled = featurespkg.DcsName("eligibility_enabled")
)

// New returns an object implementing dcs go-utils Features interface which contains utility functions to Enable Features
func New() *feature {
	return &feature{}
}

// Key returns the key based on FeatureName
func (f *feature) Key(name string) featurespkg.DcsKey {
	switch name {
	case RefundEnabled.String(),
		DisableAutoRefund.String(),
		DcsDisableAutoRefund.String(),
		DcsRefundEnabled.String():
		return "example/pg/merchant/refund/Features"
	case EligibilityEnabled.String(), DcsEligibilityEnabled.String():
		return "rzp/pg/merchant/affordability/EligibilityFeatures"
	default:
		return ""
	}
}

// DcsFieldNameFromApiName returns the dcs Name based on FeatureName in API
func (f *feature) DcsFieldNameFromApiName(name featurespkg.ApiName) featurespkg.DcsName {
	switch name {
	case RefundEnabled:
		return DcsRefundEnabled
	case DisableAutoRefund:
		return DcsDisableAutoRefund
	case EligibilityEnabled:
		return DcsEligibilityEnabled
	default:
		return featurespkg.DcsName(name)
	}
}

// EnabledFeaturesForKeyFromResponse returns the enabled features from dcs response bytes for a key
func (f *feature) EnabledFeaturesForKeyFromResponse(key string, value []byte) []string {
	var res []string
	fn, err := gen.FetchUnMarshalFn(key)
	if err != nil {
		return res
	}
	m, err := fn(value)
	if err != nil {
		return res
	}
	m.ProtoReflect().Range(func(p protoreflect.FieldDescriptor, v protoreflect.Value) bool {
		if v.Bool() {
			res = append(res, p.TextName())
		}
		return true
	})

	return res
}
