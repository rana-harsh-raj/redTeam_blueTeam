// This file is added to internal/boot only in the isolated build stage.
// It selects existing source initializers; no domain implementation is replaced.
package boot

import (
	"context"
	"os"

	logconfig "github.com/razorpay/goutils/logger/v2"
	"github.com/razorpay/vendor-payments/internal/config"
	"github.com/razorpay/vendor-payments/internal/initiatetds"
	"github.com/razorpay/vendor-payments/internal/rxclient"
	"github.com/razorpay/vendor-payments/pkg/cache"
	"github.com/razorpay/vendor-payments/pkg/db"
)

// InitSourceToPayReplica uses synthetic local configuration and the same setup
// functions used by the upstream boot sequence. Elasticsearch, DCS, OCR, and
// unrelated worker registrations are outside this deliberately narrow process.
func InitSourceToPayReplica(ctx context.Context) error {
	d := db.Config{Dialect: "mysql", Protocol: "tcp", URL: "mysql:3306", Name: "vendor_payments",
		Username: "s2p", Password: "s2p", MaxOpenConnections: 8, MaxIdleConnections: 4}
	config.AppConfig = config.Config{}
	config.AppConfig.Db = d
	config.AppConfig.DbTest = d
	config.AppConfig.DbReplicaLive = d
	config.AppConfig.LoggerConfig = logconfig.Config{LogLevel: "info", SentryEnabled: false}
	config.AppConfig.Cache = cache.Config{Driver: "redis", Redis: cache.RedisConfig{Host: "redis", Port: 6379}}
	config.AppConfig.Api = rxclient.Config{Url: "http://boundary:8080", Secret: "local-vendor-payments-secret", Timeout: 10}
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
