# Milestone 4 — Baseline and Git Integrity Report (T01)

> **DO NOT EDIT.** This report is immutable evidence produced by task T01
> (Baseline and Git Integrity Auditor) on 2026-09-06. Corrections belong in a
> new dated addendum file, never in this one.

## Verdict

**BASELINE_HOLDS.**

- Every hash-bound M3.1 artifact recomputes identically (9/9 evidence files,
  manifest file hash = companion `.sha256` = acceptance `manifest_sha256`,
  manifest self-hash, and all 1917 manifest entries: 0 missing, 0 mismatch).
- The M3.1 acceptance evaluator re-run on the current tree reproduces 20/21
  gates identically. The single flipped gate (`historical_integrity`) fails
  only because the evaluator hardcodes the three M3.1 branch names; its commit
  checks (`twin-v1.0`, `milestone-2-red-loop`) still pass. This is an
  evaluator-scope limitation, not drift (details in section 4).
- One empty-volume clean boot of the frozen baseline on this host produced
  **26 / 0 / 0** with **0 outside packets** (coverage proven, 38,123 control
  packets), matching both M3.1 clean boots.
- The live arena was stopped, preserved, and restored with identical network
  IDs, volumes and container IDs.

## 1. Git identity

| Item | Value |
|---|---|
| Base tag | `red-loop-m3.1` — annotated tag object `2f11824f1605deef15febc8294def7d322b41132` → commit **`3a044f83ed0eda6ead71585f07c450af2ed7978f`** (matches the required base) |
| Base is ancestor of HEAD | yes (`git merge-base --is-ancestor`) |
| Branch | `milestone-4-direct-reconciliation` |
| HEAD at task start (baseline capture, evaluator re-run) | `4d78f8693bb0854257fd2e9c302021dbe334eba3` (M4 scaffold) |
| **Tested commit** (HEAD at clean-boot time, recorded in the boot fingerprint `git_head`) | **`3371d82031509a74ddc202624852c8cfe978a11c`** ("M4: mandatory gate catalog (74 gates) + decisions D-004/D-005") |
| Commits since base | `4d78f86`, `3371d82` — together they touch only `RED_LOOP/m4/gate-catalog.json`, `reports/implementation/m4-*.md` (no ENV2_COMPOSE, TWIN_SPEC, verifier, or M3.1 artifact changes; `git diff --name-only 3a044f8 HEAD -- ENV2_COMPOSE TWIN_SPEC` is empty) |
| `git status --porcelain` | empty at start; empty again after the clean boot (only git-ignored `RED_LOOP/runs/` and `.local/` changed) |
| `twin-v1.0` | annotated tag `d3f9a16e26aee401c377fa299b1b0a49fa9a012b` → commit `78def24eb57112c0dc39a6ae9062b1f0d1c711fb` |

Note on HEAD movement: the coordinator committed `3371d82` on this branch
while the clean boot was running. The boot fingerprint therefore records
`git_head = 3371d82`; the baseline capture (section 5) records `4d78f86`. The
diff between them is documentation/catalog only, so both are the same runtime
tree; the ENV2_COMPOSE `config_digest` is unchanged across them.

All commits in the M3.1 acceptance `git` block resolve and are ancestors of HEAD:

| Name | Commit | Resolves | Ancestor of HEAD |
|---|---|---|---|
| m3.1 acceptance head | `aca25d23aa7e62918a6ab50c08d68e82a26be862` | commit | yes |
| twin_v1_commit | `78def24eb57112c0dc39a6ae9062b1f0d1c711fb` | commit | yes |
| runtime_freeze_commit | `219ca48bbb5e51db9347b335ac0a50241656045b` | commit | yes |
| m2_head (`milestone-2-red-loop`) | `a107612ed11344a8096e74dbf68616679855465e` | commit | yes |
| m3_head (`milestone-3-gateway-hardening`) | `3873e96ff9b8d510059bbccb9ab3553bf5c5c671` | commit | yes |
| live-arena boot commit (`.runtime/arena-fingerprint.json`) | `39fe41e54d7ee7063ebdbac546334023b1aea5d5` | commit | yes |

Observation: `m3-1-acceptance.json` records head `aca25d2`, one commit before
the tagged `3a044f8` ("M3.1 READY"); the tag commit is the one that committed
the artifact. This is consistent with the artifact being generated before the
final commit and is not a mismatch.

## 2. Hash recompute

Method: SHA-256 over file bytes (1 MiB chunks), identical to
`m3_1_acceptance.py::sha256_file` and `build_manifest.py::sha256_file`.

