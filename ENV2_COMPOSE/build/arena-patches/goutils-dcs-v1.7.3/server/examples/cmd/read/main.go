package main

import (
	"context"
	"flag"
	"fmt"
	"time"

	"github.com/razorpay/goutils/dcs/server"
	"github.com/razorpay/goutils/dcs/server/examples/dcs/features"
	dcs "github.com/razorpay/goutils/dcs/server/examples/pkg/dcs/dual_mode"
)

func main() {
	var serverURL, user, pass, env string
	flag.StringVar(&serverURL, "h", "http://localhost:8081", "host url of the dcs server")
	flag.StringVar(&user, "u", "example", "user of the dcs server")
	flag.StringVar(&pass, "p", "example", "pass of the dcs server")
	flag.StringVar(&env, "e", "stage", "env of the dcs server")
	flag.Parse()

	ctx := context.Background()
	cfg := dcs.Config{
		Username: user,
		Password: pass,
		Env:      env,
	}

	clt, err := dcs.New(ctx, cfg)
	if err != nil {
		panic(err)
	}

	svc := server.NewDcs(clt, dcs.Marshal)
	count := 15
	for {
		out, err := svc.EnabledFeatures(ctx, features.New(), []string{features.RefundEnabled.String()}, "mid_swiggy2", "live")
		if err != nil {
			panic(err)
		}
		fmt.Println("count: ", count, "\n", "out: ", out)
		count++
		if count > 15 {
			break
		}
		time.Sleep(2 * time.Second)
	}
}
