package config

import "testing"

func TestGetMode(t *testing.T) {
	type args struct {
		mode string
	}
	tests := []struct {
		name    string
		args    args
		want    Mode
		wantErr bool
	}{
		{
			name:    "success_test",
			args:    args{mode: "test"},
			want:    Test,
			wantErr: false,
		},
		{
			name:    "success_live",
			args:    args{mode: "live"},
			want:    Live,
			wantErr: false,
		},
		{
			name:    "failure",
			args:    args{mode: "error"},
			want:    UndefinedMode,
			wantErr: true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := GetMode(tt.args.mode)
			if (err != nil) != tt.wantErr {
				t.Errorf("GetMode() error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			if got != tt.want {
				t.Errorf("GetMode() got = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestMode_String(t *testing.T) {
	tests := []struct {
		name    string
		m       Mode
		want    string
		wantErr bool
	}{
		{
			name:    "success_test",
			m:       Test,
			want:    "test",
			wantErr: false,
		},
		{
			name:    "success_live",
			m:       Live,
			want:    "live",
			wantErr: false,
		},
		{
			name:    "failure",
			m:       UndefinedMode,
			want:    "",
			wantErr: true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := tt.m.String()
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
