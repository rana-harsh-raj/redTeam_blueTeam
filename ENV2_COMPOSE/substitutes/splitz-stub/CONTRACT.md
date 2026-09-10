# splitz-stub substitute contract

Substitutes Splitz's `EvaluateAPI` (Twirp JSON-over-HTTP). Grounded in
`findings/24_shared_libs_proto.md:339-361` (goutils/splitz client contract)
and `findings/26_dcs_splitz_governor_shield.md` section B (the 8 named
experiment ids in the BOM's Env2 flag matrix), plus a direct read of
`goutils/splitz/client.go` (endpoint constants, `EvaluateRequest`/response
struct field names) and `payouts/.agents/skills/pre-mortem/references/
services-splitz.md` (confirms `EvaluateRequest.Id` is always `merchantID`
at every payouts call site shown there).

## Endpoints

| Path | Purpose |
|---|---|
| `POST /twirp/rzp.splitz.evaluate.v1.EvaluateAPI/Evaluate` | single evaluate |
| `POST /twirp/rzp.splitz.evaluate.v1.EvaluateAPI/EvaluateBulk` | `{bulk_evaluate: [EvaluateRequest,...]}` → `{bulk_evaluate_response: [...]}` |
| `POST /twirp/rzp.splitz.evaluate.v1.EvaluateAPI/FetchDecisionContext` | stub: `{experiments: []}` |
| `POST /twirp/rzp.splitz.evaluate.v1.EvaluateAPI/AllowCors` | stub: `{}` |

Auth: HTTP Basic, `[splitz.auth]` in payouts config.

## Request / response

Request: `{id, experiment_id, experiment_name, request_data, track_impression}`
— `id` is the merchant id for every payouts call site. Response mirrors
`goutils/splitz/client.go`'s `evaluateResponse` struct field-for-field
(`id, project_id, experiment{id,name,exclusion_group_id,updated_at},
variant{id,name,variables:[{key,value}],experiment_id,weight,region},
Reason, steps`). `variant.variables` always carries exactly one
`{key:"result", value:"<variant_name>"}` entry, matching
`payouts/pkg/splitz/client.go:149-161`'s "on"/"off" variable convention.

## Resolution: fixture lookup, keyed by (experiment id or name) × merchant id

`seeds/splitz/experiments.json`, mounted at `/app/seed/experiments.json`
(`SPLITZ_SEED_FILE` env) — the 8 experiment ids named in
`findings/26_dcs_splitz_governor_shield.md`, each with a per-merchant
variant table for `ARENAM00000001/2/3`, plus a `_by_name_alias` block for
`fts_request_from_payouts_service` (id not confirmed in findings/26's
enumeration — seeded by name only). Lookup order: `experiment_id` in the
by-id table, else `experiment_name` in the by-name table (which also
includes every by-id experiment's own `name`), else `default_variant`
("on") with `Reason: "arena_default_variant_unknown_experiment"` so a test
can distinguish "matched a seeded experiment" from "fell through to
default" in the response body.

`SPLITZ_INJECT_LATENCY_MS` env (default 0) delays every Evaluate/
EvaluateBulk call — mirrors `shield-stub`'s latency-injection convention
for exercising payouts' own timeout/fallback handling
(`[splitz] timeout = 200` in payouts config).

## Known gap fixed in this pass

The route table has both `.../Evaluate` and `.../EvaluateBulk` — since the
former is a literal string-prefix of the latter, `_common/base_stub.py`'s
naive `path.startswith(prefix)` dispatch could match the wrong (shorter)
route depending on Python dict insertion order. Fixed generically in
`base_stub.py`'s `_dispatch()` (sorts candidate routes by prefix length,
longest first, before matching) rather than just reordering this one
file's `ROUTES` dict — the same latent bug would otherwise resurface in any
other stub with an overlapping-prefix route pair.

## Validated

`python3 -m py_compile`; ran `server.py` on port 18802, curled `Evaluate`
by id (M3/`CreateTransferMetaRollout` → `on`), by name
(M1/`fts_request_from_payouts_service` → `on`), an unknown experiment id
(→ default `on`, correct `Reason`), and `EvaluateBulk` with 2 requests
(M2/`Qnc6b4fg1Hi36k` → `on`, M2/`FireStatusUpdateKafka` → `off`) — confirmed
the bulk response now returns both results after the base_stub dispatch fix.
