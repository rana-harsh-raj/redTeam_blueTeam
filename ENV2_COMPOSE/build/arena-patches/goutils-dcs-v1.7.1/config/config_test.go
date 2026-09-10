package config

import (
	"reflect"
	"testing"
)

func TestConfig_WithCredentials(t *testing.T) {
	//cfg := DefaultConfig()
	type fields struct {
		ServerURL string
		UserCreds *UserCredentials
		Mock      bool
		Env       Env
		Modes     []Mode
		Mode      string
	}
	type args struct {
		creds *UserCredentials
	}
	tests := []struct {
		name   string
		fields fields
		args   args
		want   *Config
	}{
		{
			name:   "success_backward_compatable_test",
			fields: fields{},
			args: args{creds: &UserCredentials{
				Username: "testing",
				Password: "testing",
			}},
			want: &Config{
				UserCreds: &UserCredentials{
					Username: "testing",
					Password: "testing",
				},
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := &Config{
				ServerURL: tt.fields.ServerURL,
				UserCreds: tt.fields.UserCreds,
				Mock:      tt.fields.Mock,
				Env:       tt.fields.Env,
				Modes:     tt.fields.Modes,
				Mode:      tt.fields.Mode,
			}
			if got := c.WithCredentials(tt.args.creds); !reflect.DeepEqual(got, tt.want) {
				t.Errorf("WithCredentials() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestConfig_WithEnv(t *testing.T) {
	type fields struct {
		ServerURL string
		UserCreds *UserCredentials
		Mock      bool
		Env       Env
		Modes     []Mode
		Mode      string
	}
	type args struct {
		e Env
	}
	tests := []struct {
		name   string
		fields fields
		args   args
		want   *Config
	}{
		{
			name:   "success",
			fields: fields{},
			args:   args{e: Stage},
			want: &Config{
				Env: Stage,
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := &Config{
				ServerURL: tt.fields.ServerURL,
				UserCreds: tt.fields.UserCreds,
				Mock:      tt.fields.Mock,
				Env:       tt.fields.Env,
				Modes:     tt.fields.Modes,
				Mode:      tt.fields.Mode,
			}
			if got := c.WithEnv(tt.args.e); !reflect.DeepEqual(got, tt.want) {
				t.Errorf("WithEnv() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestConfig_WithMock(t *testing.T) {
	type fields struct {
		ServerURL string
		UserCreds *UserCredentials
		Mock      bool
		Env       Env
		Modes     []Mode
		Mode      string
	}
	type args struct {
		b bool
	}
	tests := []struct {
		name   string
		fields fields
		args   args
		want   *Config
	}{
		{
			name:   "success",
			fields: fields{},
			args:   args{b: true},
			want: &Config{
				Mock: true,
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := &Config{
				ServerURL: tt.fields.ServerURL,
				UserCreds: tt.fields.UserCreds,
				Mock:      tt.fields.Mock,
				Env:       tt.fields.Env,
				Modes:     tt.fields.Modes,
				Mode:      tt.fields.Mode,
			}
			if got := c.WithMock(tt.args.b); !reflect.DeepEqual(got, tt.want) {
				t.Errorf("WithMock() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestConfig_WithMode(t *testing.T) {
	type fields struct {
		ServerURL string
		UserCreds *UserCredentials
		Mock      bool
		Env       Env
		Modes     []Mode
		Mode      string
	}
	type args struct {
		b string
	}
	tests := []struct {
		name   string
		fields fields
		args   args
		want   *Config
	}{
		{
			name:   "success",
			fields: fields{},
			args:   args{b: "test"},
			want: &Config{
				Mode: "test",
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := &Config{
				ServerURL: tt.fields.ServerURL,
				UserCreds: tt.fields.UserCreds,
				Mock:      tt.fields.Mock,
				Env:       tt.fields.Env,
				Modes:     tt.fields.Modes,
				Mode:      tt.fields.Mode,
			}
			if got := c.WithMode(tt.args.b); !reflect.DeepEqual(got, tt.want) {
				t.Errorf("WithMode() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestConfig_WithModes(t *testing.T) {
	type fields struct {
		ServerURL string
		UserCreds *UserCredentials
		Mock      bool
		Env       Env
		Modes     []Mode
		Mode      string
	}
	type args struct {
		b []Mode
	}
	tests := []struct {
		name   string
		fields fields
		args   args
		want   *Config
	}{
		{
			name:   "success",
			fields: fields{},
			args:   args{b: []Mode{Live, Test}},
			want: &Config{
				Modes: []Mode{Live, Test}},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := &Config{
				ServerURL: tt.fields.ServerURL,
				UserCreds: tt.fields.UserCreds,
				Mock:      tt.fields.Mock,
				Env:       tt.fields.Env,
				Modes:     tt.fields.Modes,
				Mode:      tt.fields.Mode,
			}
			if got := c.WithModes(tt.args.b); !reflect.DeepEqual(got, tt.want) {
				t.Errorf("WithModes() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestConfig_WithServerURL(t *testing.T) {
	type fields struct {
		ServerURL string
		UserCreds *UserCredentials
		Mock      bool
		Env       Env
		Modes     []Mode
		Mode      string
	}
	type args struct {
		url string
	}
	tests := []struct {
		name   string
		fields fields
		args   args
		want   *Config
	}{
		{
			name:   "success",
			fields: fields{},
			args:   args{url: "https://dcs-test.int.stage.razorpay.in"},
			want: &Config{
				ServerURL: "https://dcs-test.int.stage.razorpay.in",
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := &Config{
				ServerURL: tt.fields.ServerURL,
				UserCreds: tt.fields.UserCreds,
				Mock:      tt.fields.Mock,
				Env:       tt.fields.Env,
				Modes:     tt.fields.Modes,
				Mode:      tt.fields.Mode,
			}
			if got := c.WithServerURL(tt.args.url); !reflect.DeepEqual(got, tt.want) {
				t.Errorf("WithServerURL() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestDefaultConfig(t *testing.T) {
	tests := []struct {
		name string
		want *Config
	}{
		{
			name: "success",
			want: &Config{
				ServerURL: "http://localhost:8081",
				Mock:      false,
				Env:       0,
				Modes:     nil,
				Mode:      "test",
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := DefaultConfig(); !reflect.DeepEqual(got, tt.want) {
				t.Errorf("DefaultConfig() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestNewConfig(t *testing.T) {
	tests := []struct {
		name string
		want *Config
	}{
		{
			name: "success",
			want: &Config{},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := NewConfig(); !reflect.DeepEqual(got, tt.want) {
				t.Errorf("NewConfig() = %v, want %v", got, tt.want)
			}
		})
	}
}
