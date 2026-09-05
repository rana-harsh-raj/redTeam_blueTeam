package config

// A Config provides service configuration for service clients. By default,
// all clients will use the config.DefaultConfig structure.
// Override default Config when it is sent in Client Options using dcs.WithConfig(cfg)
type Config struct {
	// UserCreds are basic auth credentials for dcs sever Login
	UserCreds *UserCredentials
	// Mock Enables mocking of dcs.Client functions
	Mock bool
	// Env are used only when same deployment serves test and live mode traffic
	Env Env
	// Modes are used only when same deployment serves test and live mode traffic
	Modes []Mode

	// ServerURL a dcs sever endpoint URI (hostname only) \n
	// Deprecated: ServerURL is deprecated instead
	// we will be selecting the url dynamically in sdk based on mode and env.
	ServerURL string
	// Mode represents the test or live env mode
	// Deprecated: Mode is deprecated instead add the mode in modes array
	Mode string
}

// UserCredentials will be used for authentication of clients,
// These credentials are used to get back a token for making main requests to the DCS Server
type UserCredentials struct {
	Username string
	Password string
}

// NewConfig returns a new Config pointer
// methods to set multiple configuration values inline without using pointers.
//
//	// Create config service client.
//	svc := dcs.New(ctx, WithConfig(dcs.NewConfig())
func NewConfig() *Config {
	return &Config{}
}

// WithServerURL sets a config with serverBaseUrl and returning
// a Config pointer.
// Deprecated: ServerURL is deprecated instead
// we will be selecting the url dynamically in sdk based on mode and env.
func (c *Config) WithServerURL(url string) *Config {
	c.ServerURL = url
	return c
}

// WithMock sets a config with Mock boolean seeting it will return mock client  and returning
// a Config pointer.
func (c *Config) WithMock(b bool) *Config {
	c.Mock = b
	return c
}

// WithEnv sets a config with app env value used for fetching the url accordingly.
// a Config pointer.
func (c *Config) WithEnv(e Env) *Config {
	c.Env = e
	return c
}

// WithMode sets a config with Mode boolean setting it will return test or live client and returning
// a Config pointer.
// Deprecated: Mode is deprecated instead add the mode in modes array
func (c *Config) WithMode(b string) *Config {
	c.Mode = b
	return c
}

// WithModes sets a config with all the modes that client support
func (c *Config) WithModes(b []Mode) *Config {
	c.Modes = b
	return c
}

// WithCredentials sets a config Credentials value returning a Config pointer
// for config server login.
func (c *Config) WithCredentials(creds *UserCredentials) *Config {
	c.UserCreds = creds
	return c
}

// DefaultConfig returns the default configuration with default UserCredentials
//
// Generally you shouldn't need to use this method directly, but
// is available if you need to set defaults for testing and development, since there are part of options.
func DefaultConfig() *Config {
	return NewConfig().
		WithServerURL("http://localhost:8081").
		WithMode("test")
}
