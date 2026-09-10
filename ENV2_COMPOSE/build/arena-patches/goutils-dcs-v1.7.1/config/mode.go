package config

import "fmt"

type Mode int64

const (
	UndefinedMode Mode = iota
	Live
	Test
)

func (m Mode) String() (string, error) {
	switch m {
	case Live:
		return "live", nil
	case Test:
		return "test", nil
	default:
		return "", fmt.Errorf("%s", "invalid mode provided")
	}
}

func GetMode(mode string) (Mode, error) {
	switch mode {
	case "live":
		return Live, nil
	case "test":
		return Test, nil
	default:
		return UndefinedMode, fmt.Errorf("%s", "invalid mode provided")
	}
}
