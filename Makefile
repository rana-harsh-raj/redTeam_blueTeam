# Milestone 4 — one-command entry points.
#
# Every target delegates to scripts/m4.sh, which echoes what it does and calls
# the REAL project scripts. All targets are rerunnable. `make m4-clean` tears
# down only a DISPOSABLE instance and refuses to touch the live arena.
#
# Quick start:
#   make m4-acceptance        # evaluate all 74 gates (dry-run friendly)
#   make m4-evidence-verify   # build + verify the evidence manifest (G68/G69)
#
# Run order for a full acceptance (see reports/implementation/M4_RUNBOOK.md):
#   m4-boot -> m4-self-check -> m4-test -> m4-assurance-run -> m4-replay
#            -> m4-acceptance -> m4-evidence-verify

SHELL := /bin/bash
M4 := scripts/m4.sh

.PHONY: help m4 m4-boot m4-self-check m4-test m4-assurance-run m4-replay \
        m4-acceptance m4-evidence-verify m4-clean \
        m41-canonical-paths m41-clean-acceptance m41-evidence-verify

help m4:            ## show the M4 entry points
	@bash $(M4) help

m4-boot:            ## bring up / clean-boot the Env2 arena (ARGS=<instance|--live>)
	@bash $(M4) boot $(ARGS)

m4-self-check:      ## provision + self_check a fresh disposable Direct merchant (ARGS=<campaign>)
	@bash $(M4) self-check $(ARGS)

m4-test:            ## verifier golden-run + boundary suite + BAS/XAS journey drivers
	@bash $(M4) test $(ARGS)

m4-assurance-run:   ## 4-context autonomous assurance run (RED_LOOP/run.py contexts)
	@bash $(M4) assurance-run $(ARGS)

m4-replay:          ## clean-state replay driver (STUB; coordinator fills)
	@bash $(M4) replay $(ARGS)

m4-acceptance:      ## evaluate all 74 gates -> m4-direct-e2e-acceptance.json (ARGS=--dry-run ...)
	@bash $(M4) acceptance $(ARGS)

m4-evidence-verify: ## build + verify the M4 evidence manifest (G68/G69)
	@bash $(M4) evidence-verify

m4-clean:           ## safe teardown of a DISPOSABLE instance (ARGS=<instance-id>; never the live arena)
	@bash $(M4) clean $(ARGS)

# ---- M4.1 reproducible-evidence entry points -------------------------------
m41-canonical-paths: ## guard: every mandatory evidence path is tracked, not git-ignored
	@python3 RED_LOOP/surface/m41_canonical_paths.py

m41-evidence-verify: ## verify committed evidence manifest hashes (tracked evidence; no rebuild, no ignored globs)
	@python3 RED_LOOP/surface/m4_evidence_manifest.py verify

m41-evidence-rebuild: ## regenerate the manifest from tracked evidence, then verify (only when evidence changes)
	@python3 RED_LOOP/surface/m4_evidence_manifest.py build
	@python3 RED_LOOP/surface/m4_evidence_manifest.py verify

m41-clean-acceptance: m41-canonical-paths m41-evidence-verify ## full M4.1 clean-checkout acceptance chain (expect 74/74)
	@python3 RED_LOOP/surface/m4_acceptance.py --dry-run

# ---- M5 autonomous-discovery entry points ----------------------------------
.PHONY: m5-scenarios m5-benchmark m5-safety m5-campaign m5-acceptance m5-verify m5-full m5-report

m5-scenarios:      ## boot engine+sink and run all 12 workflow scenarios e2e
	@python3 RED_LOOP/m5/scenario_suite.py

m5-benchmark:      ## build+launch the blind benchmark and prove mutant isolation
	@python3 RED_LOOP/m5/benchmark/integrity_check.py

m5-safety:         ## produce the safety + egress attestation
	@python3 RED_LOOP/m5/safety_egress.py

m5-campaign:       ## run an autonomous campaign (ARGS=--rounds N --models a,b ...)
	@python3 RED_LOOP/m5/campaign/run_campaign.py $(ARGS)

