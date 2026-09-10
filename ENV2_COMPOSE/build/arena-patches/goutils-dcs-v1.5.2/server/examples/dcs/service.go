package dcs

import (
	"context"

	dcs "github.com/razorpay/goutils/dcs/server"
	dcspkg "github.com/razorpay/goutils/dcs/server/examples/pkg/dcs/dual_mode"
)

type Service struct {
	*dcs.Service
}

func NewService(ctx context.Context, cfg dcspkg.Config, mode bool) (*Service, error) {
	// dcs specific wiring
	dcsClient, err := dcspkg.New(ctx, cfg)
	if err != nil {
		return nil, err
	}

	dcsService := dcs.NewDcs(dcsClient, dcspkg.Marshal)
	return &Service{
		dcsService,
	}, nil
}
