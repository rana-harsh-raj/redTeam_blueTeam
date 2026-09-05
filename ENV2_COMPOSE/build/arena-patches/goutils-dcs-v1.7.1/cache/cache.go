package cache

import (
	"context"
	"errors"

	"github.com/allegro/bigcache/v3"
)

var (
	// ErrKeyNotFound in cachez
	ErrKeyNotFound = errors.New("key not found in cache")
)

// Cache stores the in memory cache for all dcs queries
// Cache has 15 second TTL by default and has the default hard cache size limit of 8Mbs
type Cache struct {
	bigcache *bigcache.BigCache
}

// New gives back the cache instance
func New(ctx context.Context, config *Config) (*Cache, error) {
	if config == nil {
		config = NewConfig()
	}

	bigcacheConfig := bigcache.DefaultConfig(config.Eviction)
	bigcacheConfig.HardMaxCacheSize = config.MaxCacheSize
	bigcacheConfig.CleanWindow = config.CleanWindow
	bigcacheConfig.Shards = config.Shards
	bigcacheConfig.MaxEntriesInWindow = config.MaxEntriesInWindow
	bc, err := bigcache.New(ctx, bigcacheConfig)
	if err != nil {
		return nil, err
	}

	return &Cache{
		bigcache: bc,
	}, nil
}

// Get reads value for the key.
// It returns an ErrKeyNotFound when
// no value exists for the given key.
func (c *Cache) Get(key string) ([]byte, error) {
	b, err := c.bigcache.Get(key)
	if err != nil && err == bigcache.ErrEntryNotFound {
		return b, ErrKeyNotFound
	}

	return b, err
}

// Set sets the key with a value
func (c *Cache) Set(key string, value []byte) error {
	return c.bigcache.Set(key, value)
}
