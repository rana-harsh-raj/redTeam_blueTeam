# workflow-engine in the Env 2 arena

`substitutes/workflow-engine` is the **default** Workflow/Cadence substitute for
the arena as of M6 (`.env.arena`: `ARENA_WORKFLOW_HOST=http://workflow-engine:8093`).

It is the same durable maker/checker engine that M5 reconstructed and validated
(`reports/implementation/m5-source-map.md`, `m5-runbook.md`, this directory's
`FIDELITY.md`, and the 12-scenario suite behind `make m5-scenarios`), wrapped in
a container (`arena_entrypoint.py`, `Dockerfile`) and pointed at the REAL
payouts-api.

What it adds over `substitutes/workflow-sim` (which remains defined and
selectable):

| | workflow-sim | workflow-engine |
|---|---|---|
| WorkflowAPI/Create → payout `pending` | yes | yes |
| terminal callback into real PS routes | yes | yes |
| approvers as real identities (org / actor / role / token) | no | **yes** |
| approval policy (N distinct approvals, maker≠checker, expiry) | no | **yes** |
| append-only audit trail | no | **yes** |
| durable + restart-recoverable | no (in-memory dict) | **yes** (SQLite on `wfengine-data`) |
| callback retry with stable Idempotency-Key | no | **yes** |
| `/_arena/{health,pending,decide}` control plane | yes | yes (compatible) |

To revert, set `ARENA_WORKFLOW_HOST` in `.env.arena` to
`http://workflow-sim:8092` (thin stand-in) or `http://127.0.0.1:1` (the frozen
pre-M2 dead address, i.e. workflows disabled).

---

## 1. Endpoints

Service name on `rzp-arena`: **`workflow-engine`**, port **8093**. It is on the
internal network only; there is no host port and no route through kong-lite.

### Service plane — what payouts calls

```
POST /twirp/rzp.workflows.workflow.v1.WorkflowAPI/Create
  Authorization: Basic workflow:workflow
```

The Basic pair is exactly payouts' rendered `[workflow.auth]`
(`config/templates/base/payouts/arena.toml:308-310` → `username = "workflow"`,
`password = "workflow"`; the compose block sets `WFE_SERVICE_USER` /
`WFE_SERVICE_PASS` to the same literals). A wrong or missing pair → `401`.

Response mirrors `CreateHttpResponse` (`pkg/workflow/workflow_create.go:119-135`,
read by payouts at `:217-220`):

```json
{"id":"wfl5751abcb2cf","config_id":"wcfbe488621a10","status":"created",
 "domain_status":"pending","entity_id":"pout_...","owner_id":"ARENAM...",...}
```

`id` and `config_id` are **14 characters** (`WFE_WORKFLOW_ID_LEN=14`), because
payouts stores them in `CHAR(14)` columns (`workflow_entity_map.workflow_id`,
`workflow_state_map.workflow_id`). A longer id would be truncated or rejected by
MySQL strict mode.

### Actor plane — what an approver calls

```
POST /v1/workflows/{id}/approve|reject|cancel     Authorization: Bearer <actor token>
GET  /v1/workflows/{id}                           Authorization: Bearer <actor token>
GET  /v1/workflows/{id}/audit                     Authorization: Bearer <actor token>
POST /v1/workflows                                Authorization: Bearer <actor token>  (requester role)
```

Optional body `{"version": N}` gives optimistic concurrency (a stale `N` → `409
stale_version`). All reads and writes are org-scoped: a token from another org
gets `403 cross_org`.

### Admin plane — provisioning + evidence

```
POST /admin/orgs      {"name":..., "org_id":...}                  Authorization: Bearer <admin token>
POST /admin/actors    {"org_id":..., "name":..., "roles":[...]}
POST /admin/policies  {"org_id":..., "entity_type":"payout", "required_approvals":N,
                       "separation":0|1, "eligible_approvers":[actor_id,...],
                       "expiry_seconds":S}
POST /admin/expire-sweep
GET  /admin/workflows
```

The admin token is read from `/run/secrets/wfe_admin_token`
(`WFE_ADMIN_TOKEN_FILE`), generated per-arena by `secrets/gen-secrets.sh` and
materialized into the `secrets-workflow` volume as uid-10001 mode-0400. **The
engine refuses to start without it** rather than serve an open admin plane.

