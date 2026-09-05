# Arena build patches (BUILD phase only)

`goutils-dcs-<version>/` are verbatim copies of `github.com/razorpay/goutils/dcs` from the Go module cache with ONE
change in `dcs.go`: `GetContextUrl` returns `os.Getenv("ARENA_DCS_URL")` when the request context carries no URL
(see the `ARENA PATCH` comment). Reason: the SDK derives its host from a hardcoded env->hostname map for every call
whose context lacks the override, so pristine binaries dial a real host; in the arena every DCS call must reach
`dcs-stub`. With `ARENA_DCS_URL` unset the behaviour is unchanged.

`build/apply-arena-patches.sh <repos-root>` (run before `build/build-host.sh`) adds the `replace` directive to the
payouts/cfa/x-balances copies' `go.mod` (idempotent) and applies the two source patches: the `ARENA_DCS_URL`
context override in each service's DCS client wrapper, and the `ARENA_STORK_JSON` twirp-JSON client in payouts'
stork client. Never run it against the pristine clones.
