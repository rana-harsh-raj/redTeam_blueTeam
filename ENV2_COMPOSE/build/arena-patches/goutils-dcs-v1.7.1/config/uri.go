package config

import "fmt"

// URI will be using env to fetch the URL dynamically and all urls should be defined in dcs package
type URI int64

// UndefinedURI will be having value 0 and
// corresponding sequence number will be followed for other constants
const (
	UndefinedURI URI = iota
	StageTest
	StageLive
	ProdTest
	ProdLive
	DevTest
	DevLive
	FuncTest
	FuncLive
	BvtTest
	BvtLive
	PerfTest
	PerfLive
	DarkTest
	DarkLive
	AutomationTest
	AutomationLive
)

var urls = map[URI]string{
	ProdTest:       "https://dcs-test.int.razorpay.com",
	ProdLive:       "https://dcs-live.int.razorpay.com",
	DarkTest:       "https://dcs-test.int.razorpay.com",
	DarkLive:       "https://dcs-live.int.razorpay.com",
	StageTest:      "https://dcs-test.concierge.stage.razorpay.in",
	StageLive:      "https://dcs-live.concierge.stage.razorpay.in",
	DevTest:        "https://dcs-test.dev.razorpay.in",
	DevLive:        "https://dcs-live.dev.razorpay.in",
	FuncTest:       "https://dcs-test.concierge.func.razorpay.in",
	FuncLive:       "https://dcs-live.concierge.func.razorpay.in",
	BvtTest:        "https://dcs-test.dev.razorpay.in",
	BvtLive:        "https://dcs-live.dev.razorpay.in",
	PerfTest:       "https://dcs-test.int.perf.razorpay.in",
	PerfLive:       "https://dcs-live.int.perf.razorpay.in",
	AutomationTest: "https://dcs-test.concierge.qa.razorpay.in",
	AutomationLive: "https://dcs-live.concierge.qa.razorpay.in",
}

// any new env mapping should be added here
var envModeToURI = map[string]URI{
	"stage_test":        StageTest,
	"stage_live":        StageLive,
	"prod_test":         ProdTest,
	"prod_live":         ProdLive,
	"dev_test":          DevTest,
	"dev_live":          DevLive,
	"func_test":         FuncTest,
	"func_live":         FuncLive,
	"bvt_test":          BvtTest,
	"bvt_live":          BvtLive,
	"perf_test":         PerfTest,
	"perf_live":         PerfLive,
	"perf1_test":        PerfTest,
	"perf1_live":        PerfLive,
	"slit_test":         DevTest,
	"slit_live":         DevLive,
	"perf2_test":        PerfTest,
	"perf2_live":        PerfLive,
	"dark_test":         DarkTest,
	"dark_live":         DarkLive,
	"automation_test":   AutomationTest,
	"automation_live":   AutomationLive,
	"availability_test": DevTest,
	"availability_live": DevLive,
}

func (u URI) String() (string, error) {
	if url, ok := urls[u]; ok {
		return url, nil
	}

	return "", fmt.Errorf("%s", "undefined URI provided")
}

func URIFromEnvAndMode(env Env, mode Mode) (URI, error) {
	e, err := env.String()
	if err != nil {
		return UndefinedURI, err
	}
	m, err := mode.String()
	if err != nil {
		return UndefinedURI, err
	}
	s := fmt.Sprintf("%s_%s", e, m)
	if uri, ok := envModeToURI[s]; ok {
		return uri, nil
	}

	return UndefinedURI, fmt.Errorf("%s", "missing env or mode in the input, not a valid env")
}
