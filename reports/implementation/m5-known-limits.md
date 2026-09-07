# M5 / M4.1 Known Limits (honest)

## M4.1 (repaired this session)
- **M41-07 PARTIAL**: acceptance reproduces 74/74 from a clean checkout, but only **7/74 gates**
  (G01,G02,G05,G68,G69,G71,G73) are independent machine checks (git/hash). The other 67 validate
  coordinator-written runtime artifacts. M4.1 fixes reproducibility, NOT the self-report dependence.
  Converting all 67 to direct raw-evidence validators is deferred (large, requires live arena replay).
- **M41-08 NOT ADDRESSED**: 10 boundary tests still skip on the frozen-baseline boot because the
  `hardened_edge` fixture calls `pytest.skip()` when `KONG_ENFORCE_ROUTE_POLICY != 1`. Making them
  execute requires a second hardened boot profile and an arena run; not performed this session.
  Tenant-isolation from empty volumes therefore remains unexercised on the frozen boot.

## M5 (Phases 1–8) — NOT EXECUTED this session
- The real maker-checker workflow surface, the rebuilt autonomous discovery machinery, blind
  calibration, and the 3–6 hour final campaign were NOT run. These require the ~66-container arena
  (host at capacity on 11.65 GiB) plus external LLM gateways over multiple hours, which cannot be
  honestly compressed into this session without fabricating campaign/finding evidence.
- Verdict discipline preserved: absence of a live model-originated finding is NOT ACCEPTED. If/when
  the platform is built and the campaign finds nothing, the correct verdict is
  PLATFORM_COMPLETE_DISCOVERY_NOT_PROVEN. This session reaches neither — it delivers M4.1 only.
