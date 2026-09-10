# 30. Env 2 substitutes — implementation log

Deterministic external-service substitutes for the Env 2 arena
(`ENV2_COMPOSE/substitutes/**`), implemented per the 12-item spec (dcs,
splitz, shield, monolith, pricing, asv, stork-capture, merchant-webhook-sink,
kong-lite, mozart, config templates, docs). Progress appended after each stub.

**Merchant ID correction (applies everywhere below):** the fixtures agent's
`env2-seeds/dcs_fixtures.json` established that arena merchant ids must be
`ARENAM00000001` / `ARENAM00000002` / `ARENAM00000003` (14 chars, matching
payouts/fts `merchant_id CHAR(14)` columns and the fixtures workstream's
`payouts.sql`/`fts.sql`/`ledger.sql`/`xbalances.sql`), NOT the task prompt's
illustrative 16-char `ARENAM0000000001..3`, and NOT `config/arena.yaml`'s
pre-existing `ARENA_M1/M2/M3` (which would silently mismatch the other
workstream's seeded rows). All seeds/stubs in this pass use
`ARENAM00000001/2/3`; `config/arena.yaml`'s `merchants:`/`dcs_flags:`/
`splitz_variants:` blocks are being updated to match (see below).

**Cross-cutting confirmed gap (DCS):** payouts' and CFA's `goutils/dcs`
clients both build with a single `Mode`, which makes `getLoginURI()` resolve
the login host from a hardcoded env→real-hostname map, **never** consulting
the `ServerURL` config key. There is no config-only way to point an
unpatched payouts-api/cfa-server binary at `dcs-stub`; both build spikes
(`findings/28_build_spike_payouts.md`, `findings/28_build_spike_cfa.md`)
independently hit this and used the same workaround (`[dcs] Env` set to an
unrecognized string so `dcs.New()` fails before any network call, DCS flags
resolve `false`, boot is otherwise unaffected). `dcs-stub` is still fully
spec-built; see its `CONTRACT.md` "Known, load-bearing gap" section for the
full grounding (goutils/dcs/dcs.go + config/uri.go code excerpts).

## Progress table (filled in as each stub lands)

| stub | endpoints implemented | fixture file | validated how |
|---|---|---|---|
| dcs-stub | `/v1/auth/login`, `/v1/kv/get`, `/v1/kv/evaluate`, `/v1/kv/put`, `/v1/kv/patch`, `/v1/kv/audit`, `/v1/kv/entities`, `/_arena/legacy/{id}` (stub-only) | `seeds/dcs/merchants.json` (DCS names + stub-only legacy alias table, both naming conventions per merchant) | `python3 -m py_compile`; ran `server.py` on port 18801, curled login/get/evaluate/legacy for all 3 merchants, round-tripped the base64 protobuf `value` bytes through the module's own `decode_message()` and confirmed exact field values (M3 Workflows: skip_workflow_for_payroll/skip_approval_workflow_for_api/enable_payout_workflow all true; M2 ApiInterface: payout_service_enabled/bene_name_in_payout/enable_beneficiary_name_in_response/payout_idem_key_required true; M2 PayoutModeConfig: allowed_upi_channels=[icici,axis]) |
| splitz-stub | `/twirp/rzp.splitz.evaluate.v1.EvaluateAPI/{Evaluate,EvaluateBulk,FetchDecisionContext,AllowCors}` | `seeds/splitz/experiments.json` (8 experiment ids x 3 merchants + by-name alias) | `python3 -m py_compile`; ran on port 18802, curled Evaluate by id/by name/unknown-id-fallback and EvaluateBulk (2 items); found+fixed a real prefix-match dispatch bug in shared `_common/base_stub.py` (Evaluate vs EvaluateBulk) generically |
| shield-stub | `POST /v1/rules/evaluate/payout` | `seeds/shield/rules.json` (account-suffix-9999 block, M1+vendor_payments block, M3 review, default allow) | `python3 -m py_compile`; ran on port 18803, curled all 4 scripted scenarios, all matched expected action/triggered_rules; `SHIELD_LATENCY_MS` env (renamed from prior `SHIELD_INJECT_LATENCY_MS` to match task spec) |
| monolith-stub | `GET /internal/merchants/{id}`, `POST /payouts_service/{fetch_pricing_info,deduct_credits,reverse_credits,source_update,status_details_source_update,mail_and_sms,dual_write,decrement_free_payouts}`, `GET /fund_accounts_internal/fa_{id}`, `POST /merchant/on_hold_slas_internal`, `GET /actor_info_internal/{id}` (unconfirmed route), `POST /users_internal` (unconfirmed route), `POST /update_fts_fund_transfer` (relay), `GET /_arena/log` | `seeds/monolith/{merchants,fund_accounts,misc}.json`, `seeds/pricing.json` | `python3 -m py_compile`; ran on ports 18804/18805, curled every route (merchant/pricing/free-payout-counter-increment/fund-account/on_hold_slas/actor_info/users_internal/dual_write/mail_and_sms/_arena/log); verified `update_fts_fund_transfer` relay both `PS_RELAY_MODE=relay` (real PATCH to a throwaway mock payouts-api on 127.0.0.1:19400, confirmed via 200 status + Basic-Auth header) and `=drop` (no outbound call). Entirely rewritten from a prior placeholder that served non-existent routes (`/v1/contacts`, `/v1/fund_accounts`, `/v1/merchants`) not found anywhere in findings/20's confirmed route table. |
| pricing-stub | `POST /fetch_pricing_info` (thin alias, NOT wired into any real client config -- payouts' `[ccSdk]` boots with `mock=true`, no host ever configured; monolith-stub's `fetch_pricing_info` is the real pricing source) | `seeds/pricing.json` (shared with monolith-stub) | `python3 -m py_compile`; ran on port 18806, curled fetch_pricing_info M2/NEFT, matches seeds/pricing.json |
| asv-stub | `POST /account/fetch` (documented placeholder -- payouts' real `account_service` client is `goutils/account-service`'s gRPC SDK, which dials unconditionally regardless of `Mock` and speaks HTTP/2+protobuf, not plain JSON; this stdlib JSON stub cannot serve that traffic at the wire-protocol level, confirmed by reading `goutils/account-service/account-service.go` NewClient + `payouts/pkg/account/client.go`) | none (static defaults via env) | `python3 -m py_compile`; ran on port 18807, curled /account/fetch |
| stork-capture | `/twirp/rzp.stork.webhook.v1.WebhookAPI/{Create,List,ProcessEvent}`, `/twirp/rzp.stork.{sms,email}.v1.*API/Send`, `/_captured/events`, `/_arena/events?merchant=` | `seeds/stork/subscriptions.json` | `python3 -m py_compile`; ran end-to-end against merchant-webhook-sink (ports 18808/18809): ProcessEvent delivered with a valid HMAC signature after fixing a real bug (was signing `event.payload` but delivering the full envelope `json.dumps(event)` as the HTTP body -- now both are the same `event.payload` string, matching payouts/pkg/stork/process_event.go's real construction); `STORK_REORDER=1` confirmed to deliver 2 back-to-back events out of order; `/data/events.jsonl` persistence confirmed |
| merchant-webhook-sink | `POST /webhook/{merchant}` (+ back-compat `/webhook`), `GET /_received/events`, `GET /_arena/deliveries` | none (pass-through sink) | `python3 -m py_compile`; validated end-to-end with stork-capture above; `/data/deliveries.jsonl` persistence confirmed |
| kong-lite | reverse proxy + auth for `/v1/payouts*`, `/v1/contacts*`, `/v1/fund_accounts*` (all -> payouts-api), `/twirp/*`, `/v1/fts`, `/v1/balances`, `/_arena/health` | `seeds/merchants.json` (key_id/secret-file per merchant) | `python3 -m py_compile` (server.py + new `_common/rsa_sign.py`); `rsa_sign.py`'s own self-test signs+verifies against the real generated `secrets/passport_{private,public}_key.txt` (verify_ok=True); ran kong-lite on port 18811 against a throwaway mock payouts-api, confirmed Basic-Auth verification (401 without creds, 200 with the real M1 secret), passport JWT minted+independently re-verified (claims match findings/21 §2.2(a) exactly), service `Authorization: Basic <cred.API>` header added, client's own Authorization stripped. New shared module `substitutes/_common/rsa_sign.py`: pure-stdlib RS256 signer (ASN.1 DER parser + PKCS#1v1.5 padding + modexp), no cryptography/pyjwt dependency added. |
| mozart-sim | `/{namespace}/{gateway}/{version}/{transfer_init,transfer_status,gateway_auth,gateway_session,beneficiary_register,beneficiary_verify,account_balance,create_otp}` -- now PRIMARY (real mozart-mock build confirmed blocked: `go build` on `../mozart` 404s on private module `github.com/razorpay/integrations-utils`) | `seeds/mozart_scenarios.json` (6 grounded scenarios cross-checked against real mozart/app/testdata fixtures + fts error_code.go) | `python3 -m py_compile`; ran on port 18812, curled all 6 grounded scenarios end-to-end (processed, insufficient-fund, pending-then-processed x2 polls, ambiguous CBS:188-with-UTR, duplicate-txn, success-then-returned x2 polls) -- every response byte-matches the fixture file |

## Root-agent cross-cutting requests (addressed alongside the substitutes work)

- `mozart-mock` moved to an opt-in `mozart-real` compose profile (not in `substitutes`/`core`); `mozart-sim` is now PRIMARY (`config/generate.py`'s `ARENA_MOZART_IMPL` default, `.env.arena`).
- `config/generate.py` now emits BOTH `generated/<svc>/default.toml` and `generated/<svc>/arena.toml` (identical content, both fully self-contained) for payouts/ledger/fts/cfa/xbalances, per the confirmed container convention (`APP_ENV=arena`, loads `default.toml` then `arena.toml` from a single mounted `/app/config/`). Documented as a deliberate simplification, not a real sanitized copy of each repo's own `config/default.toml` (out of this pass's scope) -- see `SERVICES` dict comment in `config/generate.py`.
- `generated/payouts/e2e.toml`: new `config/templates/payouts-e2e.frozen.toml` (verbatim snapshot of `payouts/config/e2e.toml`, needed only so `e2e/utils`'s `init()` doesn't panic at boot) + a regex sanitizer in `generate.py` (`_sanitize_e2e_hosts`) that neutralizes all 23 real `razorpay.{com,in,vpc}`/`amazonaws.com` hostnames in it -- confirmed via `preflight/preflight.py` passing on the rendered output.
- Every core-profile service: `working_dir: /app`, `user: appuser`, config mounted at `/app/config` (not `/config`), new `x-proxy-env` anchor (`WORKDIR=/app`, `APP_ENV=arena`, `HTTP_PROXY`/`HTTPS_PROXY` pointed at a closed local port, `NO_PROXY` listing every arena service name) merged into each service's `environment:`.
- Found and fixed a real bug while cross-checking against `build/build-host.sh` (which lists the actual binaries baked into the already-built images): `fts-web`/`fts-workers` were modeled as an nginx-fronted single binary dispatched via `command: ["web"/"worker"]` on port 80 -- `build-host.sh` confirms `fts-web`/`fts-worker` are each their own `go build ./cmd/{web,worker}` binary, no nginx, listening directly on port 8080 (`fts.toml.tmpl`'s `LISTEN_PORT`). Fixed the entrypoints, healthcheck port, and every downstream reference (`config/arena.yaml`'s `fts_web` port, `kong-lite`'s default route table + CONTRACT.md).
- `ledger-migrate`/`fts-migrate`: added `-dir` mounts (`LEDGER_REPO_MIGRATIONS_DIR`/`FTS_REPO_MIGRATIONS_DIR` in `.env.arena`, both placeholder paths -- `docker compose config -q` validates the YAML without them existing) and `TENANT=PG`/`LEDGER_ARENA_PG_PASSWORD` env for ledger, per `findings/28_build_spike_ledger.md`'s confirmed goose/TENANT/CI-materialization-step details.
- Validated: `docker compose --env-file .env.arena -f docker-compose.yml --profile datastores --profile substitutes [--profile core --profile verify --profile migrations [--profile mozart-real]] config -q` all exit 0; spot-checked `payouts-api`'s fully-resolved config (volumes/environment/user/working_dir) via `docker compose config` (no `-q`).

## Final validation sweep (end of pass)

```
bash secrets/gen-secrets.sh && python3 config/generate.py && python3 preflight/preflight.py   # PASS
find . -name "*.py" | xargs -n1 python3 -m py_compile                                          # clean
find . -name "*.sh" | xargs -n1 bash -n                                                        # clean
docker compose --env-file .env.arena -f docker-compose.yml \
  --profile datastores --profile substitutes [--profile core --profile verify --profile migrations [--profile mozart-real]] config -q  # all exit 0
python3 -c "import tomllib" over every generated/**/*.toml (12 files)                          # all parse
python3 -c "import json" over every seeds/**/*.json (12 files)                                 # all parse
```

## Assumptions (collected)

1. Arena merchant ids are `ARENAM00000001/2/3` (14 chars), not the task's illustrative 16-char form — matches the fixtures workstream's CHAR(14)-column-safe choice; `config/arena.yaml` updated to match.
2. `cred.API` inbound Basic-Auth username on payouts-api's `/v1/payouts` group is assumed `"api"` (new `[auth.api]` in `payouts.toml.tmpl`, secret `auth_api_payouts`) — the real production username for this exact credential slot was not independently confirmed.
3. `kong-lite` signs the passport JWT with `kid: "arena-passport-1"` under `[passport.arena]` — the existing scaffold's own convention (single arena-wide key, not separate `apiv1`/`edgev1` keys) — kept as-is rather than splitting into a two-key setup, since payouts' v3 handler only needs the `kid` in its config map to match what's minted.
4. `monolith-stub`'s `actor_info_internal`/`users_internal` routes are best-effort (not found in findings/20's confirmed route table) — documented, not silently presented as verified.
5. DCS is a confirmed dead path for the pristine payouts-api/cfa-server binaries (see CONTRACT.md); dcs-stub is still fully built to spec.
6. ASV (`account_service`) is a confirmed protocol-mismatch dead path (real client is gRPC); asv-stub stays a JSON placeholder.
7. `pricing-stub` is confirmed unused by any real client config (payouts' ccSdk boots `mock=true`, no host ever configured); kept as a documented thin alias reading the same fixture as monolith-stub.
8. `ledger-migrate`/`fts-migrate` `-dir` mount source paths (`LEDGER_REPO_MIGRATIONS_DIR`/`FTS_REPO_MIGRATIONS_DIR`) are left as `.env.arena` placeholders for whoever runs `scripts/up.sh` against a real repo clone tree to set — not resolved in this pass (no repo clone access needed for my own validation, which was all `docker compose config -q`, not `up`).
9. `generated/<svc>/default.toml` and `arena.toml` are rendered as byte-identical copies (both fully self-contained), not a real sanitized copy of each repo's own `config/default.toml` with deltas layered on top — flagged as a deliberate, documented simplification for whoever owns the 5-real-service config-loading track next.
