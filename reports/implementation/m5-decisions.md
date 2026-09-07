# M5 / M4.1 Decisions

- **D5-001** Base the M5 branch on the M4 evidence line (HEAD 0cb90eb, descends from tag 3ae1773).
  Rationale: it is the tip of the accepted M4 state and includes the finalize housekeeping commit.
- **D5-002** Repair the soak-path defect by making the TRACKED canonical
  `reports/implementation/m4-direct-e2e-soak.json` authoritative. Verified byte-identical
  (sha256 00205fcd…) to the accepted live soak, so acceptance reads the same evidence — a
  relocation to a tracked path, not a re-run or a weakened criterion.
- **D5-003** Add an active canonical-path validator rather than only restructuring data, so the
  defect class (mandatory evidence on an ignored path) fails loudly in future.
- **D5-004** Preserve all 74 M4 gate semantics unchanged (M41-06); the validation-mode matrix is a
  derived classification layer over the preserved gates, and honestly marks that only 7/74 are
  independent machine checks (git/hash) while the rest read coordinator-written artifacts.
- **D5-005** Do NOT edit the historical M4 evaluator in history or retag; the repair lives only on
  the M5 branch working tree.
