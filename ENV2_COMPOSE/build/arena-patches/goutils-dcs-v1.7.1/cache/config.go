package cache

import (
	"time"
)

var (
	// DefaultEviction is the default eviction time after which cache entries would be evicted
	DefaultEviction time.Duration = 15 * time.Second
	// DefaultMaxCacheSize is the default max cache size in mbs after which oldest entries would
	// be removed for the new ones.
	DefaultMaxCacheSize int = 8
	// DefaultCleanWindow is the default time before two cleanups
	DefaultCleanWindow time.Duration = 1 * time.Second
	// DefaultShards is the default shards for keys distribution
	DefaultShards int = 16
	// DefaultMaxEntriesInWindow is the max number of entries in the window
	// remaining request will reach DCS
	DefaultMaxEntriesInWindow int = 1000 * 10
)

// Config defines the cache config for dcs
type Config struct {
	// Eviction Time after which entry can be evicted
	// Default is 15 seconds
	Eviction time.Duration

	// MaxCacheSize is a limit for cache size in MB.
	// It can protect application from consuming all available memory on a pod,
	// therefore from running OOM Killer. When the limit is higher than 0 and
	// reached then the oldest entries are overridden for the new ones.
	// Default is 8Mi.
	MaxCacheSize int

	// Interval between removing expired entries (clean up).
	// If set to <= 0 then no action is performed, when in doubts use defaults
	CleanWindow time.Duration

	// Number of cache shards, value must be a power of two
	// This will distribute the keys into multiple buffer instances
	Shards int

	// Max number of entries in life window. Used only to calculate initial size for cache shards.
	// When proper value is set then additional memory allocation does not occur.
	MaxEntriesInWindow int
}

// NewConfig creates the default config for cache
// use With() functions to overwrite the default configurations
func NewConfig() *Config {
	return &Config{
		Eviction:           DefaultEviction,
		MaxCacheSize:       DefaultMaxCacheSize,
		CleanWindow:        DefaultCleanWindow,
		Shards:             DefaultShards,
		MaxEntriesInWindow: DefaultMaxEntriesInWindow,
	}
}

// WithEviction sets the eviction for the config
func (c *Config) WithEviction(eviction time.Duration) *Config {
	c.Eviction = eviction
	return c
}

// WithMaxCacheSize sets the max cache size after which keys would be rotated
func (c *Config) WithMaxCacheSize(size int) *Config {
	c.MaxCacheSize = size
	return c
}

// WithCleanWindow sets the max cache size after which keys would be rotated
func (c *Config) WithCleanWindow(window time.Duration) *Config {
	c.CleanWindow = window
	return c
}

// WithShards sets the no. of shards which keys will be stored
func (c *Config) WithShards(count int) *Config {
	c.Shards = count
	return c
}

// WithMaxEntriesInWindow sets the max entries stored in the cache
func (c *Config) WithMaxEntriesInWindow(count int) *Config {
	c.MaxEntriesInWindow = count
	return c
}
