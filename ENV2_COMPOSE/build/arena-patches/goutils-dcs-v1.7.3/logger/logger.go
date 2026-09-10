package logger

import (
	"context"
	"fmt"
	"os"

	goutilslogger "github.com/razorpay/goutils/logger/v2"
)

// Logger is an interface for logging, it is used internally
// at present but has scope for external implementations
type Logger interface {
	Infof(ctx context.Context, format string, args ...interface{})
	Errorf(ctx context.Context, format string, args ...interface{})
	Fatalf(ctx context.Context, format string, args ...interface{})
	Debugf(ctx context.Context, format string, args ...interface{})
}

type defaultLogger struct {
	logger *goutilslogger.ZapLogger
}

// NewDefaultLogger will return default logger as zap logger,
// dcs.New(ctx) will initialize the DefaultLogger as logger
// This can be overridden by mentioning the option as dcs.New(ctx, dcs.WithLogger(log))
// This Internally uses go-utils zap logger library
func NewDefaultLogger(ctx context.Context) *defaultLogger {
	callerSkip := 4
	conf := goutilslogger.Config{
		LogLevel:      goutilslogger.Info,
		ContextString: "config",
		CallerSkip:    &callerSkip,
	}

	var err error
	// using the global, pkg scope zapLogger,
	lgr, err := goutilslogger.NewLogger(conf)
	if err != nil {
		fmt.Printf("failed to initialize logger\n")
		os.Exit(1)
	}

	return &defaultLogger{lgr}
}

// entry gets the logger if in the context else re-init the logger
// adds in the context and return the entry
func (z *defaultLogger) entry(ctx context.Context) *goutilslogger.Entry {
	entry, ok := ctx.Value(goutilslogger.LoggerCtxKey).(*goutilslogger.Entry)
	if ok {
		return entry
	}

	return z.logger.WithContext(ctx, nil)
}

// Infof prints log with format and args
func (z *defaultLogger) Infof(ctx context.Context, format string, args ...interface{}) {
	z.entry(ctx).Info(fmt.Sprintf(format, args...))
}

// Errorf print log with format and args
func (z *defaultLogger) Errorf(ctx context.Context, format string, args ...interface{}) {
	z.entry(ctx).Error(fmt.Sprintf(format, args...))
}

// Fatalf print log with format and args
func (z *defaultLogger) Fatalf(ctx context.Context, format string, args ...interface{}) {
	z.entry(ctx).Fatal(fmt.Sprintf(format, args...))
}

// Debugf print log with format and args
func (z *defaultLogger) Debugf(ctx context.Context, format string, args ...interface{}) {
	z.entry(ctx).Debug(fmt.Sprintf(format, args...))
}
