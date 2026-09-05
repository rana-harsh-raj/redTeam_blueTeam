package main

import (
	"context"
	"flag"
	"fmt"
	"net/http"

	refundpb "github.com/razorpay/config-proto/rpc/go/example/pg/merchant/refund"
	"github.com/razorpay/goutils/dcs"
	"github.com/razorpay/goutils/dcs/cache"
	"github.com/razorpay/goutils/dcs/config"
	"github.com/razorpay/goutils/dcs/logger"
	rpc "github.com/razorpay/goutils/dcs/rpc/dcs/kv/v1"
	"google.golang.org/protobuf/proto"
	"k8s.io/klog/v2"
)

// Command runs all the tasks for the client in an interactive way
type Command struct {
	client dcs.ConfigClient
}

// NewCommand creates the new command
func NewCommand(client dcs.ConfigClient) *Command {
	return &Command{
		client: client,
	}
}

// Put replaces the keys value
func (c *Command) Put(ctx context.Context) error {
	klog.Infof("Put() begin --")
	refundSwiggy := &refundpb.Features{
		RefundEnabled:     true,
		DisableAutoRefund: false,
	}

	klog.Infof("Refund=%#+v", refundSwiggy)

	value, err := proto.Marshal(refundSwiggy)
	if err != nil {
		klog.Error("error marshaling refund:%v", err)
		return err
	}

	klog.Infof("Refund Bytes=%#+v, len(value)=%+v", value, len(value))

	request := &rpc.PutRequest{
		Key: &rpc.Key{
			Namespace:  "example/pg",
			Entity:     "merchant",
			EntityId:   "1",
			Domain:     "refund",
			ObjectName: "Features",
		},
		Value: value,
		AuditLog: &rpc.AuditLog{
			ChangeBy:         "dev@razorpay.com",
			ChangeReason:     "compliance issue",
			ChangeApprovedBy: "techlead@razorpay.com",
		},
	}

	klog.Infof("PutRequest=%+v", request)

	response, err := c.client.Put(ctx, request)
	if err != nil {
		klog.Errorf("Error:%+v", err)
		return err
	}

	klog.Infof("PutResponse=%+v", response)
	klog.Info("Put() end -- \n\n")
	return nil
}

// Get gets the keys
func (c *Command) Get(ctx context.Context) error {
	klog.Infof("Get() begin --")
	request := &rpc.GetRequest{
		Queries: []*rpc.Query{
			{
				Key: &rpc.Key{
					Namespace:  "example/pg",
					Entity:     "merchant",
					EntityId:   "1",
					Domain:     "refund",
					ObjectName: "Features", // TODO: handle camel case conversion
				},
				Fieldmasks: []*rpc.Field{
					{Name: "disable_auto_refund"},
					{Name: "refund_enabled"},
				},
			},
		},
	}

	klog.Infof("GetRequest=%+v", request)

	response, err := c.client.Get(ctx, request)
	if err != nil {
		klog.Errorf("Error:%+v", err)
		return err
	}

	for _, resp := range response.Kvs {
		refund := &refundpb.Features{}
		fmt.Println(string(resp.Value))
		err := proto.Unmarshal(resp.Value, refund)
		if err != nil {
			klog.Error(err)
			return err
		}
		klog.Infof("Refund=%+#v", refund)
	}

	klog.Infof("GetResponse=%+v", response)

	klog.Infof("Get() end --\n\n")
	return nil
}

