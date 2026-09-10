package credentials

import (
	"context"
	"sync"
	"time"
)

// A Value is the Config Server credentials value for individual credential fields.
type Value struct {
	// Server Session Token
	SessionToken string
	// Expiry will store the expiry duration returned by sever while login
	Expiry time.Duration
}

// A Expiry provides expiration logic to be used by credentials
// client to implement expiry functionality.
//
// The best method to use this struct is as an anonymous field within the
// client's struct.
//
// Example:
//
//	type loginer struct {
//	    Expiry
//	    ...
//	}
type Expiry struct {
	// The date/time when to expire on
	expiration time.Time

	// If set will be used by IsExpired to determine the current time.
	// Defaults to time.Now if CurrentTime is not set.  Available for testing
	// to be able to mock out the current time.
	CurrentTime func() time.Time
}

// SetExpiration sets the expiration IsExpired will check when called.
//
// If window is greater than 0 the expiration time will be reduced by the
// window value.
//
// Using a window is helpful to trigger credentials to expire sooner than
// the expiration time given to ensure no requests are made with expired
// tokens.
func (e *Expiry) SetExpiration(expiration time.Time, window time.Duration) {
	// Passed in expirations should have the monotonic clock values stripped.
	// This ensures time comparisons will be based on wall-time.
	e.expiration = expiration.Round(0)
	if window > 0 {
		e.expiration = e.expiration.Add(-window)
	}
}

// IsExpired returns if the credentials are expired.
func (e *Expiry) IsExpired() bool {
	curTime := e.CurrentTime
	if curTime == nil {
		curTime = time.Now
	}
	return e.expiration.Before(curTime())
}

// ExpiresAt returns the expiration time of the credential
func (e *Expiry) ExpiresAt() time.Time {
	return e.expiration
}

// A Credentials provides concurrency safe retrieval of Config Server credentials Value.
// Credentials will cache the credentials value until they expire. Once the value
// expires the next Get will attempt to retrieve valid credentials.
//
// Credentials is safe to use across multiple goroutines and will manage the
// synchronous state so the Providers do not need to implement their own
// synchronization.
//
// The first Credentials.Get() will always call Provider.Retrieve() to get the
// first instance of the credentials Value. All calls to Get() after that
// will return the cached credentials Value until IsExpired() returns true.
type Credentials struct {
	m        sync.RWMutex
	wg       sync.WaitGroup
	creds    Value
	provider Provider
	fetching bool
}

// NewCredentials returns a pointer to a new Credentials with the provider set.
func NewCredentials(provider Provider) *Credentials {
	c := &Credentials{
		provider: provider,
	}
	return c
}

// Expire expires the credentials and forces them to be retrieved on the
// next call to Get().
//
// This will override the Provider's expired state, and force Credentials
// to call the Provider's Retrieve().
func (c *Credentials) Expire() {
	c.m.Lock()
	defer c.m.Unlock()

	c.creds = Value{}
}

// IsExpired returns if the credentials are no longer valid, and need
// to be retrieved.
//
// If the Credentials were forced to be expired with Expire() this will
// reflect that override.
func (c *Credentials) IsExpired() bool {
	c.m.RLock()
	defer c.m.RUnlock()

	return c.isExpiredLocked(c.creds)
}

// isExpiredLocked helper method wrapping the definition of expired credentials.
func (c *Credentials) isExpiredLocked(creds interface{}) bool {
	if creds == nil || c.provider.IsExpired() {
		return true
	} else if val, ok := creds.(Value); !ok || (val.SessionToken == "") {
		return true
	}

	return false
}

// asyncIsExpired returns a channel of credentials Value. If the channel is
// closed the credentials are expired and credentials value are not empty.
func (c *Credentials) asyncIsExpired() <-chan Value {
	ch := make(chan Value, 1)
	go func() {
		c.m.RLock()
		defer c.m.RUnlock()

		if curCreds := c.creds; !c.isExpiredLocked(curCreds) {
			ch <- curCreds
		}

		close(ch)
	}()

	return ch
}

// Get returns the credentials value, or error if the credentials Value failed
// to be retrieved.
//
// Will return the cached credentials Value if it has not expired. If the
// credentials Value has expired the Provider's Retrieve() will be called
// to refresh the credentials.
//
// If Credentials.Expire() was called the credentials Value will be force
// expired, and the next call to Get() will cause them to be refreshed.
func (c *Credentials) Get() (Value, error) {
	return c.GetWithContext(context.Background())
}

// GetWithContext returns the credentials value, or error if the credentials
// Value failed to be retrieved. Will return early if the passed in context is
// canceled.
//
// Will return the cached credentials Value if it has not expired. If the
// credentials Value has expired the Provider's Retrieve() will be called
// to refresh the credentials.
//
// If Credentials.Expire() was called the credentials Value will be force
// expired, and the next call to Get() will cause them to be refreshed.
func (c *Credentials) GetWithContext(ctx context.Context) (Value, error) {
	// Check if credentials are cached, and not expired.
	select {
	case curCreds, ok := <-c.asyncIsExpired():
		// ok will only be true, of the credentials were not expired. ok will
		// be false and have no value if the credentials are expired.
		if ok {
			return curCreds, nil
		}
	case <-ctx.Done():
		return Value{}, ctx.Err()
	}

	// Cannot pass context down to the actual retrieve, because the first
	// context would cancel the whole group when there is no direct
	// association of items in the group.
	return c.Do(func() (Value, error) {
		return c.singleRetrieve(context.Background())
	})
}

func (c *Credentials) singleRetrieve(ctx context.Context) (Value, error) {
	c.m.Lock()
	defer c.m.Unlock()

	if curCreds := c.creds; !c.isExpiredLocked(curCreds) {
		return curCreds, nil
	}

	var creds Value
	var err error
	creds, err = c.provider.Retrieve(ctx)
	if err == nil {
		c.creds = creds
	}

	return creds, err
}

// Do executes and returns the results of the given function, making
// sure that only one execution is in-flight for a given key at a
// time. If a duplicate comes in, the duplicate caller waits for the
// original to complete and receives the same results.
// The return value shared indicates whether v was given to multiple callers.
func (c *Credentials) Do(fn func() (Value, error)) (v Value, err error) {
	c.m.Lock()

	if c.fetching {
		c.m.Unlock()
		c.wg.Wait()
		return c.creds, nil
	}

	c.wg.Add(1)
	c.fetching = true
	c.m.Unlock()

	value, err := fn() // only single thread can call this function

	c.m.Lock()
	c.creds = value
	c.fetching = false
	c.m.Unlock()

	c.wg.Done()

	return value, err
}

// A Provider is the interface for any component which will provide credentials
// Value. A provider is required to manage its own Expired state, and what to
// be expired means.
//
// The Provider should not need to implement its own mutexes, because
// that will be managed by Credentials.
type Provider interface {
	// Retrieve returns nil if it successfully retrieved the value.
	// Error is returned if the value were not obtainable, or empty.
	Retrieve(ctx context.Context) (Value, error)

	// IsExpired returns if the credentials are no longer valid, and need
	// to be retrieved.
	IsExpired() bool
}
