package cache

import (
	"bytes"
	"context"
	"testing"
	"time"
)

func toStringPtr(a string) *string {
	return &a
}

func toTimePtr(a time.Duration) *time.Duration {
	return &a
}

func handleErr(t *testing.T, err error, tcErr *string) bool {
	if err != nil {
		if tcErr == nil {
			t.Errorf("did not expect err, got err=%v", err)
			return false
		}
		if *tcErr != err.Error() {
			t.Errorf("expected err=%v, got err=%v", *tcErr, err)
			return false
		}
		return false // return false not not move ahead in case of error
	} else if tcErr != nil {
		t.Errorf("expected err=%v, got nil", *tcErr)
		return false
	}

	return true
}

func TestCache(t *testing.T) {
	tests := []struct {
		name             string
		config           *Config
		key              string
		setKey           string
		configErr        *string
		setErr           *string
		getErr           *string
		getBytesExpected []byte
		sleep            *time.Duration
	}{
		{
			name:             "cacheHitSet",
			config:           NewConfig(), // test new config with default config
			key:              "key",
			setKey:           "key",
			getBytesExpected: []byte{71, 111},
		},
		{
			name:             "testCacheHit",
			config:           NewConfig().WithEviction(5 * time.Microsecond).WithCleanWindow(time.Duration(1000) * time.Millisecond),
			key:              "key",
			setKey:           "key",
			getBytesExpected: []byte{71, 111},
			sleep:            toTimePtr(time.Duration(10) * time.Millisecond),
		},
		{
			name:             "testCacheMissDueToEviction",
			config:           NewConfig().WithEviction(5 * time.Microsecond).WithCleanWindow(time.Duration(10) * time.Millisecond),
			key:              "key",
			setKey:           "key",
			getErr:           toStringPtr("key not found in cache"),
			getBytesExpected: []byte{71, 111},
			sleep:            toTimePtr(time.Duration(1000) * time.Millisecond),
		},
		{
			name:             "testCacheMissDueToNewKey",
			config:           NewConfig().WithEviction(5 * time.Microsecond).WithCleanWindow(time.Duration(1000) * time.Millisecond),
			key:              "key",
			setKey:           "key2",
			getErr:           toStringPtr("key not found in cache"),
			getBytesExpected: []byte{71, 111},
			sleep:            toTimePtr(time.Duration(10) * time.Millisecond),
		},
	}

	for _, tc := range tests {
		tc := tc
		bigcache, err := New(context.Background(), tc.config)
		if !handleErr(t, err, tc.configErr) {
			continue
		}

		err = bigcache.Set(tc.setKey, tc.getBytesExpected)
		if !handleErr(t, err, tc.setErr) {
			continue
		}

		if tc.sleep != nil {
			time.Sleep(*tc.sleep)
		}

		got, err := bigcache.Get(tc.key)
		if !handleErr(t, err, tc.getErr) {
			continue
		}

		if !bytes.Equal(got, tc.getBytesExpected) {
			t.Errorf("bytes expected=%v, got=%v", tc.getBytesExpected, got)
		}
	}

}
