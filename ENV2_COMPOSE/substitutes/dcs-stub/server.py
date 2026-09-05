#!/usr/bin/env python3
"""dcs-stub: substitute for goutils/dcs (the DCS KV/Auth API). See CONTRACT.md.

Wire shapes taken from goutils/dcs/rpc/dcs/{kv,auth}/v1/*.pb.go and
proto/dcs/kv/v1/kv.proto (findings/26_dcs_splitz_governor_shield.md section A):
  POST /v1/auth/login  -> {"access_token": "..."}   (auth.pb.go: json:"access_token")
  POST /v1/kv/get      -> batched read, fieldmasks per key
  POST /v1/kv/evaluate -> same request/response shape as Get (level-combining
                           resolution is a store-internal concept; this stub
                           has only one level, so Evaluate == Get)
  POST /v1/kv/patch    -> partial update, persisted in-process
  POST /v1/kv/put      -> full replace, persisted in-process

`value` on the wire is base64(protobuf bytes) of the object named by
Key.object_name (KeyValue{key, value bytes}, kv.proto). This stub ships a
small proto3 wire-format encoder/decoder (varint bool/int64, length-delimited
string/repeated-string/map<string,string>) driven by FIELD_SCHEMAS below, so
`value` round-trips as real protobuf bytes for the 6 payouts/CFA config-proto
messages this arena's fixtures use -- not just an opaque JSON blob. Field
numbers/types are taken from findings/26 section A's schema table (itself
read from config-proto/rzp/x/merchant/payouts/*.proto).

Known, load-bearing gap (documented at length in CONTRACT.md): payouts'
own DCS client (payouts/pkg/dcs/client.go, GenerateOptions) never sets
`ServerURL` -- goutils/dcs@v1.7.3's own URL-resolution logic derives the
login host from a hardcoded env->real-hostname map whenever exactly one
Mode is configured (which payouts and CFA both do), completely ignoring
ServerURL in that branch. So the `[dcs] ServerURL = ".../dcs-stub"` key in
payouts.toml.tmpl/cfa.toml.tmpl is inert against the pristine binaries;
this stub is still fully built to spec (for the verifier, for manual
curl/testing, and in case a patched binary using dcs.SetContextUrl is used)
but will NOT actually be called by an unpatched payouts-api/cfa-server in
this arena. See config/templates/payouts.toml.tmpl's [dcs] comment and
findings/28_build_spike_payouts.md ("Blockers" #1) / findings/28_build_spike_cfa.md
(the analogous incident #1) for the grounding.
"""
import base64
import json
import os
import struct
import sys

sys.path.insert(0, "/app")
from _common.base_stub import serve, _log  # noqa: E402

SEED_FILE = os.environ.get("DCS_SEED_FILE", "/app/seed/merchants.json")

# object_name -> ordered [(field_number, field_name, type), ...]
# type in {"bool", "int64", "string", "repeated_string", "map_string_string"}
# Source: findings/26_dcs_splitz_governor_shield.md section A schema table
# (config-proto/rzp/x/merchant/payouts/*.proto field numbers).
FIELD_SCHEMAS = {
    "rzp/x/merchant/payouts/Workflows": [
        (1, "enable_approval_via_oauth", "bool"),
        (2, "skip_workflow_for_dashboard", "bool"),
        (3, "skip_workflow_for_payroll", "bool"),
        (4, "skip_approval_workflow_for_api", "bool"),
        (5, "enable_payout_workflow", "bool"),
    ],
    "rzp/x/merchant/payouts/FundTransfer": [
        (1, "enable_payouts", "bool"),
        (2, "payouts_to_fts_async_processing", "bool"),
        (3, "increase_per_payout_amount_limit", "bool"),
        (4, "payouts_blocked_via_lite_account", "bool"),
        (5, "enable_payouts_queue_buffer", "bool"),
        (6, "queue_payout_bal_buffer", "int64"),
        (7, "block_va_payouts", "bool"),
    ],
    "rzp/x/merchant/payouts/FundLoading": [
        (1, "skip_whitelisted_source_accounts", "bool"),
    ],
    "rzp/x/merchant/payouts/ApiInterface": [
        (1, "enable_beneficiary_name_in_response", "bool"),
        (2, "enable_null_narration", "bool"),
        (3, "payout_idem_key_required", "bool"),
        (4, "enable_http_encryption", "bool"),
        (5, "below_rupee_payouts", "bool"),
        (6, "payout_service_enabled", "bool"),
        (7, "fmp_config", "map_string_string"),
        (8, "bene_name_in_payout", "bool"),
        (9, "rbl_ca_upi", "bool"),
        (10, "enable_ip_whitelist_fetch", "bool"),
    ],
    "rzp/x/merchant/payouts/Cfa": [
        (1, "skip_ifsc_lookup", "bool"),
    ],
    "rzp/x/merchant/payouts/direct_accounts/Configs": [
        (1, "in_flight_reservation_enabled", "bool"),
    ],
    "rzp/x/merchant/payouts/direct_accounts/PayoutModeConfig": [
        (1, "allowed_upi_channels", "repeated_string"),
    ],
}


