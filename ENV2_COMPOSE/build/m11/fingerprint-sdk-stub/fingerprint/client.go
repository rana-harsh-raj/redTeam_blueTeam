// Package fingerprint is the M11 twin STUB for github.com/razorpay/fingerprint-sdk (external device-fingerprint
// vendor SDK; repository returns 404 to the build identity, see ENV2_COMPOSE/build/m11/build-shield.sh).
// It implements only the three symbols razorpay/shield references (app/services/params/derived.go,
// app/crossBorder/boot/boot.go, app/crossBorder/app/services/external_risk_evaluator/providers/fingerprint.go,
// app/crossBorder/app/job/fingerprint_user_risk_details/helper.go). Every call answers "no data": the payout rule
// evaluation path never reaches these (they belong to the cross-border card-payment risk flow).
package fingerprint

import (
	"context"
	"errors"
)

type Client struct{}

func New(_ context.Context) *Client { return &Client{} }

// GetEvent would fetch a fingerprint event from the vendor; the twin has no vendor.
func (c *Client) GetEvent(_ context.Context, _ string, _ string) (map[string]interface{}, error) {
	return nil, errors.New("fingerprint-sdk stub: no external fingerprint provider in the twin")
}

// GetFingerprintIdFromSealedResponse would unseal a vendor response; the twin has no vendor.
func (c *Client) GetFingerprintIdFromSealedResponse(_ string, _ string) (string, error) {
	return "", errors.New("fingerprint-sdk stub: no external fingerprint provider in the twin")
}

// UnsealEventsResponse would decrypt a sealed vendor events payload; the twin has no vendor.
func (c *Client) UnsealEventsResponse(_ string, _ string) (interface{}, error) {
	return nil, errors.New("fingerprint-sdk stub: no external fingerprint provider in the twin")
}
