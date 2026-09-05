# Explorer verification

Verified locally on 2026-09-05 using agent-browser and the Python server at loopback port 18765; portable export verified separately at 18766.

- All seven navigation views render, including both source state machines and synthetic-data cards without mappings.
- All nine illustrative scenarios were selected and all 128 steps rendered a title and both payload panels: Shared 13, Direct 14, queued 16, scheduled 15, failed 13, returned 22, monolith 13, direct-status 11, Kafka 11. This is UI verification of illustrations, not runtime confirmation of all nine scenarios.
- Map component selection, production/local labels, worker expansion and technical details are available. Laptop layout was corrected so map columns do not clip behind the inspector.
- The actual Local runs button started a synthetic merchant-key-authenticated request through Kong. It passed Payouts/FTS terminal-state matching, balanced initiated/processed Ledger journals, and one signed terminal webhook. Final browser verification captured payout: pout_TYMvS8v7ChP1ih (saved-run.json records the complete trace and snapshot). The current server was restarted after its lifecycle fixes; the live action passed and the static export was rebuilt and rechecked.
- Separate command golden runs passed with simultaneous DNS/packet audit, including runs/golden-monolith-final; the earlier candidate2/golden also recorded resource sampling. No external packet was observed. The UI-triggered demonstration itself was not wrapped in packet capture; the same fixed scenario runner was exercised under capture by the command.
- The portable static build loaded the captured saved trace and displayed five successful assertion steps while the live action was disabled.
- Server requests for /server.py returned 404; forged Host and cross-origin action requests returned 403. The browser receives neither Docker command parameters nor merchant secrets. Runtime traces are sanitized and saved webhook records are narrowed to the current payout.

Screenshots: ARCHITECTURE_EXPLORER/screenshots/architecture.png, shared-flow.png, live-run.png and kafka-blocked.png. The screenshot/browser check found and fixed an initial missing mapping on five synthetic-data cards; all views then passed. The server accepts only allowlisted static resources and the fixed golden action; saved examples and captured evidence remain visibly distinct.

Limit: resource peaks are sampled, not instantaneous. The in-flight demonstration runner validates one terminal delivery at its observation point; duplicate-event resilience is tested separately by V21. Illustrative bank and production flows retain their documented fidelity limits.

## Final boot20 saved-trace refresh

The final audited golden runner passed on boot20 for payout `pout_TYNlMATJ3Bs6Hn`: [result](runs/final-acceptance/golden/live-result.json) and [same-window audit](runs/final-acceptance/golden/egress.json). The portable export was rebuilt and its saved JSON hash matches the source saved JSON. Browser verification displays this exact payout and five verified assertion steps in both the static export and local server. Static live actions are disabled; the local server reports core services ready. [Verification record](explorer-final-saved-verification.json).

The static saved button was activated normally. On the live server, browser click commands reported success without firing the saved action, so this final saved-load check used DOM activation of the existing button; it does not establish a second native-click live launch. The earlier actual UI golden launch remains the live-action evidence. No new payout was launched during the final saved-mode check. Screenshot: `ARCHITECTURE_EXPLORER/screenshots/saved-final.png`.