// Patch patches proto
func (c *Command) Patch(ctx context.Context) error {
	klog.Infof("Patch() begin --")
	partialObject := refundpb.Features{ // patching refund object for one field
		// RefundEnabled:     true,
		DisableAutoRefund: true,
	}

	klog.Infof("partialRefund=%+v", partialObject)

	patchBytes, err := proto.Marshal(&partialObject)
	if err != nil {
		klog.Fatalf("error marshaling refund:%v", err)
	}

	request := &rpc.PatchRequest{
		Key: &rpc.Key{
			Namespace:  "example/pg",
			Entity:     "merchant",
			EntityId:   "1",
			Domain:     "refund",
			ObjectName: "Features", // TODO: handle camel case conversion
		},
		Value: patchBytes,
		Fieldmasks: []*rpc.Field{ // Provide info on which fields are getting patched
			{Name: "disable_auto_refund"},
		},
		AuditLog: &rpc.AuditLog{
			ChangeBy:         "dev2@razorpay.com",
			ChangeReason:     "refund patch",
			ChangeApprovedBy: "techlead2@razorpay.com",
		},
	}

	klog.Infof("PatchRequest=%+v", request)

	response, err := c.client.Patch(ctx, request)
	if err != nil {
		klog.Errorf("Error:%+v", err)
		return err
	}

	klog.Infof("PatchResponse=%+v", response)
	klog.Infof("Patch() end --")
	return nil
}

// GetAuditHistory gets the audit history data
func (c *Command) GetAuditHistory(ctx context.Context) error {
	klog.Infof("GetAudit() begin ---")
	request := &rpc.GetAuditHistoryRequest{
		Key: &rpc.Key{
			Namespace:  "example/pg",
			Entity:     "merchant",
			EntityId:   "1",
			Domain:     "refund",
			ObjectName: "Features", // TODO: handle camel case conversion
		},
		Count: 2,
	}

	response, err := c.client.GetAuditHistory(ctx, request)
	if err != nil {
		klog.Errorf("Error:%+v", err)
		return err
	}

	for i, resp := range response.AuditLogs {
		klog.Infof("i=%d, resp=%+v\n", i, resp)
	}
	klog.Infof("GetAudit() end ---")
	return nil
}

// Define a mock struct to be used in your unit tests of GetRefund.
type mockClient struct {
	dcs.ConfigClient
}

func (m *mockClient) Get(_ context.Context, in *rpc.GetRequest) (*rpc.GetResponse, error) {
	fmt.Println("Entered Mock Function ")
	return &rpc.GetResponse{
		Kvs: []*rpc.KeyValue{
			{
				Key:   in.Queries[0].Key,
				Value: []byte{16, 1},
			},
		},
	}, nil
}

func main() {
	var serverURL, user, pass string
	flag.StringVar(&serverURL, "h", "http://localhost:8081", "host url of the dcs server")
	flag.StringVar(&serverURL, "e", "dev", "host url of the dcs server")
	flag.StringVar(&user, "u", "example", "user of the dcs server")
	flag.StringVar(&pass, "p", "example", "pass of the dcs server")
	flag.Parse()

	ctx := context.Background()

	creds := &config.UserCredentials{
		Username: user,
		Password: pass,
	}
	dcsCache, err := cache.New(ctx, cache.NewConfig())
	if err != nil {
		panic(err)
	}
	config := config.NewConfig().
		WithMock(false).
		WithModes([]config.Mode{config.Test}).
		WithEnv(config.Dev).
		WithCredentials(creds)

	options := []dcs.Option{
		dcs.WithConfig(config),
		dcs.WithLogger(logger.NewDefaultLogger(ctx)),
		dcs.WithCache(dcsCache),
		dcs.WithHTTPClient(http.DefaultClient),
	}
	client, err := dcs.New(ctx, options...)
	if err != nil {
		panic(err)
	}
	mock := &mockClient{client}

	cmd := NewCommand(client)
	mockCmd := NewCommand(mock)

	// command.go has examples of requests the client makes
	err = cmd.Put(ctx)
	if err != nil {
		panic(err)
	}

	err = cmd.Get(ctx)
	if err != nil {
		panic(err)
	}

	err = cmd.Patch(ctx)
	if err != nil {
		panic(err)
	}

	err = cmd.Get(ctx)
	if err != nil {
		panic(err)
	}

	err = cmd.GetAuditHistory(ctx)
	if err != nil {
		panic(err)
	}

	// example mock
	err = mockCmd.Get(ctx)
	if err != nil {
		panic(err)
	}
}