Read the value on the host with `cat ENV2_COMPOSE/secrets/wfe_admin_token.txt`.
Never hand it to a campaign/attacker worker — actor tokens are the only thing a
workload identity ever receives. That separation is the property M5 established.

### Control / evidence plane — `/_arena/*`

`workflow-sim`-compatible, so existing tooling (e.g.
`RED_LOOP/surface/m2_surface_acceptance.py`) keeps working unchanged:

```
GET  /health, /ping, /_arena/health   -> {"status":"ok","service":"workflow-engine","pending":N}
GET  /_arena/pending                  -> {"count":N,"pending":[record,...]}
POST /_arena/decide {"payout_id":"pout_...","decision":"approve"|"reject",
                     "queue_if_low_balance": true|false}
GET  /_arena/workflows                -> every workflow (any state) + its callback outcome
```

`/_arena/decide` **bypasses actor/policy evaluation by design** — it is the
operator/verifier plane, exactly as in workflow-sim, and is never a merchant
capability (the attacker broker denies `/_arena/*`; see
`RED_LOOP/registry/known_gaps.yaml` `KG-SUB-WORKFLOW-DECIDE`). It moves the
workflow to its terminal state, writes an `arena_approve` / `arena_reject` audit
row, fires the real callback through the engine's durable delivery path, and
returns the delivery outcome synchronously (waits up to `WFE_ARENA_DECIDE_WAIT`,
default 12s).

Two intentional differences from workflow-sim's `/_arena/decide`:

* deciding a payout that has **no pending** workflow returns `404
  no_pending_workflow` (workflow-sim keeps decided records in its map and would
  happily re-fire the callback). A lazily-expired workflow returns `409
  terminal_state`.
* the `callback` object is `{ok,status_code,attempts,url,method,delivery_id,
  pending_delivery}` — it has `attempts`/`delivery_id` instead of workflow-sim's
  echoed `response` body, because delivery here is a durable retrying queue.

`/_arena/workflows` exists because `/_arena/pending` drops a workflow once it is
terminal; this is where a verifier confirms an approve/reject actually landed
without needing the admin token.

---

## 2. Making a fresh merchant workflow-applicable

Two things must be true. They are independent and both are required.

### 2a. PS side — the DCS `Workflows` config

Payouts decides whether a payout needs approval from the merchant's DCS
`Workflows` object. The real config-proto field names (there is **no** bare
`payout_workflows` field — see `seeds/dcs/merchants.json`'s `_legacy_name_note`
and `findings/26_dcs_splitz_governor_shield.md:80-117`) are, in
`rzp/x/merchant/payouts/Workflows`:

