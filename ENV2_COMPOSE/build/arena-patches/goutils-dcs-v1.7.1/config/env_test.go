package config

import "testing"

func TestEnv_String(t *testing.T) {
	tests := []struct {
		name    string
		e       Env
		want    string
		wantErr bool
	}{
		{
			name:    "success_stage",
			e:       Stage,
			want:    "stage",
			wantErr: false,
		},
		{
			name:    "success_prod",
			e:       Prod,
			want:    "prod",
			wantErr: false,
		},
		{
			name:    "failure",
			e:       UndefinedEnv,
			want:    "",
			wantErr: true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := tt.e.String()
			if (err != nil) != tt.wantErr {
				t.Errorf("String() error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			if got != tt.want {
				t.Errorf("String() got = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestGetEnv(t *testing.T) {
	type args struct {
		env string
	}
	tests := []struct {
		name    string
		args    args
		want    Env
		wantErr bool
	}{
		{
			name:    "success_stage",
			args:    args{env: "stage"},
			want:    Stage,
			wantErr: false,
		},
		{
			name:    "success_prod",
			args:    args{env: "prod"},
			want:    Prod,
			wantErr: false,
		},
		{
			name:    "failure",
			args:    args{env: "test"},
			want:    UndefinedEnv,
			wantErr: true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := GetEnv(tt.args.env)
			if (err != nil) != tt.wantErr {
				t.Errorf("GetEnv() error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			if got != tt.want {
				t.Errorf("GetEnv() got = %v, want %v", got, tt.want)
			}
		})
	}
}
