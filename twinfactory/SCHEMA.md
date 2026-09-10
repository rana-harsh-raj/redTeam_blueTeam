# twinfactory — records and layout (schema m9.1)

The factory home (`$TWIN_FACTORY_HOME`, default `~/.twin-factory`) lives outside the source checkout.

```
~/.twin-factory/
  registry.json                 durable instance registry: {instances: {<id>: {instance_id, state, profile,
                                architecture_snapshot_id, recipe_set_id, seed, backend, kong_host_port, arena_subnet,
                                compose_project, exec_dir, runtime_instance_id, created_at, updated_at}}, destroyed: [...]}
                                (advisory file lock registry.lock guards every write)
  images/index.json             content-addressed image cache index: {<image ref>: {image_id, repo_digests, tar,
                                tar_sha256, size_bytes, exported_from, exported_at}}; tars named <image id>.tar
  instances/<id>/
    manifest.json               instance manifest (below)
    runtime_instance.json       {runtime_instance_id, body, recorded_at}; runtime_instance_id = sha256(canonical(body))
    profile.json                the derived RuntimeProfile with its derivation trace
    events.jsonl                lifecycle events (create/state/journeys/reset/...)
    workspace.json              rendering record (what was copied, migrations/fts-config status, hashes)
    ENV2_COMPOSE/               rendered static arena definition + instance .env.arena + GENERATED secrets/, generated/,
                                seeds/generated/, .runtime/ (all private to the instance; mode 0600/0700)
    inputs/migrations/, inputs/fts-config/, DOMAIN_REPLICAS/...   read-only inputs mounted by the compose definition
    logs/NN-<step>.log          every boot/reset/stop/destroy step with rc and timing
    evidence/                   arena-fingerprint.json, secrets-manifest.json (names+sha256 only), boot-*.json,
                                journeys-*.json, reset-*.json, destroy-*.json
    journeys/run-<ts>/          runner output (summary.json, evidence/*.json, instance-journeys.json); journeys/reports/
    state/<label>/              datastore dumps written by snapshot-state (STATE.json lists files + sha256)
```

## RuntimeProfile (profile.json)

`{name, kind: runtime_profile, schema_version, architecture_snapshot_id, recipe_set_id, services: {<compose service>:
{category, reasons: ["R<n>: why"...]}}, jobs: [one-shot migration jobs], s2p: bool, families, journeys, derivation:
{rules, family, journey_definitions, proves_families, jobs}, counts}`. `full` = every canonical ServiceRecipe of the
snapshot; `focused:<family>` = closure over rules R1–R11 (see `twinfactory/profiles.py` `RULES`). Aliases:
`critical-payouts` → `focused:shared-payouts`, `focused-s2p` → `focused:cross-domain-s2p`.

## Instance manifest (manifest.json)

| field | meaning |
|---|---|
| `instance_id`, `state` | sanitized id; created / built / running / stopped / failed / destroyed |
| `architecture_snapshot_id`, `recipe_set_id` | the immutable M8 snapshot and recipe set the instance was compiled from |
| `profile`, `profile_digest`, `compose_profiles` | derived profile name; sha256 of its service/job set; compose `--profile` flags |
| `seed`, `seed_epoch` | synthetic-data seed and the fixture epoch derived from it (deterministic) |
| `backend` | `{kind, isolation_boundary, profile, options, identity{daemon_id, name, docker_host, ...}}` |
| `arena_suffix`, `compose_project`, `arena_network`, `ingress_network`, `s2p_network`, `arena_subnet`, `ingress_subnet`, `kong_host_port` | the instance namespace (derived from the id, collision-checked against the registry) |
| `exec_dir`, `env2_root` | execution directory / rendered ENV2_COMPOSE |
| `inputs_hash`, `inputs_file_count` | sha256 over the credential-free static definition (compose files, templates, seed templates, substitutes, scripts, migration assets); identical for every instance rendered from the same checkout |
| `rendered_config_hash`, `secrets_manifest_digest` | digests of the instance-private rendered configuration and generated secrets (values never recorded) |
| `image_digests` | `{<image ref>: {image_id (sha256 config digest), repo_digests, services}}` actually present on the instance daemon |
| `image_records` | per-service resolution source: present / cache / pull / pull-pinned / build |
| `boot` | `{boot_id, config_digest, secs, steps[], started_at}` from the boot orchestrator + fingerprint |
| `timings` | create / provision / generate / images / build / boot / restart / reset / snapshot_state / restore_state / stop / destroy seconds |
| `runtime_instance_id` | content id of the RuntimeInstance body |
| `source_checkout_head`, `arena_tag`, `s2p_image`, `workspace` | provenance |

## RuntimeInstance body

`{kind: RuntimeInstance, schema_version, instance_id, architecture_snapshot_id, recipe_set_id, profile, profile_digest,
seed, seed_epoch, backend{kind, isolation_boundary, profile, daemon_id}, inputs_hash, rendered_config_hash,
secrets_manifest_digest, image_digests, compose_project, arena_suffix, networks, subnets, published_ports, boot_id,
arena_tag, s2p_image, source_checkout_head}` — no timestamps, so the id is stable for the same binding.

## Isolation proof (reports/implementation/m9-isolation-proof.json)

`{kind: m9_isolation_proof, a, b, phases: {P1..P9: {title, passed, detail, secs, at}}, measurements: {<id>: [{label,
containers_mem_mib, docker_system_df, vm_dir_kib, sizing, timings}]}, passed}` — see `twinfactory/isolation.py`.
