package server

import (
	"context"
	"fmt"

	contracts "github.com/razorpay/goutils/dcs/server/contracts/rpc/client/dcs/v1"
)

// Server implements the rpc for the dcs apis
// dcs apis for write request from dashboards, currently api proxy
// dashboard -> api proxy -> dcs api in pg-router microservice
type Server struct {
	// rpc.KVServiceServer makes the Server implement the rpc functions
	contracts.DcsAPIServer
	// service handles all the functionality
	service *Service
}

// NewServer return the new instance of server
func NewServer(svc *Service) *Server {
	return &Server{service: svc}
}

func (s *Server) ServiceName() string {
	return "DCSService"
}

// Set updates the configuration for a key in dcs
func (s *Server) Set(ctx context.Context, req *contracts.SetRequest) (*contracts.SetResponse, error) {
	if req == nil {
		return &contracts.SetResponse{
			Success: false,
			Error: &contracts.Error{
				Code:        "INVALID_REQUEST",
				Description: "set request cannot be nil",
			},
		}, fmt.Errorf("set request cannot be nil")
	}

	err := s.service.Set(ctx, req)
	if err != nil {
		return &contracts.SetResponse{
			Key:     req.Key,
			Success: false,
			Error: &contracts.Error{
				Code:        "INTERNAL_SERVER_ERROR",
				Description: err.Error(),
			},
		}, err
	}

	return &contracts.SetResponse{
		Key:     req.Key,
		Success: true,
	}, nil
}

func (s *Server) Service() *Service {
	if s != nil {
		return s.service
	}
	return nil
}
