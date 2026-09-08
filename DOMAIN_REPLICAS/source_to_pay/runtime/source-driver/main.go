// Replica entrypoint: local transport/control around unchanged source business
// logic. The adapter endpoints are never represented as upstream HTTP routes.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"sync"
	"sync/atomic"
	"time"
	_ "time/tzdata"

	"github.com/pressly/goose"
	tprpc "github.com/razorpay/vendor-payments/generated_endpoints/rpc/tax-payments"
	vprpc "github.com/razorpay/vendor-payments/generated_endpoints/rpc/vendor-payments"
	"github.com/razorpay/vendor-payments/internal/apiservice"
	"github.com/razorpay/vendor-payments/internal/boot"
	"github.com/razorpay/vendor-payments/internal/common"
	_ "github.com/razorpay/vendor-payments/internal/migrations"
	"github.com/razorpay/vendor-payments/internal/pubsub"
	"github.com/razorpay/vendor-payments/internal/requestmode"
	"github.com/razorpay/vendor-payments/internal/tasks"
	"github.com/razorpay/vendor-payments/internal/taxpayments"
	vptrace "github.com/razorpay/vendor-payments/internal/trace"
	"github.com/razorpay/vendor-payments/pkg/db"
	kafka "github.com/segmentio/kafka-go"
)

var clockSeconds atomic.Int64
var operations sync.Mutex
var consumeReady atomic.Bool
var consumeFailure atomic.Value

const correlationID = "s2p-golden-20260820-001"

func ctx() context.Context {
	c := requestmode.SetMode(context.Background(), "live")
	rc := vptrace.GetRequestContext(c)
	rc.RequestId = correlationID
	rc.TaskId = correlationID
	rc.ServiceName = "s2p-source-driver"
	return c
}

func trace(kind string, data interface{}) error {
	b, err := json.Marshal(data)
	if err != nil {
		return err
	}
	return db.RepoClient.DBFromContext(ctx()).Exec(
		"INSERT INTO replica_trace(kind,payload,created_at,correlation_id) VALUES(?,?,?,?)", kind, string(b), time.Now().Unix(), correlationID).Error
}

type subscriber struct{}

func (s subscriber) PublisherUpdate(c context.Context, topic string, data interface{}) {
	if err := trace("source_pubsub", map[string]interface{}{"topic": topic, "data": data}); err != nil {
		log.Fatal(err)
	}
}

func consume() {
	reader := kafka.NewReader(kafka.ReaderConfig{Brokers: []string{"kafka:9092"}, GroupID: "s2p-actual-source",
		Topic: "add-tds-entry", MinBytes: 1, MaxBytes: 1024 * 1024, MaxWait: time.Second,
		StartOffset: kafka.FirstOffset, CommitInterval: 0})
	defer reader.Close()
	task := tasks.NewInitiateTdsTask()
	consumeReady.Store(true)
	for {
		msg, err := reader.FetchMessage(context.Background())
		if err != nil {
			consumeFailure.Store(err.Error())
			log.Fatal(err)
		}
		operations.Lock()
		payload := map[string]interface{}{"topic": msg.Topic, "partition": msg.Partition, "offset": msg.Offset,
			"key": string(msg.Key), "value": string(msg.Value)}
		if err := trace("kafka_received", payload); err != nil {
			log.Fatal(err)
		}
		// The upstream handler deliberately returns nil on some business errors.
		// Independent SQL/tag-back assertions, not this return value, decide success.
		err = task.ProcessMessage(ctx(), string(msg.Value), msg.Time)
		if err != nil {
			payload["handler_error"] = err.Error()
		}
		if err := trace("source_handler_returned", payload); err != nil {
			log.Fatal(err)
		}
		operations.Unlock()
		if err := reader.CommitMessages(context.Background(), msg); err != nil {
			log.Fatal(err)
		}
		if err := trace("kafka_committed", payload); err != nil {
			log.Fatal(err)
		}
	}
}

func respond(w http.ResponseWriter, status int, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(data); err != nil {
		log.Print(err)
	}
}

func body(r *http.Request, target interface{}) error {
	return json.NewDecoder(http.MaxBytesReader(nil, r.Body, 1024*1024)).Decode(target)
}

