package config

import "fmt"

type Env int64

const (
	UndefinedEnv Env = iota
	Stage
	Prod
	Dev
	Func
	Bvt
	Automation
	Perf
	Perf1
	Slit
	Perf2
	Dark
	Availability
)

func (e Env) String() (string, error) {
	switch e {
	case Stage:
		return "stage", nil
	case Prod:
		return "prod", nil
	case Dev:
		return "dev", nil
	case Func:
		return "func", nil
	case Bvt:
		return "bvt", nil
	case Automation:
		return "automation", nil
	case Perf1:
		return "perf1", nil
	case Perf:
		return "perf", nil
	case Slit:
		return "slit", nil
	case Perf2:
		return "perf2", nil
	case Dark:
		return "dark", nil
	case Availability:
		return "availability", nil

	default:
		return "", fmt.Errorf("%s", "invalid env provided")
	}
}

func GetEnv(env string) (Env, error) {
	switch env {
	case "stage":
		return Stage, nil
	case "prod":
		return Prod, nil
	case "dev":
		return Dev, nil
	case "func":
		return Func, nil
	case "automation":
		return Automation, nil
	case "bvt":
		return Bvt, nil
	case "perf":
		return Perf, nil
	case "perf1":
		return Perf1, nil
	case "slit":
		return Slit, nil
	case "perf2":
		return Perf2, nil
	case "dark":
		return Dark, nil
	case "availability":
		return Availability, nil
	default:
		return UndefinedEnv, fmt.Errorf("%s", "invalid env provided")
	}
}
