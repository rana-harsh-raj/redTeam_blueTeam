# Payouts Twin Explorer

A local, dependency-free web application for understanding the synthetic Payouts Twin. Python 3.10+ and a modern browser are sufficient. No analytics, CDN, remote font, private source body, or runtime credential is included in the frontend.

From the repository root:

```sh
python3 ARCHITECTURE_EXPLORER/manage.py install
python3 ARCHITECTURE_EXPLORER/server.py
python3 ARCHITECTURE_EXPLORER/manage.py build
```

Open [the Explorer](http://127.0.0.1:18765). The build command creates `ARCHITECTURE_EXPLORER/dist`; serve it with `python3 -m http.server 18766 --bind 127.0.0.1 --directory ARCHITECTURE_EXPLORER/dist`. The portable export supports saved exploration; live actions require `server.py` next to the configured arena checkout.

Select a component on the architecture map to inspect its ownership, connections, source references and production/local differences. Worker processes are grouped by parent and can be expanded. Follow a payout has nine selectable scenarios, step playback, before/after state, database changes, synthetic payloads and field explanations. The technical toggle opens detailed source and identifier panels. The state-machine view separates Payouts from FTS; the ownership view separates authoritative and copied state.

Illustrative flows in `data/architecture.json` are explicitly labelled generated projections. They are not captured runtime proof. In Local runs, the fixed Shared payout action authenticates through local Kong, calls the real core services, and checks the actual Payouts/FTS rows, balanced Ledger journals and signed merchant terminal webhook. The bounded runner saves sanitized HTTP/database traces and its result under `reports/implementation/runs/explorer-*`. A passing result is copied to `data/saved-run.json` for offline replay. A failed run remains visible and never overwrites the last passing saved trace.

The same fixed golden run can be executed with packet auditing:

```sh
python3 ARCHITECTURE_EXPLORER/run-golden.py --with-egress-audit
```

Before using live controls, complete the arena build and `ENV2_COMPOSE/scripts/up.sh`, and build the verifier using the Compose verify profile (or run `scripts/golden-run.sh`). The browser never receives merchant credentials. The Python server binds only loopback, validates Host and same-origin action headers, accepts no command parameters, serializes runs and removes its temporary credential file and runner container. The backend has local Docker access, so run it only for this local checkout. Ctrl+C stops the Explorer; `bash ENV2_COMPOSE/scripts/down.sh` tears down the arena and its generated secrets.

Screenshots are under `screenshots/`. Source references include repository, file/symbol, commit and confidence; source bodies remain in the approved local repositories. Rebuild the static export after a new captured run if it should be included there.
