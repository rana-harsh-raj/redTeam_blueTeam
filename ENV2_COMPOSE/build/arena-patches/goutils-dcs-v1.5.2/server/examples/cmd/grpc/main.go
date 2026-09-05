package main

import (
	"context"
	"flag"
	"fmt"

	"github.com/grpc-ecosystem/go-grpc-middleware/v2/interceptors/tracing"
	"github.com/grpc-ecosystem/grpc-gateway/v2/runtime"
	contracts "github.com/razorpay/goutils/dcs/server/contracts/rpc/client/dcs/v1"
	"github.com/razorpay/goutils/dcs/server/examples/dcs"
	dcspkg "github.com/razorpay/goutils/dcs/server/examples/pkg/dcs/dual_mode"

	"github.com/razorpay/goutils/grpcserver"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

const (
	// DefaultGRPCAddress for grpc server
	DefaultGRPCAddress = "0.0.0.0:8080"
	// DefaultHTTPAddress for http server
	DefaultHTTPAddress = "0.0.0.0:8081"
	// DefaultInternalAddress  for internal server
	DefaultInternalAddress = "0.0.0.0:8082"
)

func main() {
	var serverURL, user, pass, env string
	flag.StringVar(&serverURL, "h", "http://localhost:8081", "host url of the dcs server")
	flag.StringVar(&user, "u", "example", "user of the dcs server")
	flag.StringVar(&pass, "p", "example", "pass of the dcs server")
	flag.StringVar(&env, "e", "stage", "env of the dcs server")

	flag.Parse()

	ctx := context.Background()
	cfg := dcspkg.Config{
		Username: user,
		Password: pass,
		Env:      env,
	}

	svc, err := dcs.NewService(ctx, cfg, false)
	if err != nil {
		panic(err)
	}

	svr := dcs.NewServer(svc)
	grpcHandlerFn := func(server *grpc.Server) error {
		contracts.RegisterDcsAPIServer(
			server,
			svr,
		)
		return nil
	}
	httpHandlerFn := func(mux *runtime.ServeMux, address string) error {
		err := contracts.RegisterDcsAPIHandlerFromEndpoint(
			ctx, mux, address,
			[]grpc.DialOption{
				grpc.WithTransportCredentials(insecure.NewCredentials()),
				grpc.WithUnaryInterceptor(tracing.UnaryClientInterceptor()),
			},
		)
		if err != nil {
			return err
		}
		return nil
	}

	addrs := grpcserver.ServerAddresses{
		Grpc:     DefaultGRPCAddress,
		Http:     DefaultHTTPAddress,
		Internal: DefaultInternalAddress,
	}
	server, err := grpcserver.NewServer(
		addrs,
		grpcHandlerFn,
		httpHandlerFn,
	)

	defer func(server *grpcserver.Server, ctx context.Context, shutdownTimeoutSeconds int) {
		err := server.Stop(ctx, shutdownTimeoutSeconds)
		if err != nil {
			fmt.Println("failed to stop the server")
		}
	}(server, ctx, 30)
	err = server.Start(ctx)
	if err != nil {
		return
	}
}
