package server

import (
	"context"
	"fmt"

	"github.com/razorpay/goutils/dcs"
	rpc "github.com/razorpay/goutils/dcs/rpc/dcs/kv/v1"
	contracts "github.com/razorpay/goutils/dcs/server/contracts/rpc/client/dcs/v1"
	featurespkg "github.com/razorpay/goutils/dcs/server/features"
)

type MarshalFn func(key string, fields []byte) ([]byte, []string, error)

// Service is the implementation to handle all pg-router features
type Service struct {
	client dcs.ConfigClient
	fn     MarshalFn
}

// NewDcs returns the features dcs impl
func NewDcs(client dcs.ConfigClient, fn MarshalFn) *Service {
	return &Service{
		client: client,
		fn:     fn,
	}
}

// Set sets the dynamic configurations for PG router features
func (c *Service) Set(ctx context.Context, req *contracts.SetRequest) error {
	if req == nil {
		return fmt.Errorf("dcs set request cannot be nil")
	}

	key := dcs.UnflattenKey(req.Key, req.EntityId)

	objectBytes, fields, err := c.fn(req.Key, []byte(req.Value))
	if err != nil {
		return err
	}
	fieldMasks := []*rpc.Field{}
	for _, field := range fields {
		fieldMasks = append(fieldMasks, &rpc.Field{
			Name: field,
		})
	}

	// In-case of dual mode you need to dynamically add
	// mode in context, which will be used to select the url based on mode
	if req.LiveMode {
		ctx = dcs.SetContextMode(ctx, "live")
	} else {
		ctx = dcs.SetContextMode(ctx, "test")
	}

	// set config in dcs server
	request := &rpc.PatchRequest{
		Key:   key,
		Value: objectBytes,
		AuditLog: &rpc.AuditLog{
			ChangeBy:         req.AuditLog.ChangeBy,
			ChangeReason:     req.AuditLog.ChangeReason,
			ChangeApprovedBy: req.AuditLog.ChangeApprovedBy,
		},
		Fieldmasks: fieldMasks,
	}
	_, err = c.client.Patch(ctx, request)
	if err != nil {
		return err
	}

	return nil
}

// EnabledFeatures helps in fetches features flags based on EntityID
func (c *Service) EnabledFeatures(ctx context.Context,
	in featurespkg.Features,
	flags []string,
	entityID string, mode string) ([]string, error) {
	var keys = map[string]int{}
	var values = make([][]*rpc.Field, len(flags))
	var count = 0
	var features []string

	ctx = dcs.SetContextMode(ctx, mode)

	for _, name := range flags {
		keyStr := in.Key(name).String()
		field := &rpc.Field{
			Name: name,
		}
		if index, ok := keys[keyStr]; !ok {
			keys[keyStr] = count
			values[count] = []*rpc.Field{field}
			count++
		} else {
			values[index] = append(values[index], field)
		}
	}

	req := &rpc.GetRequest{Queries: []*rpc.Query{}}
	for k, v := range keys {
		key := dcs.UnflattenKey(k, entityID)
		req.Queries = append(req.Queries, &rpc.Query{Key: key, Fieldmasks: values[v]})
	}

	resp, err := c.client.Get(ctx, req)
	if err != nil {
		return features, err
	}

	// default is consider disabled if no error and data empty
	if resp == nil || len(resp.Kvs) == 0 {
		return features, nil
	}
	for _, res := range resp.Kvs {
		if res == nil {
			continue
		}

		features = append(features,
			in.EnabledFeaturesForKeyFromResponse(
				dcs.FlattenedKeyWithOutID(res.Key),
				res.Value)...)
	}

	return features, nil
}
