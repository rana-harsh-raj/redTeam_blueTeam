// This file is added to internal/boot only in the isolated build stage.
// It selects existing source initializers; no domain implementation is replaced.
// Endpoints and secrets below are overridable per twin instance (M9) through
// S2P_* environment variables; every default equals the previously hard-coded
// constant, so the pinned standalone replica behaves identically without them.
package boot

import (
	"context"
	"os"
	"strconv"

	logconfig "github.com/razorpay/goutils/logger/v2"
	"github.com/razorpay/vendor-payments/internal/config"
	"github.com/razorpay/vendor-payments/internal/initiatetds"
	"github.com/razorpay/vendor-payments/internal/rxclient"
	"github.com/razorpay/vendor-payments/pkg/cache"
	"github.com/razorpay/vendor-payments/pkg/db"
)

// envOr returns the value of key, or def when the variable is unset or empty.
func envOr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// envIntOr returns key parsed as an int, or def when unset, empty, or unparseable.
func envIntOr(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

// InitSourceToPayReplica uses synthetic local configuration and the same setup
// functions used by the upstream boot sequence. Elasticsearch, DCS, OCR, and
// unrelated worker registrations are outside this deliberately narrow process.
func InitSourceToPayReplica(ctx context.Context) error {
	d := db.Config{Dialect: "mysql", Protocol: "tcp", URL: envOr("S2P_DB_URL", "mysql:3306"), Name: envOr("S2P_DB_NAME", "vendor_payments"),
		Username: envOr("S2P_DB_USER", "s2p"), Password: envOr("S2P_DB_PASSWORD", "s2p"), MaxOpenConnections: 8, MaxIdleConnections: 4}
	config.AppConfig = config.Config{}
	config.AppConfig.Db = d
	config.AppConfig.DbTest = d
	config.AppConfig.DbReplicaLive = d
	config.AppConfig.LoggerConfig = logconfig.Config{LogLevel: "info", SentryEnabled: false}
	config.AppConfig.Cache = cache.Config{Driver: "redis", Redis: cache.RedisConfig{Host: envOr("S2P_REDIS_HOST", "redis"), Port: int32(envIntOr("S2P_REDIS_PORT", 6379))}}
	config.AppConfig.Api = rxclient.Config{Url: envOr("S2P_BOUNDARY_URL", "http://boundary:8080"), Secret: envOr("S2P_API_SECRET", "local-vendor-payments-secret"), Timeout: 10}
	config.AppConfig.InitiateTdsConfig = initiatetds.Config{Topic: "add-tds-entry"}
	config.AppConfig.Core.EnableNewTinChanges = false
	config.AppConfig.Core.DisableManualTDSTaxPayments = false
	config.AppConfig.Core.DisableGstPayments = true
	config.AppConfig.TdsTaskConfig.QueueName = "prod-tds"
	if err := os.Setenv("APP_MODE", "replica"); err != nil {
		return err
	}
	if err := initDependencies(ctx, "replica", ComponentWorker, []string{
		Dependencies.Timezone, Dependencies.Logger, Dependencies.Database,
		Dependencies.Cache, Dependencies.Prometheus,
	}); err != nil {
		return err
	}
	setupConfigs()
	setupPubSub()
	setupTaxPayments()
	setupTDSCategory()
	setupRXClient()
	setupHttp()
	setupPayments()
	setupPayout()
	setupSettings()
	setupContact()
	setupFundAccount()
	setupBankingAccount()
	setupOtp()
	setupInitiateTds()
	return nil
}
