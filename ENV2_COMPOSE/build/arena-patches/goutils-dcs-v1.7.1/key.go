package dcs

import (
	"fmt"
	"strings"

	rpc "github.com/razorpay/goutils/dcs/rpc/dcs/kv/v1"
)

// FlattenedKeyWithOutID returns the rpc key as a string
func FlattenedKeyWithOutID(key *rpc.Key) string {
	if key == nil {
		return ""
	}

	return fmt.Sprintf(
		"%s/%s/%s/%s",
		key.Namespace,
		key.Entity,
		key.Domain,
		key.ObjectName,
	)
}

// FlattenedKeyWithID returns the rpc key as a string
func FlattenedKeyWithID(key *rpc.Key) string {
	if key == nil {
		return ""
	}

	return fmt.Sprintf(
		"%s/%s/%s/%s/%s",
		key.Namespace,
		key.Entity,
		key.EntityId,
		key.Domain,
		key.ObjectName,
	)
}

// UnflattenKey returns the rpc key from string
func UnflattenKey(key string, entityId string) *rpc.Key {
	var result *rpc.Key
	k := strings.Split(key, "/")
	n := len(k)
	if n < 5 {
		result = &rpc.Key{}
	} else {
		// key: dcs/kv/example/pg/merchant/{mid}/cards/switch/Features
		result = &rpc.Key{
			Namespace:  fmt.Sprintf("%s/%s", k[0], k[1]),
			Entity:     k[2],
			EntityId:   entityId,
			Domain:     strings.Join(k[3:n-1], "/"),
			ObjectName: k[n-1],
		}
	}

	return result
}