### 2.1 `m3-1-acceptance.json` → `evidence_sha256` (9 files): **9 match, 0 mismatch**

| File | SHA-256 (recorded = recomputed) |
|---|---|
| m31-fresh-merchant.json | `f1e20100a795418861eda34e1316cc7166119394de032308da84dd3fcfa96c42` |
| m31-fresh-merchant-noreuse.json | `07b5c85166b683f4f494135f6de6b2ba361507c8806564ff0919e96b7d86cce8` |
| m31-hardening-retention.json | `5ae609e1926904ff4e23739e09b45d10136493d2f563795aa6e431b031392d1f` |
| m31-v17.json | `898ae8d1ac663b178ab1ff120bed7a7245ec5254814a2d5da4a61718aebbc54a` |
| m31-calibration-freshid.json | `95b416fad7b09f63d2eaaf2a01033748c1154c73d9f6a51a7053dd9e33f03855` |
| m31-calibration-crossprovider.json | `fdfc05bc2d498494276f9ed9e41f896b98fbcef389b4e08553042eba1c62addb` |
| m31-harness-tests.json | `3460e43bcdfadea15ce518c3e05d0924751c7844d6fd927554fab3c484a41531` |
| m31-campaign-isolation.json | `091356b91c80e3dc57ef9ff714badf409243f7c3f219b5546fb7570f901dbd10` |
| m31-logical-replay.json | `4805a5281f117a33aca7fac67048890fab9cf6ed8136d5d58099a85cabe888b0` |

### 2.2 Evidence manifest (`RED_LOOP/archive/evidence-manifest.json`)

Scheme (from `build_manifest.py`): `self_sha256` = SHA-256 of the canonical
JSON (`sort_keys=True`, separators `(",", ":")`) with `self_sha256` blanked to
`""`; the companion `.sha256` is the SHA-256 of the written file bytes.

| Check | Result |
|---|---|
| File SHA-256 | `266756e4a8363ac2a60bef1d6cd4925d68ed93e8eb4f51a8926e9e27ee2a7daf` |
| Companion `evidence-manifest.json.sha256` | `266756e4…2a7daf  evidence-manifest.json` — **match** |
| Acceptance gate `evidence_archive_manifest.manifest_sha256` | `266756e4…2a7daf` — **match** |
| `self_sha256` recorded | `504b777dcb005823c8b36d9c3bfebb4db2f740cbf52a1d2fccab7020f03b6b73` |
| `self_sha256` recomputed | `504b777d…3b6b73` — **match** |
| Artifact entries verified (all, not a sample) | **1917 / 1917** match on SHA-256 and byte size; 0 missing; 0 mismatch |
| `missing_expected_committed` | `[]` |

Manifest observation (pre-existing, unchanged): `git.worktree_clean=false`
was recorded at generation time, and 47 `missing_expected_campaign_files`
are listed (the `calibfx-*` fixture campaigns and clean-boot dirs do not carry
`manifest.json`/`model_calls.jsonl`/`events.jsonl`). Neither is a gate input.

## 3. Historical evidence directories

`RED_LOOP/runs/cleanboot-m31-clean-a`, `cleanboot-m31-clean-b`,
`cleanboot-m31-clean-a-pollutionproof`, all 27 campaign/calibration
directories, and `live-arena-snapshot-20260906T120713Z.json` are present and
hash-bound by the manifest (section 2.2). None were modified by this task.

## 4. Gate re-evaluation (evaluator run to a scratch path; committed artifact untouched)

Command: `python3 RED_LOOP/surface/m3_1_acceptance.py --campaign camp-20260906T154844Z-23100f --out <scratch>/m3-1-acceptance-reeval.json`
(copy retained at `RED_LOOP/runs/m4-baseline-20260906T182802Z/m3-1-acceptance-reeval.json`).
Run at HEAD `4d78f86`.

