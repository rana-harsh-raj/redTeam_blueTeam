# M7 runbook — shared API-monolith ingress + Source-to-Pay integration

Branch `milestone-7-shared-ingress-integration`. Everything below runs from the repository root on the same
host that runs the arena (colima Docker, ~12.5 GiB VM). Times are from the canonical run on 2026-09-08.

## Prerequisites

- Pinned clones at `.local/repos-root` (`/Users/rana.singh/rzp-payouts-clones`) and the Source-to-Pay durable
  source root `/Users/rana.singh/.rzp-architecture-replica/repos` (`DOMAIN_REPLICAS/source_to_pay/source-lock.json`).
- Arena core images built (`ENV2_COMPOSE/build/build-host.sh all`, unchanged from M6) — the substitutes
  (including `api-ingress`) are built by compose.
- Go 1.26.6, Python 3, gitleaks, `curlimages/curl:latest` pulled (the journey transport).

## One-shot reproduction (weekly clean rebuild)

```sh
make m7-contract          # derive substitutes/api-ingress/contract/routes.json from the pinned sources (deterministic)
make m7-ingress-test      # 13 host contract tests of the ingress (fake PS upstream)
make m7-s2p-build         # pinned vendor-payments -> runtime image (~2.5 min); writes DOMAIN_REPLICAS/source_to_pay/.build/runtime-image.env
make m7-clean-boot        # down -> empty volumes+secrets -> up (arena + api-ingress) -> S2P stack -> FULL journey suite (M6 + M7)
make m7-graph             # functional graph + inventory + canonical snapshot from source
python3 RED_LOOP/m7/s2p_three_runs.py   # three clean connected runs (gate 13)
python3 RED_LOOP/m7/reset_proof.py      # reset proof (gate 16)
make m7-refresh-demo ARGS=--execute     # daily incremental refresh proof (gate 17)
python3 RED_LOOP/m7/weekly.py           # records the weekly rebuild evidence from the clean boot + graph run (gate 18)
python3 scripts/m7/secret_scan.py       # gitleaks over M7 sources/reports (gate 20)
python3 scripts/m7/s2p_post_integration.py --run   # 3 S2P clean runs + acceptance from a fresh worktree of this branch (gate 5)
make m7-hashes && make m7-acceptance    # bind artifacts, evaluate the 22 gates -> reports/implementation/M7_ACCEPTANCE.json
```

`make m7-clean-boot` writes `reports/implementation/m6-clean-boot.json`, `m6-journeys.json` (all families) and
`m7-clean-boot.json`. Run long steps with `nohup ... &` and poll: the interactive harness kills long foreground
jobs.

## Day-to-day

| Task | Command |
|---|---|
| Boot without journeys | `python3 RED_LOOP/m7/clean_boot.py --no-journeys` |
| Only the M7 families | `make m7-journeys` (`ARGS=--only journey:shared-ingress/success,...`) |
| S2P stack up / down / status | `make m7-s2p-up` / `make m7-s2p-down` / `make m7-s2p-status` |
| Ingress health / contract / evidence | `docker run --rm --network rzp-arena curlimages/curl -s http://api-ingress:8080/_ingress/health` (`/_ingress/contract`, `/_ingress/evidence` with `-u admin:$(cat ENV2_COMPOSE/secrets/ingress_admin_token.txt)`) |
| Reset the ingress's mutable state | `curl -u admin:<token> -X POST http://api-ingress:8080/_ingress/reset` (seed ownership kept) |
| Rebuild only the ingress after a code change | `cd ENV2_COMPOSE && S2P_SOURCE_IMAGE=$(cut -d= -f2 ../DOMAIN_REPLICAS/source_to_pay/.build/runtime-image.env) docker compose --env-file .env.arena -f docker-compose.yml -f docker-compose.s2p.yml --profile datastores --profile substitutes --profile core --profile s2p up -d --build api-ingress` (the overlay keeps the `boundary` alias) |
| Restore the M6 batch hop | `ARENA_BATCH_UPSTREAM=direct` in the environment of `scripts/up.sh` |

## Identities the ingress recognises (all generated per boot by `secrets/gen-secrets.sh`)

| Context | Credential | Where |
|---|---|---|
| merchant | `rzp_live_<key>:<secret>` | `seeds/generated/merchants.json` + `secrets/merchant-keys/` (via the secrets-kong volume) |
| dashboard user | `dashboard:<session token>` from `POST /_ingress/session {merchant_id,user_id}` (admin) | ingress SQLite |
| internal app | `rzp_live:<app secret>` + `X-Razorpay-Account` | `secrets/app_{vendor_payments,xpayroll,batch,workflows,merchant_dashboard}.txt` (secrets-ingress volume; `app_batch` also in secrets-kong for batch-sim) |
| payouts-service | `rzp_live:<auth_monolith_shared>` | payouts `[api.auth]` |
| admin | `admin:<ingress_admin_token>` | `secrets/ingress_admin_token.txt` |

`app_vendor_payments` is fixed to the Source-to-Pay replica's synthetic constant `local-vendor-payments-secret`
(`runtime/source-driver/boot.go`); every other value is fresh random per boot.

## Evidence map

| Artifact | Produced by | Bound in |
|---|---|---|
| `reports/implementation/m6-journeys.json` + `RED_LOOP/runs/<run>/evidence/*.json` | `RED_LOOP/m6/journeys/run.py` | M7_ARTIFACT_HASHES.json |
| `m6-clean-boot.json`, `m7-clean-boot.json` | clean boots | idem |
| `m7-s2p-connected-runs.json`, `m7-reset-proof.json`, `m7-refresh-demo.json`, `m7-weekly-rebuild.json`, `m7-secret-scan.json`, `m7-s2p-post-integration-acceptance.json` | the scripts above | idem |
| `reports/architecture/M7_CANONICAL_SNAPSHOT.json` (+ `_STATS.json`) | `scripts/m7/canonical_snapshot.py` | idem |
| `reports/source-to-pay-replica/ACCEPTANCE_CLOSURE.md`, `closure-m7/artifacts/*`, `historical-b6e4976/artifacts/*` | Stage A | idem |
| `reports/implementation/M7_ACCEPTANCE.json` | `scripts/m7/acceptance.py` | self (excluded from the manifest) |

## Source-to-Pay standalone acceptance from this branch (gate 5)

`scripts/m7/s2p_post_integration.py --run` creates a fresh worktree of the current commit, captures an isolation
baseline with `scripts/m7/isolation_baseline.py` (running containers only, executing checkouts excluded), commits
it on a throwaway branch, runs `DOMAIN_REPLICAS/source_to_pay/scripts/clean-run.py` three times (the third from a
second fresh worktree), scans deliverables and runs `acceptance.sh`, then copies `acceptance.json` to
`reports/implementation/m7-s2p-post-integration-acceptance.json`. It uses its own compose projects (`s2p_m7post*`)
and never touches the arena.

## Teardown

`ENV2_COMPOSE/scripts/down.sh` removes every arena container, volume (including `rzp-arena-ingress-data`,
`rzp-arena-secrets-ingress`, `rzp-arena-s2p-mysql-data`) and generated secret; `make m7-s2p-down` removes only the
S2P services.