func handler(w http.ResponseWriter, r *http.Request) {
	if r.Method == "GET" && r.URL.Path == "/health" {
		sql := db.RepoClient.DBFromContext(ctx()).DB()
		if err := sql.Ping(); err != nil {
			respond(w, 503, map[string]interface{}{"error": err.Error()})
			return
		}
		if !consumeReady.Load() {
			respond(w, 503, map[string]interface{}{"ready": false})
			return
		}
		respond(w, 200, map[string]interface{}{"ready": true, "process": "source-driver", "source_sha": os.Getenv("SOURCE_SHA"),
			"synthetic_clock": time.Unix(clockSeconds.Load(), 0).UTC().Format(time.RFC3339)})
		return
	}
	operations.Lock()
	defer operations.Unlock()
	switch r.URL.Path {
	case "/_replica/clock":
		var input struct {
			Unix int64 `json:"unix"`
		}
		if r.Method != "POST" || body(r, &input) != nil || input.Unix <= 0 {
			respond(w, 400, map[string]string{"error": "positive unix required"})
			return
		}
		if input.Unix < clockSeconds.Load() {
			respond(w, 400, map[string]string{"error": "clock must advance"})
			return
		}
		clockSeconds.Store(input.Unix)
		if err := trace("clock_advanced", input); err != nil {
			respond(w, 500, map[string]string{"error": err.Error()})
			return
		}
		respond(w, 200, input)
	case "/_replica/pay":
		var input tprpc.PayTaxPaymentRequest
		if r.Method != "POST" {
			respond(w, 405, map[string]string{"error": "POST required"})
			return
		}
		if err := body(r, &input); err != nil {
			respond(w, 400, map[string]string{"error": err.Error()})
			return
		}
		if err := trace("pay_requested", input); err != nil {
			respond(w, 500, map[string]string{"error": err.Error()})
			return
		}
		result, err := taxpayments.GetCore().Pay(ctx(), &input)
		if err != nil {
			respond(w, 422, map[string]string{"error": err.Error()})
			return
		}
		respond(w, 200, result)
	case "/_replica/status":
		var input vprpc.PayoutStatusChangeRequest
		if r.Method != "POST" {
			respond(w, 405, map[string]string{"error": "POST required"})
			return
		}
		if err := body(r, &input); err != nil {
			respond(w, 400, map[string]string{"error": err.Error()})
			return
		}
		if err := trace("callback_received", input); err != nil {
			respond(w, 500, map[string]string{"error": err.Error()})
			return
		}
		result, err := (&apiservice.VPServer{}).PayoutStatusChange(ctx(), &input)
		if err != nil {
			respond(w, 422, map[string]string{"error": err.Error()})
			return
		}
		respond(w, 200, result)
	case "/_replica/tax-payment":
		input := tprpc.GetTaxPaymentRequest{MerchantId: r.URL.Query().Get("merchant_id"), TaxPaymentId: r.URL.Query().Get("id")}
		result, err := taxpayments.GetCore().Get(ctx(), &input)
		if err != nil {
			respond(w, 422, map[string]string{"error": err.Error()})
			return
		}
		respond(w, 200, result)
	default:
		respond(w, 404, map[string]string{"error": "unknown local adapter endpoint"})
	}
}

func main() {
	if len(os.Args) > 1 && os.Args[1] == "health" {
		client := http.Client{Timeout: 3 * time.Second}
		response, err := client.Get("http://127.0.0.1:8080/health")
		if err != nil {
			log.Fatal(err)
		}
		defer response.Body.Close()
		if response.StatusCode != http.StatusOK {
			log.Fatalf("source health status: %d", response.StatusCode)
		}
		return
	}
	initial, err := time.Parse(time.RFC3339, "2026-08-20T06:30:00Z")
	if err != nil {
		log.Fatal(err)
	}
	clockSeconds.Store(initial.Unix())
	// Existing injectable source clock; no business-date validation is bypassed.
	common.TimeNow = func() time.Time { return time.Unix(clockSeconds.Load(), 0) }
	if err := boot.InitSourceToPayReplica(ctx()); err != nil {
		log.Fatal(err)
	}
	if err := goose.SetDialect("mysql"); err != nil {
		log.Fatal(err)
	}
	if err := goose.Up(db.RepoClient.DBFromContext(ctx()).DB(), "internal/migrations"); err != nil {
		log.Fatal(err)
	}
	if err := db.RepoClient.DBFromContext(ctx()).Exec("CREATE TABLE IF NOT EXISTS replica_trace (id BIGINT AUTO_INCREMENT PRIMARY KEY, kind VARCHAR(64) NOT NULL, payload JSON NOT NULL, created_at BIGINT NOT NULL, correlation_id VARCHAR(64) NOT NULL)").Error; err != nil {
		log.Fatal(err)
	}
	if err := pubsub.GetPubSub().Subscribe(pubsub.Topics.TaxPaymentUpdated, subscriber{}); err != nil {
		log.Fatal(err)
	}
	if err := trace("source_booted", map[string]interface{}{"source_sha": os.Getenv("SOURCE_SHA"), "clock": initial.Format(time.RFC3339)}); err != nil {
		log.Fatal(err)
	}
	go consume()
	fmt.Println("SOURCE_DRIVER_READY")
	server := http.Server{Addr: ":8080", Handler: http.HandlerFunc(handler), ReadHeaderTimeout: 5 * time.Second}
	log.Fatal(server.ListenAndServe())
}
