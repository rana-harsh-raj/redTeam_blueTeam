package config

import "testing"

func TestURIFromEnvAndMode(t *testing.T) {
	type args struct {
		env  Env
		mode Mode
	}
	tests := []struct {
		name    string
		args    args
		want    URI
		wantErr bool
	}{
		{
			name: "success_stage_live",
			args: args{
				env:  Stage,
				mode: Live,
			},
			want:    StageLive,
			wantErr: false,
		},
		{
			name: "success_stage_test",
			args: args{
				env:  Stage,
				mode: Test,
			},
			want:    StageTest,
			wantErr: false,
		},
		{
			name: "success_prod_live",
			args: args{
				env:  Prod,
				mode: Live,
			},
			want:    ProdLive,
			wantErr: false,
		},
		{
			name: "success_prod_test",
			args: args{
				env:  Prod,
				mode: Test,
			},
			want:    ProdTest,
			wantErr: false,
		},
		{
			name: "failure_env",
			args: args{
				env:  UndefinedEnv,
				mode: Test,
			},
			want:    UndefinedURI,
			wantErr: true,
		},
		{
			name: "failure_mode",
			args: args{
				env:  Stage,
				mode: UndefinedMode,
			},
			want:    UndefinedURI,
			wantErr: true,
		},
		{
			name: "failure_mode_env",
			args: args{
				env:  UndefinedEnv,
				mode: UndefinedMode,
			},
			want:    UndefinedURI,
			wantErr: true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := URIFromEnvAndMode(tt.args.env, tt.args.mode)
			if (err != nil) != tt.wantErr {
				t.Errorf("URIFromEnvAndMode() error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			if got != tt.want {
				t.Errorf("URIFromEnvAndMode() got = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestURI_String(t *testing.T) {
	tests := []struct {
		name    string
		u       URI
		want    string
		wantErr bool
	}{
		{
			name:    "success_stage_live",
			u:       StageLive,
			want:    "https://dcs-live.concierge.stage.razorpay.in",
			wantErr: false,
		},
		{
			name:    "success_stage_test",
			u:       StageTest,
			want:    "https://dcs-test.concierge.stage.razorpay.in",
			wantErr: false,
		},
		{
			name:    "success_prod_test",
			u:       ProdTest,
			want:    "https://dcs-test.int.razorpay.com",
			wantErr: false,
		},
		{
			name:    "success_prod_live",
			u:       ProdLive,
			want:    "https://dcs-live.int.razorpay.com",
			wantErr: false,
		},
		{
			name:    "failure",
			u:       UndefinedURI,
			want:    "",
			wantErr: true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := tt.u.String()
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
