package httpclient

// RequestType will define the type of request that we are sending to the server.
type RequestType int

const (
	// PUT will store the details
	PUT RequestType = iota
	// GET will get the attributes
	GET
	// PATCH will update the attributes
	PATCH
	// LOGIN will login the user and fetches token
	LOGIN
	// GETAUDITHISTORY will return the audit history of changes
	GETAUDITHISTORY
)

// URI will return the uri for the specific request Type
func (r RequestType) URI() string {
	switch r {
	case PUT:
		return "v1/kv/put"
	case GET:
		return "v1/kv/get"
	case PATCH:
		return "v1/kv/patch"
	case LOGIN:
		return "v1/auth/login"
	case GETAUDITHISTORY:
		return "v1/kv/audit"
	}
	return ""
}

// SetAuthorizationHeader will set the authorization header based on RequestType
func (r RequestType) SetAuthorizationHeader(requester *Requester, token string) {
	switch r {
	case PUT, GET, PATCH, GETAUDITHISTORY:
		requester.SetBearerToken(token)
	case LOGIN: // no authorization for login
	}
}