| Gate | Committed | Re-eval | Notes |
|---|---|---|---|
| historical_integrity | pass | **fail** | Evaluator checks `branch in ("milestone-3-1-clean-parity","milestone-3-1-calibration","milestone-3-1-claude-final")`; current branch is `milestone-4-direct-reconciliation`. Its two commit checks (`twin-v1.0^{commit}` = `78def24…`, `milestone-2-red-loop` = `a107612…`) both still hold. |
| evidence_archive_manifest | pass | pass | manifest_sha256 identical |
| runtime_isolation_no_overlap | pass | pass | |
| clean_boot_a_26_0_0 | pass | pass | boot_id f60691ce-… |
| clean_boot_b_26_0_0 | pass | pass | boot_id 8492d68c-… |
| clean_boot_egress_clean | pass | pass | |
| v17_resolved | pass | pass | HEALTHY_ON_CLEAN |
| logical_replay_material_match | pass | pass | |
| fresh_merchant_current_head | pass | pass | 8/8, hardened profile |
| fresh_id_calibration_fixture | pass | pass | |
| fresh_id_deterministic_replay | pass | pass | |
| fresh_id_negative_control | pass | pass | |
| fresh_id_cross_provider_replay | pass | pass | |
| deterministic_judge | pass | pass | 7 tests |
| candidate_admission | pass | pass | |
| hardened_gateway | pass | pass | live `KONG_ENFORCE_ROUTE_POLICY=1` confirmed via `docker inspect` |
| campaign_isolation | pass | pass | |
| safety_egress | pass | pass | 0 / 0 |
| evidence_integrity | pass | pass | |
| second_open_campaign | pass | pass | stagnation_pause, kimi-k3, 61 turns |
| context_efficiency_recomputed | pass | pass | 0.809 |

`evidence_sha256` block: identical between committed and re-evaluated artifact.
Re-eval `accepted=false` solely from `historical_integrity`.

## 5. Live-arena baseline fingerprint (captured BEFORE stopping)

Evidence dir: `RED_LOOP/runs/m4-baseline-20260906T182802Z/` (git-ignored).
Files: `git.txt`, `docker-ps.txt`, `image-digests.txt`,
`arena-fingerprint-live.json`, `config-fixture-spec-sha256.txt`,
`seeds-generated-sha256.txt`, `live-arena-snapshot-20260906T182802Z.json`,
`m3-1-acceptance-reeval.json`, `orchestrator.log`, `clean-boot-m4-base-a.log`,
`live-restoration-proof.txt`.

### 5.1 Live arena state at capture (2026-09-06T18:28:02Z)

- Project `env2_compose`: 66 running, 65 healthy (cron-driver defines no
  healthcheck), 0 unhealthy; 67 compose containers total (ledger-scheduler is a
  one-shot, `Exited (0)`).
- Networks: `rzp-arena` `bedd720758fa…` (172.28.16.0/24), `rzp-ingress`
  `72e15908c82e…` (172.28.17.0/24). These differ from the M3.1 snapshot at
  12:07Z (`2c505c083d14` / `4b9fbcec821b`): the live arena was re-created at
  13:40:40Z from commit `39fe41e5` (`.runtime/arena-fingerprint.json`,
  boot_id `06330f7e-3099-4299-ab0c-9b9d50c43899`), i.e. during M3.1's
  fresh-merchant work, before the M3.1 tag. This is the arena M3.1's
  `hardened_gateway` gate was evaluated against.
- Volumes: 16 (8 `env2_compose_*` datastores + 8 `rzp-arena-config/secrets-*`).
- Kong: `KONG_ENFORCE_ROUTE_POLICY=1` (hardened; required by the accepted
  `hardened_gateway` gate), healthy, host port 18080 answers (HTTP 404 on `/`).
- Live `config_digest` `839a4da8c312d11fb6753b99318f7c22d2cc349b46a01b838eeb5e4b1aacf092`
  equals the digest recorded at the arena's own boot: no ENV2_COMPOSE input has
  changed since the live arena was booted.

### 5.2 Core image digests (identical in live arena and clean boot)

| Service | Image | Image ID / RepoDigest |
|---|---|---|
| payouts-api | rzp-arena/payouts:v1-candidate | `sha256:915299abaaf234dd7d8be73da9ec18c6b0554703184c6510c8516f3f6698838b` |
| fts-web | rzp-arena/fts:v1-candidate | `sha256:dac4c76a4686f6f5d4d65fdccfbb5505e74e8fefd55cd1012602dd36f31a930d` |
| ledger-api | rzp-arena/ledger:v1-candidate | `sha256:878be96a5f2bbef6af7236660984547a420f27c58d0f7a88b6e5fdb74ada4361` |
| cfa-server | rzp-arena/cfa:v1-candidate | `sha256:1b6cf763e003de35f69032eb1b39179a25b4dc6e76cc329a3140673712e27206` |
| xbalances-server | rzp-arena/xbalances:v1-candidate | `sha256:980f8333a3e906a8ed51e865fdde72c9060f92774bdf551674f3c64522733b5f` |
| kong-lite (edge) | rzp-arena/kong-lite:v1-candidate | `sha256:0ff91a193035bc14523afb538f6f7fe03faac5c782a4b722af5b01f4e879531c` (live) |