m5-verify:         ## independently replay every accepted finding from evidence
	@python3 RED_LOOP/m5/verify/replay_findings.py

m5-acceptance:     ## evaluate all M5 gates against committed evidence (dry-run friendly)
	@python3 RED_LOOP/surface/m5_acceptance.py --dry-run

m5-full: m5-scenarios m5-benchmark m5-safety ## scenarios + benchmark integrity + safety, then acceptance
	@python3 RED_LOOP/surface/m5_acceptance.py --dry-run || true
	@echo "run 'make m5-campaign' for a fresh campaign, or m5-acceptance to verify canonical evidence"

# ---- M6 snapshot / refresh ----
# Domain snapshot, drift diff, blast-radius mapping, build recipes and the
# daily/weekly refresh runbook. See scripts/snapshot/README.md.
# Everything below is read-only unless you add --execute (ARGS=--execute).
.PHONY: m6-snapshot m6-snapshot-diff m6-affected m6-recipes m6-refresh-daily m6-refresh-weekly m6-snapshot-test

m6-snapshot:        ## capture reports/domain/snapshots/<UTC>.json (+ latest.json); runs without docker
	@python3 scripts/snapshot/capture.py $(ARGS)

m6-snapshot-diff:   ## diff two snapshots (ARGS="<old.json> <new.json>"); exit 3 == changed
	@python3 scripts/snapshot/diff.py $(ARGS)

m6-affected:        ## rebuild / rerun_journeys / regen_config for a diff (ARGS=<diff.json>)
	@python3 scripts/snapshot/affected.py $(ARGS)

m6-recipes:         ## write reports/domain/recipes/*.yaml + reports/domain/SNAPSHOT_MANIFEST.json
	@python3 scripts/snapshot/recipes.py $(ARGS)

m6-refresh-daily:   ## capture -> diff -> affected -> refresh plan (ARGS=--execute to run it)
	@python3 scripts/snapshot/refresh.py daily $(ARGS)

m6-refresh-weekly:  ## full re-pin plan: prepare-repos -> check-inputs -> build -> clean boot -> journeys -> acceptance (ARGS=--execute)
	@python3 scripts/snapshot/refresh.py weekly $(ARGS)

m6-snapshot-test:   ## unit tests for the snapshot / refresh tooling
	@python3 -m unittest discover -s scripts/snapshot/tests

# ---- M6 complete Payouts functional domain ----
.PHONY: m6-inventory m6-graph m6-journeys m6-clean-boot m6-acceptance m6-full
m6-inventory:      ## repository + deployment-artefact inventory and remaining-access manifest
	@python3 scripts/domain/inventory.py

m6-graph:          ## merge discovery parts (+ runtime overlay + executed journeys) into the functional graph
	@python3 scripts/domain/runtime_overlay.py || true
	@python3 scripts/domain/journeys_part.py || true
	@python3 scripts/domain/build_graph.py --strict
	@python3 scripts/domain/inventory.py

m6-journeys:       ## run the M6 business-journey suite against the live arena (ARGS=--only id,id | --family f)
	@python3 RED_LOOP/m6/journeys/run.py $(ARGS)

m6-clean-boot:     ## down -> empty volumes -> fresh secrets/config/seeds -> up -> journeys (ARGS passed to clean_boot.py)
	@python3 RED_LOOP/m6/clean_boot.py $(ARGS)

m6-acceptance:     ## evaluate the M6 gates against committed evidence (clean-checkout safe)
	@python3 RED_LOOP/surface/m6_acceptance.py

m6-full: m6-clean-boot m6-graph m6-acceptance ## the weekly full rebuild chain (after images are built)

# ---- M7 shared API-monolith ingress + Source-to-Pay integration ----
.PHONY: m7-contract m7-ingress-test m7-s2p-build m7-s2p-up m7-s2p-down m7-s2p-status m7-journeys m7-clean-boot m7-graph m7-refresh-demo m7-acceptance m7-hashes
m7-contract:       ## derive substitutes/api-ingress/contract/routes.json from the pinned api/payouts/vendor-payments sources
	@python3 scripts/m7/derive_contract.py
