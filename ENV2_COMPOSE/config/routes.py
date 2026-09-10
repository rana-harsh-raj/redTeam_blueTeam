"""Source-backed route selection. Apply after base configuration rendering.

This selects existing service branches; it does not translate Kafka payloads or
change application behavior. See reports/implementation/ROUTE_SOURCE_EVIDENCE.md.
"""
import json
import re
import tomllib

PROFILES = {
    "monolith": {"direct_create": False, "direct_status": False, "kafka": False},
    "direct": {"direct_create": True, "direct_status": True, "kafka": False},
    "direct-create-monolith-status": {"direct_create": True, "direct_status": False, "kafka": False},
    "kafka": {"direct_create": True, "direct_status": False, "kafka": True},
}


def _set_section(text, section, fields):
    """Set only named scalar keys; preserve unrelated config and credentials."""
    pattern = re.compile(r"(?m)^\s*\[" + re.escape(section) + r"\]\s*(?:#[^\n]*)?$")
    match = pattern.search(text)
    if match:
        start = match.end()
        next_section = re.search(r"(?m)^\s*\[", text[start:])
        end = start + next_section.start() if next_section else len(text)
        body = text[start:end]
    else:
        text += "\n[" + section + "]\n"
        start = end = len(text)
        body = "\n"
    for key, value in fields.items():
        rendered = "    " + key + " = " + json.dumps(value, separators=(",", ":"))
        key_pattern = re.compile(r"(?m)^\s*" + re.escape(key) + r"\s*=.*$")
        if key_pattern.search(body):
            body = key_pattern.sub(lambda _: rendered, body, count=1)
        else:
            body = body.rstrip() + "\n" + rendered + "\n"
    return text[:start] + "\n" + body.strip("\n") + "\n" + text[end:]


def apply_route_profile(text, svc, profile):
    selected = PROFILES[profile]
    if svc == "payouts":
        text = _set_section(text, "configs.fts_request_from_ps", {
            "enabled_env": "true",
            "whitelist_env": "" if selected["direct_create"] else "__no_synthetic_merchant__",
            "blacklist_env": "",
        })
    elif svc == "fts":
        text = _set_section(text, "splitz.experiments", {
            "create_transfer_meta_rollout": "arena_fts_meta",
            "fire_status_update_kafka": "arena_fts_kafka",
        })
        # ARENA FIX (milestone 1, TWIN_V1_AUDIT_AND_NEXT_STEP.md §5.2/§8 item B6): TIMEOUT was 10s, inverting
        # production's tolerant timeout into premature retries. Prod fts/config/env.prod-live.toml:269-273 sets
        # [payouts_service.update_fts_fund_transfer] URL="https://payouts.razorpay.com/v1/payouts/transfer_status_webhook",
        # METHOD="POST", TIMEOUT=300 -- URL path and METHOD confirmed correct against payouts
        # internal/routing/router/payout_internal_routes.go:12-13,153-158 (group "/v1/payouts" + POST
        # "/transfer_status_webhook", handler HandleTransferStatusWebhookForPayout), left unchanged.
        text = _set_section(text, "payouts_service.update_fts_fund_transfer", {
            "URL": "http://payouts-api:9400/v1/payouts/transfer_status_webhook",
            "METHOD": "POST", "TIMEOUT": 300,
        })
        text = _set_section(text, "kafka_producers.fire_transfer_status", {
            "name": "fire_transfer_status", "enabled": selected["kafka"],
        })
        text = _set_section(text, "kafka_producers.fire_transfer_status.conf", {
            "topic": "rx-fts-status-update-events", "brokers": ["kafka:9092"], "enable_tls": False,
        })
    tomllib.loads(text)  # Reject malformed/duplicate config before writing any file.
    return text


def route_experiments(profile, merchant_ids):
    """Merge these into the generated Splitz seed's experiments object."""
    selected = PROFILES[profile]
    return {
        name: {"name": name, "_classification": "ASSUMED synthetic route-profile variant; source branch verified", "variants": {mid: "on" if enabled else "off" for mid in merchant_ids}}
        for name, enabled in (("arena_fts_meta", selected["direct_status"]),
                              ("arena_fts_kafka", selected["kafka"]))
    }