RepoDigests are local-only (`rzp-arena/<svc>@sha256:<same id>`); no registry
is involved. Substitute images (`*-stub`, `*-sim`, `*-sink`, `stork-capture`,
`workflow-sim`, `ledger-gate`, `kong-lite`) are rebuilt by `compose build` on
every clean boot, so their IDs differ per boot by design; the five core images
are the fidelity-bearing ones and were identical.

### 5.3 Config / fixture / spec hashes

| File | SHA-256 |
|---|---|
| ENV2_COMPOSE/config/arena.yaml | `b3f29f1ac92221abddf24264387faeb909240ced2cd795e0f9444b23f65eba66` |
| ENV2_COMPOSE/docker-compose.yml | `9c75c7faccca490e510dba03dbc0a896cf498a0424085066520124847a227287` |
| TWIN_SPEC/acceptance-invariants.yaml | `f7e7986d119b55dece0d2d422518773038a00706bab7a7bff5d4d5a15baa625b` |
| TWIN_SPEC/architecture.yaml | `be60194534353db8515bb4e5876a8c4ccb35b243711c56e3e7dd0ca82c11cdb2` |
| TWIN_SPEC/components.yaml | `84f58ab7f15c0817824d21c839bde979a0c8589eee409ae83e0c426dcf2c5b20` |
| TWIN_SPEC/configuration-manifest.yaml | `a2d79676140523ca35cf503beca0b614819f10d0a0fec4639403dba4cba912ff` |
| TWIN_SPEC/expected-failures.yaml | `0d842952df54fdfea0c8b5be9a10528af05ad5e0abbb9680ece1b93c8a8fc32f` |
| TWIN_SPEC/route-matrix.yaml | `c48af9e17e1d65b36f75b1013700534250bf40926b56360ceb6b09a1378f6c4b` |
| TWIN_SPEC/runtime-topology.yaml | `5825b69100844ce8ff3ecd40d86655e69da0c74fd68cb24888a0c0e864685ce5` |
| TWIN_SPEC/state-machines.yaml | `2d9023d1418c7378b4b4559bf8e042a82428d51afbb5db79f2b8223474c28a20` |
| TWIN_SPEC/synthetic-data-model.yaml | `1ebaad8b52193dda889e6cbaa64addfd2672a0306a4b86e924a0e3f8bafbaf40` |

Seed fixtures (32 files under `ENV2_COMPOSE/seeds/**/*.{json,sql,js}`,
excluding `seeds/generated/`) — full list in `config-fixture-spec-sha256.txt`;
representative entries:

| File | SHA-256 |
|---|---|
| seeds/merchants.json | `f1f0fed8aea0d62ea1e52681ee178ca0fec1baf595aed55c93e6ba3fb5ff52bb` |
| seeds/pricing.json | `309e2b0c454e528fe60fcbe2e32853728032aa7a468de7c073cee3c5bd62aa37` |
| seeds/monolith/merchants.json | `1d76ceef13441fa21e57504294405193de8a444e4ac4824bcfb827fdb6c8515b` |
| seeds/postgres/ledger_seed.sql | `73cd071bebdb772ec4d43522ee8031f163d60ea8b3e28a999be3528f7258bc32` |
| seeds/mysql/fts_seed.sql | `860fa5473cc5e6bd5220f797f82a5d2e25ea51997ca7a0b328522f526810ffb4` |
| seeds/s4/payouts.sql | `88e6d9c9c81b3a7148715b0bd5cc5e9ea3af9556c789031a63da2285e21e535d` |
| seeds/s4/ledger.sql | `eeec91b795ffbdd5fbe7b4d66d71d1f079f51a8cd43ff3b5b0a37964aee082eb` |
| seeds/mongo/cfa_seed.js | `82cce08c3e6885aacc997ed74f454d4fedb370f8cdaa348f5cf957f661268edd` |

`seeds/generated/` (19 rendered, credential-bearing files) is hashed
separately in `seeds-generated-sha256.txt`; hashes only, contents never copied.
`ENV2_COMPOSE/scripts/fingerprint.py` (126 input files) gives live
`config_digest = 839a4da8c312…`.

## 6. Clean boot `m4-base-a` (frozen baseline, empty volumes)

