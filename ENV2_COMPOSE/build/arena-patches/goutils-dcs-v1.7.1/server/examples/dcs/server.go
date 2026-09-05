package dcs

import (
	dcs "github.com/razorpay/goutils/dcs/server"
)

type Server struct {
	*dcs.Server
	svc *Service
}

func NewServer(s *Service) *Server {
	return &Server{
		dcs.NewServer(s.Service),
		s,
	}
}
