# Milestone 3.1 calibration status

The requested disclosure regression profile, fixture, oracle, replay bundle, and
negative-control runs are **not implemented** in this change. Building and
executing that cross-tenant disclosure and replay workflow was declined. No
calibration routes, credentials, containers, or model adapters were added.

The ordinary gateway and substitute authorization behavior are unchanged. This
directory documents incomplete scope; it is not an executable calibration profile.

Defensive pricing work is implemented in `RED_LOOP/red_loop/pricing_ids.py`:

- Preserve the existing 14-character bounded plan mapping.
- Reserve IDs before the provisioner's first database or seed write.
- Store run-scoped reservations in the arena's generated seed directory, with a
  process lock and atomic replacement. Check ownership across runs and existing
  pricing seeds. A collision fails before seeding, without changing reservations.
- Validate plan IDs and existing explicit rule IDs against the narrowest target,
  `payouts.pricing_rule_id CHAR(14)`, using ASCII alphanumeric identifiers.
- Retain reservations throughout the arena seed lifetime, including failed
  provisioning attempts; remove them only with the corresponding disposable
  arena. These files contain identifiers, not credentials.

Fidelity limitation: the provisioner seeds plans without separate rule IDs. The
monolith substitute therefore returns a **plan ID** as `pricing_rule_id`. This is
not exact production rule-identity parity. Core `RowsAffected` handling is intact.

The reservation protects pricing ID ownership, not the entire multi-datastore
provisioning transaction. Other existing seed writes are not serialized by its
lock. This change does not establish concurrent full-provisioning safety or solve
the existing truncation of unrelated resource IDs.

Offline verification: `python3 RED_LOOP/tests/test_pricing_ids.py`.
All runtime calibration and model replay gates remain pending.