| field | meaning | value for a workflow-applicable merchant |
|---|---|---|
| `enable_payout_workflow` | master switch | **`true`** |
| `skip_approval_workflow_for_api` | API-created payouts bypass approval | **`false`** |
| `skip_workflow_for_dashboard` | dashboard-created payouts bypass | `false` |
| `skip_workflow_for_payroll` | payroll-created payouts bypass | (don't care) |
| `enable_approval_via_oauth` | OAuth approval path | `false` |

`substitutes/dcs-stub` also accepts the legacy aliases
(`payout_workflows` → `enable_payout_workflow`, `skip_workflow_for_api` →
`skip_approval_workflow_for_api`, `skip_wf_for_payroll` →
`skip_workflow_for_payroll`); see `substitutes/dcs-stub/CONTRACT.md:73-75`.

**Fixtures as shipped:**

* `ARENAM00000001` (M1, Shared/Lite) — `enable_payout_workflow: false`
* `ARENAM00000002` (M2, Direct/RBL) — `enable_payout_workflow: false`
* `ARENAM00000003` (M3) — `enable_payout_workflow: **true**`, but also
  `skip_approval_workflow_for_api: **true**` and
  `skip_workflow_for_payroll: true`. So **M3 as shipped does NOT produce a
  pending workflow for a payout created through the public API** — flip
  `skip_approval_workflow_for_api` to `false` first.

**A freshly provisioned merchant is NOT workflow-applicable.**
`RED_LOOP/red_loop/provisioner_direct.py::_register_dcs` (line 508) deep-copies
the `ARENAM00000002` block, which has `enable_payout_workflow: false`. To make
one workflow-applicable:

```bash
python3 - <<'PY'
import json, pathlib
p = pathlib.Path("ENV2_COMPOSE/seeds/dcs/merchants.json")
d = json.loads(p.read_text())
w = d["merchants"]["<MERCHANT_ID>"]["dcs"]["rzp/x/merchant/payouts/Workflows"]
w["enable_payout_workflow"] = True
w["skip_approval_workflow_for_api"] = False
lg = d["merchants"]["<MERCHANT_ID>"].get("legacy", {})
lg["payout_workflows"] = True
lg["skip_workflow_for_api"] = False
p.write_text(json.dumps(d, indent=1) + "\n")
PY
docker compose --env-file .env.arena -f docker-compose.yml restart dcs-stub
```

dcs-stub loads the seed file at boot, so the restart is required — this is the
same pattern `provisioner_direct.py` uses (it restarts `monolith-stub`,
`bankingaccounts-stub`, `dcs-stub`, `splitz-stub` after seeding). Payouts also
caches merchant config briefly; give it a few seconds, or restart `payouts-api`.

The Splitz experiment `payout_workflows_dcs_name_experiment`
(`Sg7vrXQkpK4hBA`, arena.toml:612) governs whether payouts reads the config via
the DCS *name*; it is already seeded in `seeds/splitz/experiments.json` and needs
no change.

### 2b. Engine side — the merchant needs an org and a policy

The real engine already knows every merchant and has a workflow config bound to
it by an ops flow. Nothing in the payouts `Create` call carries one, so a
brand-new arena merchant would otherwise have no org row (FK violation) and no
approval policy.

**Decision (arena default): auto-provision on first Create.**
`WFE_AUTOPROVISION_POLICY=1` (set in the compose block). The first `Create` for
an unknown `owner_id`:

1. creates the org — `identity.ensure_org()`, idempotent. `WFE_TENANT_KEY=owner_id`
   means **the engine's org IS the payouts merchant id**, so the org boundary the
   engine enforces is the merchant boundary. (Payouts sends `org_id` = the
   Razorpay org id, which *every* merchant shares — using that would put all
   merchants in one tenant and destroy cross-tenant isolation.)
2. binds the **default policy** for `entity_type="payout"` if none exists:

   | setting | default | env override |
   |---|---|---|
   | `required_approvals` | `1` | `WFE_DEFAULT_REQUIRED_APPROVALS` |
   | `separation` (maker ≠ checker) | `1` (enforced) | `WFE_DEFAULT_SEPARATION` |
   | `eligible_approvers` | `""` = **any actor in the org holding role `approver`** | — |
   | `expiry_seconds` | `86400` (24h) | `WFE_DEFAULT_EXPIRY_SECONDS` |

It is a real persisted `policies` row, so `POST /admin/policies` overrides it at
any time (e.g. to require two distinct approvals, or to pin an explicit eligible
set). Setting `WFE_AUTOPROVISION_POLICY=0` restores strict behaviour: an unknown
merchant still gets its org (the FK requires it) but falls back to the in-code
default `(1, separation on, any approver, 24h)` without persisting a row.

`WFE_AUTOPROVISION_POLICY` defaults to **off** in `arena_entrypoint.py`'s
library layer for the standalone M5 path — the arena compose block is what turns
it on, so `make m5-scenarios`, the blind benchmark and the M5 campaign are
bit-for-bit unaffected.

Actors are **not** auto-created. There is no approver until you make one (2c) —
which is correct: an approver is an identity, and identities are provisioned.

### 2c. Create the approver

```bash
ADMIN=$(cat ENV2_COMPOSE/secrets/wfe_admin_token.txt)
docker compose --env-file .env.arena -f docker-compose.yml exec -T workflow-engine \
  wget -qO- --header='Content-Type: application/json' \
  --header="Authorization: Bearer $ADMIN" \
  --post-data='{"org_id":"<MERCHANT_ID>","name":"checker-1","roles":["approver"]}' \
  http://127.0.0.1:8093/admin/actors
# -> {"actor_id":"act_...","org_id":"<MERCHANT_ID>","roles":["approver"],"token":"wtk_..."}
```

The token is returned **exactly once**, at creation; only its sha256 is stored.
Roles are drawn from `{requester, approver, operator}`. Give the maker and the
checker different actors — with `separation` on, the creator cannot approve its
own request (`403 separation_violation`).

---

## 3. The end-to-end journey

```
merchant --(POST /v1/payouts, kong-lite)--> payouts-api
payouts-api --(Twirp Create, Basic workflow:workflow)--> workflow-engine
             <-- {id: 14 chars, domain_status: pending}
payout row  -> status = pending          [PS side]
workflow    -> state  = pending          [engine side]

approver --(Bearer actor token, POST /v1/workflows/{id}/approve)--> workflow-engine
   policy evaluated: org, role, eligibility, maker!=checker, version, expiry
   -> state = approved, version++, audit row, approval row
workflow-engine --(Basic rzp_live:<auth_workflow_payouts>)--> payouts-api
   POST <callback_details....approved.url_path>   ({"queue_if_low_balance": ...})
   headers: x-creator-id, X-Razorpay-Account, Idempotency-Key: cbk_<wfid>_approve
   success = 200 | 201 | 409       (409 = already transitioned, idempotent-safe)
payout row -> leaves pending, processing continues
```

Callback targets are taken **verbatim** from what payouts itself put in the
create body's `callback_details.workflow_callbacks.processed.domain_status.
{approved,rejected}.{url_path,method,headers,payload}`
(`workflow_create.go` `getCallbackDetails` / `getCallbackHeaders`), joined onto
`PS_API_URL=http://payouts-api:9400`, exactly as workflow-sim does — so they hit
the real `payout_internal_routes.go` routes. If payouts supplied no `url_path`,
the engine falls back to `WfCallbackPath + entity_id + /approve|/reject`.

Callback identity is `rzp_live` + `/run/secrets/auth_workflow_payouts` — the same
generated secret payouts renders into its own `[auth.workflow]`, so both sides
share one value. Deliveries are persisted before the first attempt, retried with
backoff up to 5 attempts, and resumed on engine restart.

The operator shortcut for the same effect (no actor needed):

```bash
docker compose --env-file .env.arena -f docker-compose.yml exec -T workflow-engine \
  wget -qO- --header='Content-Type: application/json' \
  --post-data='{"payout_id":"pout_XXXXXXXXXXXXXX","decision":"approve"}' \
  http://127.0.0.1:8093/_arena/decide
```

---

## 4. Health and evidence

| what | where |
|---|---|
| liveness / readiness | `GET /health` (also the container `HEALTHCHECK`) — `{"status":"ok","pending":N}` |
| what is waiting | `GET /_arena/pending` |
| what happened, incl. callback delivery | `GET /_arena/workflows` |
| full state dump | `GET /admin/workflows` (admin token) |
| per-workflow audit trail | `GET /v1/workflows/{id}/audit` (actor token, org-scoped) |
| durable state on disk | `wfengine-data` volume, `/data/wfe.db` (+ `-wal`, `-shm`) |
| engine logs | `docker compose logs workflow-engine` |

Restart recovery: `docker compose restart workflow-engine` — pending workflows,
policies, actors, audit rows and *undelivered callbacks* all survive; the
callback worker re-queues everything with `delivered=0` on boot.

---

## 5. Safety properties preserved

* `rzp-arena` is `internal: true`; the engine has no egress and no host port.
* Runs as **uid 10001**, `read_only` root filesystem, `no-new-privileges`,
  tmpfs `/tmp`. `/data` is writable only because it is a named volume.
* No credential in the image or in `docker inspect`: the admin token and the
  callback password arrive only as files under `/run/secrets`, streamed into the
  `secrets-workflow` volume by `secrets/materialize.py` as uid-10001 mode-0400.
* `secrets-workflow` is deliberately **not** `secrets-kong`: the admin-plane
  token never lands on the ingress container's filesystem.
* Fails closed with no admin token.
* stdlib-only Python; no network access at build time.
