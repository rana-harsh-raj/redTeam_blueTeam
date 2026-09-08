# M8 runbook — archkit

Repository-only; runs from a clean checkout with Python 3.11+ and PyYAML. No docker, no clones, no secrets.

```sh
make m8-build        # compile -> reports/architecture/snapshots/<id>/snapshot.json, recipes/, imports/m7/, REGISTRY.json (current)
make m8-verify       # recompute the snapshot id + manifest
make m8-test         # 25 archkit tests (offline)
make m8-bench        # reports/implementation/m8-bench.json
make m8-hashes       # M8_ARTIFACT_HASHES.json over the store, archkit sources and reports
make m8-acceptance   # 20 gates -> reports/implementation/M8_ACCEPTANCE.json (exit 0 iff accepted)
make m8-serve ARGS="--port 18790"   # read-only loopback HTTP: GET /v1/latest/<capability>?k=v
```

Queries (CLI):

```sh
python3 -m archkit q                                     # list capabilities
python3 -m archkit q node id=svc:payouts-api
python3 -m archkit q service_card id=sub:api-ingress
python3 -m archkit q shortest_path a=identity:merchant-api-key b=table:payouts/payouts
python3 -m archkit q paths a=sub:api-ingress b=svc:fts-web max_hops=5 limit=5
python3 -m archkit q identity_reachability identity=identity:ingress-app-secret
python3 -m archkit q data_flow id=table:payouts/payouts
python3 -m archkit q fidelity_gaps population=p0_critical_kinds
python3 -m archkit q unknowns node=sub:batch-sim
python3 -m archkit q uncovered_trust_boundaries
python3 -m archkit q affected nodes=sub:api-ingress,svc:fts-web
python3 -m archkit q context_packet subject=family:cross-domain-s2p budget=4000
python3 -m archkit q compare other=<other snapshot id>
python3 -m archkit q s2p_detail id=state:s2p/vp.pay expand=true      # the only query that loads the 27,994-node detail graph
python3 -m archkit q recipe service=payouts-api
```

Library:

```python
from archkit.query import Query
q = Query()                      # current snapshot in reports/architecture/snapshots
q.service_card("svc:payouts-api")["items"][0]
```

Recompiling after an input change (parts, compose files, ingress contract, S2P patch, unknown sources): `make m8-build` writes a NEW snapshot id next to the old one and moves `current`; `python3 -m archkit q compare other=<old id>` and `q affected other=<old id>` explain the delta. Old snapshots are never modified or deleted.

Environment overrides: `ARCHKIT_REPO` (repository root), `ARCHKIT_STORE` (store directory) — used by the clean-checkout gate M8-14.

Rules kept: the M7 artifacts under `reports/architecture/M7_*` and `reports/implementation/M7_*` are read-only inputs (sha-bound in `imports/m7/projection.json`); the store is append-only; the query service is GET-only on 127.0.0.1.
