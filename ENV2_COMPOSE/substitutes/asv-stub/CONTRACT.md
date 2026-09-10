# asv-stub substitute contract

## Confirmed protocol-level gap: this stub CANNOT serve the pristine payouts binary's real ASV traffic

`payouts/pkg/account/client.go` (`NewClient`) builds
`goutils/account-service`'s SDK with `WithServerURL(ascfg.Host)` +
`WithCredentials(...)` and **never calls `.WithMock(...)`** — so the SDK's
own `Mock` config field (`goutils/account-service/config/config.go`) stays
at whatever `config.NewConfig()`'s zero-value is; payouts has no code path
that sets it `true`. Even if it did, `Client.NewClient()`
(`goutils/account-service/account-service.go:42-72`) calls
`c.grpcClient.EstablishConnectionWithConfig(...)` **unconditionally at
construction**, independent of `Mock` — this is a real
`google.golang.org/grpc` connection (`grpc_client.GrpcClient`,
`accountv1.NewAccountServiceClient(...)`), not a plain HTTP/JSON client.
`findings/28_build_spike_payouts.md`'s own boot-dependency table confirms
the dial is **non-blocking** (`grpc.DialContext` with `WithBlock()`
commented out) — so boot succeeds either way, but any *call* payouts
actually makes through this client is real gRPC (HTTP/2 + protobuf
framing), which a stdlib `http.server` JSON stub **cannot** answer as a
peer (wrong wire protocol entirely, not just a schema mismatch).

**Net effect**: `[account_service] Mock=true` in config would have zero
effect even if added (payouts' wrapper never reads it), and this stub is
therefore a **documented placeholder only** — useful for manual JSON/curl
inspection of "what ASV would answer," not reachable by an unpatched
payouts-api's real `Account()`/`Write()`/`Filter()` gRPC calls. This is the
ASV analogue of `dcs-stub`'s documented gap (see its `CONTRACT.md`), for a
different underlying reason (wrong wire protocol vs. wrong URL
resolution).

## What the stub does

`POST /account/fetch` → `{merchant_id}` in, `{activated, hold_funds,
account_id, status}` out — `ACTIVATED_DEFAULT`/`HOLD_FUNDS_DEFAULT` env
vars (default `true`/`false`) control the fixture-wide default; no
per-merchant seed table since real callers can't reach this stub anyway
(see gap above) — not worth building fixture depth a real client will
never exercise.

## `banking_account_service` — a DIFFERENT client, NOT this stub

Payouts also has `[banking_account_service]` → `pkg/bankingAccountService`
(`payouts/internal/provider/banking_account_service_client.go`), a
**plain HTTP/JSON** client (confirmed: `GetBasTestConfig()` in
`provider_test` uses a bare `http://0.0.0.0:28080/v1` host, `Auth{Key,
Secret}` Basic-Auth) — this is a distinct config section/client from
`[account_service]`/`AccountServiceSdkClient` above, despite the similar
name, and IS a normal HTTP client this arena could stub faithfully. It is
out of this pass's explicit 12-item scope (not named in the task) and not
wired to `asv-stub` or any other substitute in `config/templates/payouts.toml.tmpl` —
flagged here as a follow-up if a future Env picks up FAV/BAS-recon flows
that need it.

## Validated

`python3 -m py_compile`; ran `server.py` on port 18807, curled
`/account/fetch` — returns the expected default JSON shape.
