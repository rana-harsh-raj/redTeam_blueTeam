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

m41-evidence-verify: ## rebuild + verify the evidence manifest from TRACKED evidence (no ignored globs)
	@python3 RED_LOOP/surface/m4_evidence_manifest.py build
	@python3 RED_LOOP/surface/m4_evidence_manifest.py verify

m41-clean-acceptance: m41-canonical-paths m41-evidence-verify ## full M4.1 clean-checkout acceptance chain (expect 74/74)
	@python3 RED_LOOP/surface/m4_acceptance.py --dry-run
