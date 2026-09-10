# SECRET_SCAN — cloned repositories, configs and fixtures (2026-09-04)

Tool: gitleaks (Homebrew, `gitleaks dir . --redact=100`) over 61 shallow clones (1.52 GB). Report kept redacted in the session scratchpad; no secret values were read or copied.

| Rule | Hits | Files | Non-test-looking files | Assessment |
|---|---|---|---|---|
| generic-api-key | 6,300 | many | `kube-manifests` values (633, mostly key *names*/placeholders), `api/environment/.env.defaults` (88), `.env.production` (27), `.env.beta` (24), `x/config.js` (26, public Splitz/experiment ids), `mozart/conf/env.stage.toml` (18), dashboard `.env.production` (14) | Mostly identifiers and dev defaults; `.env.production`/`.env.beta` and Mozart stage/func TOML treated as **possible real** |
| private-key | 223 | 73 | `api/environment/.env.defaults` (9), `.env.drone` (2), `mozart/conf/env.{automation,bvt,e2e,func,stage}.toml`, `fts/internal/providers/encryption/sign.go` (embedded test key), `dashboard/.../mkcert/localhost-key.pem` | Test/CI keys except Mozart stage/func — **quarantined** |
| jwt / jwt-base64 | 361 | 327 | 1 (`batch/src/main/resources/application-slit.properties`) | test tokens |
| stripe-access-token | 4 | 4 | 0 | fixtures |

Actions taken:
- Quarantined (moved out of the clone tree to a 0700 directory, files 0600, never opened): `api/environment/.env.production`, `api/environment/.env.beta`, `mozart/conf/env.stage.toml`, `mozart/conf/env.func.toml` (and prod TOMLs if present). These are exactly the files the safety gate forbids as arena config sources.
- All arena configuration is generated from scratch (`ENV2_COMPOSE/config/generate.py`); no `.env.*` or `env.*.toml` from the clones is copied.
- Types/locations only are reported here; nothing was posted to any external system.

Residual risk: `api/environment/.env.defaults` contains dev private keys and 88 key-shaped values; it was left in place because the API monolith lane needs it to understand config keys, and its content is dev-default by name. Recommend the owners rotate anything real found there.


## Addendum 2026-09-04 (final package scan)

`gitleaks dir ENV2_COMPOSE` on the finished package: 62 findings. 52 are the per-arena generated credentials in `secrets/` and the rendered `generated/` configs (runtime-only, bind-mounted read-only, shredded by `scripts/down.sh`, never copied into images) plus the arena RSA passport key. The remaining 10 are 8-character substrings flagged by entropy:

| File | Line | Key | Verdict |
|---|---|---|---|
| `config/templates/base/xbalances/arena.toml` | 43-44 | `Password1`/`Password2` of an arena-only x-balances inbound identity | synthetic placeholder (`arena_…`), not a production value |
| `seeds/s4/stork_subscriptions.json`, `seeds/stork/subscriptions.json` | 10, 24, 38 | `secret` of the three synthetic merchant webhooks | synthetic (`arena_stork_secret_m…`), used only for HMAC between stork-capture and the webhook sink |
| `config/templates/base/payouts/{default,arena}.toml` | 535 / 566 | `forDualWriteDirectPushToAPI` | a Splitz experiment **identifier** copied from `payouts/config/default.toml`, not a credential |

No real Razorpay hostname, no ARN outside the LocalStack account `000000000000`, and no credential of production shape was found in templates, seeds or substitutes. The real-looking inputs quarantined from the clones remain outside the package (first section).