# ---------------------------------------------------------------------------
# Minimal proto3 wire-format encoder/decoder (varint + length-delimited only
# -- sufficient for bool/int64/string/repeated-string/map<string,string>,
# which is every field type FIELD_SCHEMAS uses). No external protobuf
# dependency; stdlib-only per the substitutes image constraint.
# ---------------------------------------------------------------------------

def _encode_varint(value):
    out = bytearray()
    v = value & 0xFFFFFFFFFFFFFFFF if value < 0 else value
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _decode_varint(buf, pos):
    result = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def _tag(field_num, wire_type):
    return _encode_varint((field_num << 3) | wire_type)


def _encode_len_delim(field_num, raw_bytes):
    return _tag(field_num, 2) + _encode_varint(len(raw_bytes)) + raw_bytes


def _encode_map_entry(field_num, key, value):
    # map<string,string> entries are themselves length-delimited messages:
    # {1: key string, 2: value string} (proto3 map wire-format convention).
    entry = _encode_len_delim(1, key.encode("utf-8")) + _encode_len_delim(2, value.encode("utf-8"))
    return _encode_len_delim(field_num, entry)


def encode_message(values, schema):
    """values: {field_name: python value}. Returns proto3 wire bytes.
    Proto3 omits zero-value scalar fields on the wire (bool False, int64 0,
    empty string/list/map) -- reproduced here for byte-parity with a real
    marshaller."""
    out = bytearray()
    for field_num, name, ftype in schema:
        v = values.get(name)
        if ftype == "bool":
            if v:
                out += _tag(field_num, 0) + _encode_varint(1)
            # False is the zero-value: omitted, matches proto3 wire convention.
        elif ftype == "int64":
            if v:
                out += _tag(field_num, 0) + _encode_varint(int(v))
        elif ftype == "string":
            if v:
                out += _encode_len_delim(field_num, str(v).encode("utf-8"))
        elif ftype == "repeated_string":
            for item in (v or []):
                out += _encode_len_delim(field_num, str(item).encode("utf-8"))
        elif ftype == "map_string_string":
            for k, val in (v or {}).items():
                out += _encode_map_entry(field_num, k, str(val))
    return bytes(out)


def decode_message(raw, schema):
    """Inverse of encode_message -- best-effort, used only so Patch/Put can
    echo back a JSON-legible value; the stub's own STORE keeps values as
    JSON dicts, so this is not on the hot Get/Evaluate path."""
    by_num = {num: (name, ftype) for num, name, ftype in schema}
    values = {}
    pos = 0
    n = len(raw)
    while pos < n:
        tag, pos = _decode_varint(raw, pos)
        field_num, wire_type = tag >> 3, tag & 0x7
        name, ftype = by_num.get(field_num, (None, None))
        if wire_type == 0:
            val, pos = _decode_varint(raw, pos)
            if name and ftype == "bool":
                values[name] = bool(val)
            elif name and ftype == "int64":
                values[name] = val
        elif wire_type == 2:
            length, pos = _decode_varint(raw, pos)
            chunk = bytes(raw[pos:pos + length])
            pos += length
            if name == "repeated_string" or ftype == "repeated_string":
                values.setdefault(name, []).append(chunk.decode("utf-8", "replace"))
            elif ftype == "string":
                values[name] = chunk.decode("utf-8", "replace")
            elif ftype == "map_string_string":
                # decode the {1:key,2:value} nested entry
                k, v2 = "", ""
                p2 = 0
                while p2 < len(chunk):
                    t2, p2 = _decode_varint(chunk, p2)
                    fn2, wt2 = t2 >> 3, t2 & 0x7
                    l2, p2 = _decode_varint(chunk, p2)
                    piece = chunk[p2:p2 + l2].decode("utf-8", "replace")
                    p2 += l2
                    if fn2 == 1:
                        k = piece
                    elif fn2 == 2:
                        v2 = piece
                values.setdefault(name, {})[k] = v2
        else:
            # unknown wire type for our schemas -- skip minimally (bytes not
            # otherwise consumed); safe no-op since none of our fields use it.
            break
    return values


# ---------------------------------------------------------------------------
# In-memory store, seeded from SEED_FILE at boot.
# STORE[merchant_id][object_name] = {field_name: value, ...}  (JSON logical shape)
# LEGACY[merchant_id] = {legacy_flag_name: value, ...}         (stub-only alias table)
# ---------------------------------------------------------------------------
STORE = {}
LEGACY = {}


def _load_seed():
    if not os.path.exists(SEED_FILE):
        _log("no seed file at %s -- dcs-stub starting empty" % SEED_FILE)
        return
    with open(SEED_FILE) as f:
        data = json.load(f)
    for mid, rec in data.get("merchants", {}).items():
        STORE[mid] = dict(rec.get("dcs", {}))
        LEGACY[mid] = dict(rec.get("legacy", {}))
    _log("loaded %d merchant(s) from %s" % (len(STORE), SEED_FILE))