m7-ingress-test:   ## host contract tests of the shared ingress (fake PS upstream, no docker)
	@cd ENV2_COMPOSE/substitutes/api-ingress && python3 -m unittest test_contract -v 2>&1 | tail -3
m7-s2p-build:      ## pinned vendor-payments source -> runtime image (DOMAIN_REPLICAS/source_to_pay scripts)
	@python3 RED_LOOP/m7/s2p_stack.py build
m7-s2p-up:         ## start the Source-to-Pay stack inside the arena (compose overlay, api-ingress = boundary)
	@python3 RED_LOOP/m7/s2p_stack.py up
m7-s2p-down:       ## stop it (arena untouched)
	@python3 RED_LOOP/m7/s2p_stack.py down
m7-s2p-status:     ## containers / health / topics
	@python3 RED_LOOP/m7/s2p_stack.py status
m7-journeys:       ## the M7 journey families only (shared-ingress + cross-domain-s2p) against the live arena
	@python3 RED_LOOP/m6/journeys/run.py --family shared-ingress,cross-domain-s2p $(ARGS)
m7-clean-boot:     ## down -> empty volumes -> up (with the shared ingress) -> S2P stack -> FULL journey suite (M6 + M7)
	@python3 RED_LOOP/m7/clean_boot.py $(ARGS)
m7-graph:          ## regenerate the functional graph + the M7 canonical architecture snapshot from source
	@python3 scripts/domain/runtime_overlay.py || true
	@python3 scripts/domain/journeys_part.py || true
	@python3 scripts/m7/ingress_part.py
	@python3 scripts/m7/s2p_part.py
	@python3 scripts/domain/build_graph.py --strict
	@python3 scripts/domain/inventory.py
	@python3 scripts/m7/canonical_snapshot.py
m7-refresh-demo:   ## incremental refresh proof: an ingress contract change reruns only the affected journeys
	@python3 RED_LOOP/m7/refresh_demo.py $(ARGS)
m7-acceptance:     ## evaluate the 22 M7 hard gates against committed evidence
	@python3 scripts/m7/acceptance.py
m7-hashes:         ## bind every M7 runtime/report artifact into reports/implementation/M7_ARTIFACT_HASHES.json
	@python3 scripts/m7/hashes.py

# ---- M8: immutable architecture snapshot + query service (archkit; no docker) ----
.PHONY: m8-build m8-compile m8-recipes m8-import m8-verify m8-test m8-serve m8-bench m8-hashes m8-acceptance
m8-build:          ## compile snapshot + recipes + M7 import, set current (reports/architecture/snapshots/<id>)
	@python3 -m archkit build
m8-compile:        ## compile the ArchitectureSnapshot only
	@python3 -m archkit compile
m8-recipes:        ## normalized recipes for the 98 canonical services (current snapshot)
	@python3 -m archkit recipes
m8-import:         ## import the accepted M7 milestone into the current snapshot (projection, runtime, evidence, acceptance)
	@python3 -m archkit import-m7
m8-verify:         ## recompute the snapshot id and manifest hashes
	@python3 -m archkit verify
m8-test:           ## archkit unit tests (offline)
	@python3 -m unittest archkit.tests.test_archkit
m8-serve:          ## read-only loopback query service (ARGS=--port N)
	@python3 -m archkit serve $(ARGS)
m8-bench:          ## timing/memory measurements -> reports/implementation/m8-bench.json
	@python3 -m archkit bench > reports/implementation/m8-bench.json && cat reports/implementation/m8-bench.json
m8-hashes:         ## bind snapshot store + archkit + reports into reports/implementation/M8_ARTIFACT_HASHES.json
	@python3 -c "from archkit.acceptance import build_hashes; from archkit.store import SnapshotStore; s=SnapshotStore(); print(build_hashes(s, s.resolve('current'))['count'])"
m8-acceptance:     ## evaluate the 20 M8 gates -> reports/implementation/M8_ACCEPTANCE.json
	@python3 -m archkit acceptance
