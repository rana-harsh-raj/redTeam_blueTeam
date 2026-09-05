# Dynamic Configuration Service (DCS) SDK
[![dcs api docs](https://img.shields.io/badge/api-reference-blue.svg)](https://idocs.razorpay.com/openapi/dcs/master) [![dcs on slack](https://img.shields.io/badge/slack-pg--common--platform-green?logo=slack)](https://razorpay.slack.com/archives/C03EL9LM058)

Dynamic configuration is the ability to change the behavior and functionality of a running system in realtime. Dynamic Configuration Service(DCS) is used to store and change configurations in realtime with audit history of every change. It is used to store merchant configurations like settlement settings, refund settings, limit settings, and other use cases.

- [Tech Spec](https://docs.google.com/document/d/1fzptKiMj5ssYu-nfV2jtFCKFa6KVaT_Sbn4fVxy3JnE)
- [Product Spec](https://docs.google.com/document/d/1He93pJwBZvRMtxtmXEeHH0mckl3EYxbooU7ntHPQXQc/edit).
- Configurations schema are stored in [github.com/razorpay/config-proto](https://github.com/razorpay/config-proto). DCS server repo is [github.com/razorpay/dcs](https://github.com/razorpay/dcs).
- [Arch diagram](https://github.com/razorpay/dcs/blob/master/README.md#dcs)

### Pre-requisites

#### Create the user in dcs server
Create a user with a role and password to talk to the dcs server. DCS Server has an [Admin API](https://github.com/razorpay/dcs/blob/master/README.md#create-a-user) for this. Take help of #payments-common-platform team to register a user.

#### Define Configuration Schema
Define the configuration schema in the central [config-proto](https://github.com/razorpay/config-proto#sequence-of-commands) repo. The proto `message` would generate the `go structs` for this message in `.pb.go` files which are stored in the same repo. You need to import these struct from their for use in your repo.
```go
import (
    refundpb "github.com/razorpay/config-proto/rpc/go/example/merchant"
)
```

#### Authz
The `role` for the user you created needs to have access permissions on the keys it want to read or write. The `role` to `permissions` mapping is maintained in Authz. Authz is used to authorize this. Refer [this](https://github.com/razorpay/dcs/blob/master/README.md#create-policies-in-authz-for-the-role) to get the permissions added.

### Client
dcs client is used to talk to dcs server. You can create the client like this:

#### Client implementation
please check the [examples](https://github.com/razorpay/goutils/blob/b06d364074cc444f9579a1dce95a74c97065d14a/dcs/server/examples) for client server implementation.
`pkg/dcs` folder has both `dual mode` initialization and `single mode` initialization

##### Dual Mode
This initialization is used when same server is used to update both test and live mode features.
We will identify the mode while processing the request, for it you wee need to set the mode in context by using the following function
`ctx = dcs.SetContextMode(ctx, mode)`

##### Single Mode
This initialization is used when you have test and live mode as separate servers and only one server is used for a single mode

##### Cache TTL and Max Cache Size configurations
```go
cacheConfig := &cache.NewConfig().
              WithEviction(30*time.Second). // 30 seconds TTL, default: 15 seconds
              WithMaxCacheSize(1024). // 1024 Mb, default: 8 Mb
              WithCleanWindow(1*time.Second) // better leave this to 1 second default and not change

dcsCache, err := &cache.New(cacheConfig)
```

#### Put Key
Put is used to set or replaces a key's value in the database. If you want to update or patch some fileds in an already existing key, use `Patch`, Put **replaces the key totally**.

```go
import (
	"google.golang.org/protobuf/proto"

	refundpb "github.com/razorpay/config-proto/example/pg/merchant/refund"
	dcs "github.com/razorpay/goutils/dcs"
	rpc "github.com/razorpay/goutils/dcs/rpc"
)

func main() {
	// ...

	// configuration schema are stored in config-proto repository, use it
	refundSwiggy := &refundpb.Refund{
		RefundEnabled:     false,
		DisableAutoRefund: true,
	}

	// convert into bytes
	value, _ := proto.Marshal(refundSwiggy)

	// set the config in the dcs server
	request := &rpc.PutRequest{
		Key: &rpc.Key{
			Namespace:  "example/pg",
			Entity:     "merchant",
			EntityId:   "mid_swiggy",
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

	response, _ := dcsClient.Put(ctx, request)
}
```

#### Get Key(s)
Get can be used to fetch details of multiple fields even from different domains in a single request. Fieldmasks helps in fetching attributes only the attributes required. The bytes return contain only the fields asked. This keeps the request fast and makes our APIs like GraphQL.

```go
import (
	"google.golang.org/protobuf/proto"

	refundpb "github.com/razorpay/config-proto/example/pg/merchant/refund"
	dcs "github.com/razorpay/goutils/dcs"
	rpc "github.com/razorpay/goutils/dcs/rpc"
)

func main() {
	// ...

	// specify which key using Key and what fields in that key using FieldMasks
	// FieldMasks returns redacted response, keeps the APIs like graphQL
	request := &rpc.GetRequest{
		Queries: []*rpc.Query{
			{
				Key: &rpc.Key{
					Namespace:  "example/pg",
					Entity:     "merchant",
					EntityId:   "mid_swiggy",
					Domain:     "refund",
					ObjectName: "Features",
				},
				Fieldmasks: []*rpc.Field{
					{Name: "disable_auto_refund"},
				},
			},
		},
	}

	response, _ := dcsClient.Get(ctx, request)
}
```

#### Patch Key
Patch is used to update attributes, for already existing keys.
It supports bulk updates.
Fieldmasks helps in updating only required attributes for a Key.

```go
import (
	"google.golang.org/protobuf/proto"

	refundpb "github.com/razorpay/config-proto/example/pg/merchant/refund"
	dcs "github.com/razorpay/goutils/dcs"
	rpc "github.com/razorpay/goutils/dcs/rpc"
)

func main() {
	// ...

	// patching refund object for one field
	partialObject := refundpb.Refund{
		DisableAutoRefund: true,
	}
	patchBytes, _ := proto.Marshal(&partialObject)

	// FieldMasks are used to specify which fields are getting patched
	request := &rpc.PatchRequest{
		Key: &rpc.Key{
			Namespace:  "example/pg",
			Entity:     "merchant",
			EntityId:   "mid_swiggy",
			Domain:     "refund",
			ObjectName: "Features", // TODO: handle camel case conversion
		},
		Value: patchBytes,
		Fieldmasks: []*rpc.Field{
			{Name: "disable_auto_refund"},
		},
		AuditLog: &rpc.AuditLog{
			ChangeBy:         "dev@razorpay.com",
			ChangeReason:     "refund patch",
			ChangeApprovedBy: "techlead@razorpay.com",
		},
	}

	response, _ := dcsClient.Patch(ctx, request)
}
```
#### Get Audit History for a Key
GetAuditHistory helps in fetching last n audit information of changes on a particular Key.

```go
import (
	dcs "github.com/razorpay/goutils/dcs"
	rpc "github.com/razorpay/goutils/dcs/rpc"
)

func main() {
	// ...

	request := &rpc.GetAuditHistoryRequest{
		Key: &rpc.Key{
			Namespace:  "example/pg",
			Entity:     "merchant",
			EntityId:   "mid_swiggy",
			Domain:     "refund",
			ObjectName: "Features",
		},
		Count: 10, // latest 10 requests
	}

	response, err := dcsClient.GetAuditHistory(ctx, request)
}
```

### Recommendation on HTTP Client
It is advisable to use [goutils/request](https://github.com/razorpay/goutils/blob/master/request/httpclient/client.go) `HTTPClient` instead of the `DefaultHTTPClient` since it has inbuilt support for `prometheus` metrics collection. That said, you're free to use whatever client implementation you want.

### Mock
- `ConfigClient` is the interface exposed by this package. You can use this to mock this package for unit tests. A sample mock is implemented in [examples](examples).
- `HTTPDoer` is the interface that the HTTP client needs to implement in order to work with this package.

In case of any issues with the SDK or using DCS reach out to us at [#payments_common_platform](https://razorpay.slack.com/archives/C03EL9LM058) or tag `@pg-common-platform`

### Features / TODO
- [x] `Get` **for** multi-Configurations Fetch.
- [x] `PUT` **for** Creating New Configuration.
- [x] `PATCH` **for** Update Single Configurations.
- [x] `GETAUDITHISTORY` **for** Getting last n audit history for a Configuration.
- [x] Accept logger as part of dcs config.
- [x] rpc folder creation from razorpay/proto
- [x] QUERY Caching for GET Requests.
- [ ] Prometheus Metrics for clients.

## Contributing to dcs client sdk

- Make code changes here
- Run `make proto-fetch` and then `make proto-generate`