_load_seed()


def _flatten(key):
    """namespace/entity[/entity_id]/domain/object_name -- goutils/dcs/key.go format."""
    ns = key.get("namespace", "")
    entity = key.get("entity", "")
    entity_id = key.get("entity_id") or key.get("entityId") or ""
    domain = key.get("domain", "")
    obj = key.get("object_name") or key.get("objectName") or ""
    parts = [ns, entity]
    if entity_id:
        parts.append(entity_id)
    parts += [domain, obj]
    return "/".join(parts)


def _object_name_for_key(key):
    domain = key.get("domain", "")
    obj = key.get("object_name") or key.get("objectName") or ""
    return "%s/%s" % (domain, obj) if domain else obj


def _login(handler, body):
    return 200, {"access_token": "arena-dcs-static-access-token"}


def _resolve_value_bytes(entity_id, key):
    """Returns base64(protobuf bytes) for (entity_id, key), OFF/empty object
    if unseeded. object_name here means the KV-layer object_name field
    (e.g. 'Workflows'), while FIELD_SCHEMAS/STORE key on the FULL
    'domain/object_name' path (e.g. 'rzp/x/merchant/payouts/Workflows')."""
    domain = key.get("domain", "")
    obj = key.get("object_name") or key.get("objectName") or ""
    full_name = obj
    # domain in real Key values is 'payouts' or 'payouts/direct_accounts';
    # FIELD_SCHEMAS/STORE key on the full 'rzp/x/merchant/payouts/...' path
    # per findings/26's table -- reconstruct it the same way seeds/dcs/
    # merchants.json was written.
    if domain:
        full_name = "rzp/x/merchant/%s/%s" % (domain, obj)
    schema = FIELD_SCHEMAS.get(full_name, [])
    values = STORE.get(entity_id, {}).get(full_name, {})
    raw = encode_message(values, schema)
    return base64.b64encode(raw).decode("ascii"), full_name


def _get_or_evaluate(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    kvs = []
    for q in req.get("queries", []):
        key = q.get("key", {})
        entity_id = key.get("entity_id") or key.get("entityId") or ""
        value_b64, _ = _resolve_value_bytes(entity_id, key)
        kvs.append({"key": key, "value": value_b64})
    return 200, {"kvs": kvs}


def _put(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    key = req.get("key", {})
    entity_id = key.get("entity_id") or key.get("entityId") or ""
    domain = key.get("domain", "")
    obj = key.get("object_name") or key.get("objectName") or ""
    full_name = "rzp/x/merchant/%s/%s" % (domain, obj) if domain else obj
    schema = FIELD_SCHEMAS.get(full_name, [])
    raw = base64.b64decode(req.get("value", "") or "")
    values = decode_message(raw, schema)
    STORE.setdefault(entity_id, {})[full_name] = values
    return 200, {"key": key}


def _patch(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    key = req.get("key", {})
    entity_id = key.get("entity_id") or key.get("entityId") or ""
    domain = key.get("domain", "")
    obj = key.get("object_name") or key.get("objectName") or ""
    full_name = "rzp/x/merchant/%s/%s" % (domain, obj) if domain else obj
    schema = FIELD_SCHEMAS.get(full_name, [])
    raw = base64.b64decode(req.get("value", "") or "")
    patch_values = decode_message(raw, schema)
    existing = STORE.setdefault(entity_id, {}).setdefault(full_name, {})
    fieldmasks = [fm.get("name") for fm in req.get("fieldmasks", []) if fm.get("name")]
    if fieldmasks:
        for fname in fieldmasks:
            if fname in patch_values:
                existing[fname] = patch_values[fname]
    else:
        existing.update(patch_values)
    return 200, {"key": key, "count": 1}


def _audit(handler, body):
    return 200, {"audit_logs": []}


def _entities(handler, body):
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid_json"}
    return 200, {"entity_ids": sorted(STORE.keys())}


# --- stub-only convenience surface (NOT a real DCS/Proxy contract -- see
# CONTRACT.md "legacy names" section). Lets a curl/verifier script fetch
# both naming conventions for a merchant in one call. ---
def _legacy_get(handler, body):
    entity_id = handler.path.rsplit("/", 1)[-1]
    return 200, {"entity_id": entity_id, "legacy": LEGACY.get(entity_id, {})}


ROUTES = {
    ("POST", "/v1/auth/login"): _login,
    ("POST", "/v1/kv/get"): _get_or_evaluate,
    ("POST", "/v1/kv/evaluate"): _get_or_evaluate,
    ("POST", "/v1/kv/put"): _put,
    ("POST", "/v1/kv/patch"): _patch,
    ("POST", "/v1/kv/audit"): _audit,
    ("POST", "/v1/kv/entities"): _entities,
    ("GET", "/_arena/legacy/"): _legacy_get,
}

if __name__ == "__main__":
    serve(ROUTES)
