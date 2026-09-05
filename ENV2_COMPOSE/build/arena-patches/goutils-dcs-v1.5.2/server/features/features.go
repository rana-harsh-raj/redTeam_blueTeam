package features

type Features interface {
	Key(name string) DcsKey
	DcsFieldNameFromApiName(name ApiName) DcsName
	EnabledFeaturesForKeyFromResponse(key string, value []byte) []string
}

type DcsKey string

type ApiName string

type DcsName string

func (k DcsKey) String() string {
	return string(k)
}

func (k ApiName) String() string {
	return string(k)
}

func (k DcsName) String() string {
	return string(k)
}
