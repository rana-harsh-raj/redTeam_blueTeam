# M11 Runbook — Critical Trust-Path Fidelity Upgrade

Branch `milestone-11-trust-path-fidelity` (from tag `campaign-m10-control-plane`). Everything runs on this machine
(no remote compute was needed or available); the `controlplane` package is untouched.

## What the real variant runs

```
merchant (host bridge :<kong_host_port>)
  -> edge-kong            razorpay/edge @ da9ff5b on kong:3.4.2-ubuntu, kong.conf verbatim, Postgres; route table derived from
                          terraform-kong @ 8ffe965b (prod-api authenticated_paths + payouts-ext by production host)
  -> api-ingress          CONTRACT-FAITHFUL monolith replacement (M7) -- now verifies the gateway passport (kid edgev2) and serves /jwks
  -> payouts-api          real (unchanged)
       -> shield-web      razorpay/shield @ 514516f, APP_MODE=rzpxprod, MySQL + Redis cluster (single node)
       -> banking-accounts-api  razorpay/banking-accounts, APP_ENV=arena, MySQL; DCS client -> dcs-stub over TLS (twin CA)
       -> workflows-api / workflows-worker / cadence   razorpay/workflows (Twirp API + approval Cadence worker), MySQL
```
The substitute variant (`--trust-path substitute`) keeps kong-lite / shield-stub / bankingaccounts-stub / workflow-engine
as the fallback and comparison profile.

## Build the promoted services (host Go toolchain, credential-free)

```
export REPOS_ROOT=/Users/rana.singh/rzp-payouts-clones OUT=/tmp/m11-stage
bash ENV2_COMPOSE/build/m11/build-shield.sh            # -modfile stub for the unreadable fingerprint-sdk (build-time only)
bash ENV2_COMPOSE/build/m11/build-banking-accounts.sh
bash ENV2_COMPOSE/build/m11/build-workflows.sh
docker build -f ENV2_COMPOSE/build/m11/edge-kong.Dockerfile -t rzp-arena/edge-kong:v1-candidate $REPOS_ROOT/edge
bash ENV2_COMPOSE/build/m11/package.sh                  # runtime-only images rzp-arena/{shield,banking-accounts,workflows}:v1-candidate
python3 -m twinfactory images export --from unix://$HOME/.colima/default/docker.sock --refs rzp-arena/edge-kong:v1-candidate,...
```
Images are content-addressed in `~/.twin-factory/images` (image id + build fingerprint of the compose build context; a
changed substitute source is rebuilt, never served stale from the cache).

## Derive the gateway configuration (no invented values)

```
make m11-kong-config      # terraform-kong -> ENV2_COMPOSE/trustpath/edge/kong-config.json (routes, plugins, hosts_in_production, not_provisioned)
make m11-part             # graph part reports/domain/parts/m11-trust-path.json
python3 -m archkit build  # snapshot + recipes (edge-kong, shield-web, banking-accounts-api, workflows-*) + profiles
```

## Create, boot, exercise a twin

```
python3 -m twinfactory create m11-real --profile critical-payouts --seed <seed> --trust-path real   # real is the default
python3 -m twinfactory build m11-real
python3 -m twinfactory start m11-real            # --resume-from <stage> re-enters a failed boot at that stage
python3 -m twinfactory health m11-real
python3 -m twinfactory journeys m11-real --family trust-path     # the 14 M11 journeys
python3 -m twinfactory journeys m11-real                         # the canonical suite (~35 min)
python3 -m twinfactory create m11-sub --profile critical-payouts --seed <seed> --trust-path substitute   # fallback / comparison
```
Boot stages in the real variant: materialize -> datastores -> migrations -> seed -> substitutes -> trust_path
(edge-kong migrations bootstrap/up/finish, Shield goose migrations under APP_MODE=default, banking-accounts goose,
workflows goose, Cadence domain registration; services up + health; Kong provisioning (services/routes/plugins,
anonymous consumer, one consumer + basic-auth-x credential per merchant key); Shield rules (rule API, skip_canary);
BAS businesses/banking_accounts rows; one payout-approval Config per merchant) -> core -> post_core -> bridge.

Fresh merchants provisioned by the journey pool get the same per-merchant state (RED_LOOP/red_loop/provisioner.py
register_kong_key / register_trust_path_merchant): Kong consumer + credential, Shield rules, BAS rows.

## Differential, resources, acceptance, report

```
make m11-differential REAL=<real run dir> SUB=<substitute run dir>   # -> M11_DIFFERENTIAL.md, m11/differential.json (0 OPEN required)
make m11-resources ID=m11-real LABEL="after canonical suite"          # -> m11/resources-m11-real.json
make m11-monolith-attempt                                             # -> m11/monolith-boot-attempt.{json,md}
make m11-clean-checkout                                               # clone HEAD -> temp dir -> create/build/start/smoke -> m11/clean-checkout.json
make m11-acceptance                                                   # -> M11_ACCEPTANCE.json (17 gates, machine-computed)
make m11-report && make m11-hashes                                    # -> M11_FINAL_REPORT.md, M11_ARTIFACT_HASHES.json
```

## Secrets

Every credential is generated per instance by `ENV2_COMPOSE/secrets/gen-secrets.sh` (+ `gen-tls.sh` for the twin CA and the
DCS hostnames' certificate) into `ENV2_COMPOSE/secrets/*.txt` (git-ignored) and materialized into named volumes owned by uid
10001 mode 0400. Never run gen-secrets in the main checkout while the M7 arena is up (it rotates that arena's secrets).
`python3 secrets/materialize.py --only config-workflows` re-materializes one group after a config change (stop that service first).

## Known real-component behaviours (see M11_FINAL_REPORT.md findings)

- F-M11-1 razorpay/workflows: an unconfigured `[auth.<service>]` slot admits anonymous callers -- the twin fills `[auth.vendorPayments]`.
- F-M11-2 edge basic-auth-x on Kong 3.4.2: a deleted credential keeps authenticating (cache keys never invalidated; DAO cache_key crashes on CRUD events).
- F-M11-3 razorpay/workflows ConfigAPI/List without `config` panics (500).
- F-M11-4 payouts-ext -> Payouts service: the merchant credential is refused by the service BasicAuth that precedes passport auth (PU-M11-7).

## Restore the M7 arena afterwards

```
python3 -m twinfactory stop m11-real --boundary ; python3 -m twinfactory stop m11-sub --boundary
colima start default
```
