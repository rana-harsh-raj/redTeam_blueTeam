package mock

import (
	"context"
	"fmt"

	"github.com/razorpay/goutils/dcs"
	rpc "github.com/razorpay/goutils/dcs/rpc/dcs/kv/v1"
)

// Client provides the API operation methods for making requests to
// DCS Server. See this Readme for details on the service.
type Client struct {
	*dcs.Client
	ReturnFailure  bool
	ReturnDisabled bool
}

func (c *Client) Get(ctx context.Context, in *rpc.GetRequest) (*rpc.GetResponse, error) {
	if c.ReturnFailure {
		return nil, fmt.Errorf("return error from DCS server")
	}
	res := &rpc.GetResponse{}

	for _, v := range in.Queries {
		kv := &rpc.KeyValue{
			Key:   v.Key,
			Value: []byte{0x8, 0x1},
		}
		if c.ReturnDisabled {
			kv.Value = []byte{}
		}
		res.Kvs = append(res.Kvs, kv)
	}
	return res, nil
}

// Patch adds or replace a key with a value
func (c *Client) Patch(ctx context.Context, in *rpc.PatchRequest) (*rpc.PatchResponse, error) {
	if c.ReturnFailure {
		return nil, fmt.Errorf("return error from DCS server")
	}

	res := &rpc.PatchResponse{
		Key:   in.Key,
		Count: 1,
	}

	return res, nil
}