Sequence (all timestamps UTC, 2026-09-06): live snapshot written 18:28:02 →
`docker compose … stop` 18:28:42–18:28:56 (0 running, 16 volumes retained) →
`bash ENV2_COMPOSE/scripts/clean-boot.sh m4-base-a` 18:28:56–18:30:56 →
`instance-cleanup.py m4-base-a` 18:32:03 → live `compose start` 18:32:19 →
66/65 healthy at 18:32:42.

| Item | Value | M3.1 boot A | M3.1 boot B |
|---|---|---|---|
| Instance / project | `m4-base-a` / `env2c_m4_base_a` | m31-clean-a / env2c_m31_clean_a | m31-clean-b / env2c_m31_clean_b |
| Subnets / kong port | 172.28.54.0/24, 172.28.55.0/24 / 18846 | 172.28.62–63 / 18334 | 172.28.54–55 / 18247 |
| Preflight overlap | networks [] volumes [] port_in_use False reuses_live_net False → SAFE | same | same |
| Route policy | `KONG_ENFORCE_ROUTE_POLICY=0` (frozen), `REGEN_SECRETS=1` | same | same |
| Containers booted | 66 | 66 | 66 |
| **Verifier (junit)** | **total 26, passed 26, failed 0, skipped 0** (pytest: "26 passed in 48.90s") | 26/0/0 | 26/0/0 |
| **Egress audit** | **status passed, outside_packets 0, destinations [], control_packets 38,123**, capture ready before command and alive after (coverage proven), command exit 0, 51.6 s | 0 outside / 36,733 control | 0 outside / 32,496 control |
| boot_id | **`45e3f0a6-1cfb-481e-8552-1beaf309199c`** | f60691ce-be2e-44d9-86c0-85bb9e750232 | 8492d68c-ba3b-4609-a917-0de8f7c0c6a2 |
| fingerprint git_head | `3371d82031509a74ddc202624852c8cfe978a11c` | d33944450ce7… | — |
| config_digest | `174d8c7aaaddf3f5e7257010edded128a34a41170eefec7c46009ce370927453` | 158a27c3cbc4… | — |
| config_hashes diff vs live tree | only `docker-compose.yml` (clean-boot.sh rewrites the default subnet literals in the disposable copy; every other of the 126 inputs identical) | same single diff | — |
| images_unresolved | `rzp-arena/mozart:v1-candidate` (compose-built substitute, recorded not fatal — same as live) | same | — |
| Unexplained exits / timeouts | none; `up.log` ends "Env 2 arena is up"; clean-boot exit 0 | none | none |

Evidence: `RED_LOOP/runs/cleanboot-m4-base-a/{instance-plan.json,up.log,verifier.log,verifier-run/{junit.xml,egress.json,dns.json,arena-fingerprint.json,test_*.jsonl}}`
(26 per-test trace files, same set as M3.1). Full console log:
`RED_LOOP/runs/m4-baseline-20260906T182802Z/clean-boot-m4-base-a.log`.

## 7. Live-arena restoration proof (`live-restoration-proof.txt`, 18:33:04Z)

| Check | Before stop | After restart |
|---|---|---|
| Running / healthy / unhealthy / starting | 66 / 65 / 0 / 0 | **66 / 65 / 0 / 0** |
| `rzp-arena` id | `bedd720758fa6aaa099fd2ac432f186612ecdf8bf4d0ff1ad2b8dc3388aa0535` | **unchanged** |
| `rzp-ingress` id | `72e15908c82e6c81170787dd622dee4efca7c6e88b6293a2186379bdb3a7ee68` | **unchanged** |
| Compose container IDs (67) | snapshot | **identical set** (`same_container_ids_as_snapshot=True 67 67`) |
| Volumes | 16 | **16** |
| Kong | healthy, enforcement=1, :18080 → 404 | **healthy, enforcement=1, :18080 → 404** |
| Disposable-instance leftovers | — | 0 containers, 0 networks, 0 volumes (`instance-cleanup` reported CLEAN=True) |

Method: `docker compose --env-file .env.arena --profile datastores --profile substitutes --profile core stop`
then `… start` from `ENV2_COMPOSE` (the command recorded in the M3.1 snapshot).
`down` was never used against the live project.

## 8. Safety statement

All commands ran against the local Docker arena only (projects `env2_compose`
and the disposable `env2c_m4_base_a`). No production, staging, corporate or
DevStack endpoint was contacted; the egress audit recorded zero packets leaving
the instance subnets during the verifier window. No historical evidence, tag or
committed M3.1 artifact was modified; `git push` was not run; nothing was
committed.
