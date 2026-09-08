#!/usr/bin/env python3
"""config/generate.py — render per-arena config from the build-spike's
PROVEN-TO-BOOT config files (see config/templates/base/<svc>/), not from
hand-written templates whose keys diverge from the real config structs.

stdlib only (per task constraint). Reads:
  - config/arena.yaml           (topology/flags, NO credentials -- see
                                  _miniyaml.py for the deliberately-minimal
                                  YAML subset parser)
  - secrets/*.txt                (credential VALUES, generated per-arena by
                                  secrets/gen-secrets.sh; this script fails
                                  loudly if a referenced secret file is
                                  missing)
  - config/templates/base/<svc>/*.toml
                                  Base config files copied VERBATIM from the
                                  Env 2 build spike
                                  (findings/28_build_spike*.md), which
                                  independently confirmed each of these
                                  boots the real service binary. Two kinds:
                                    * "<file>.toml"       -- {{TOKEN}}-ized:
                                      every spike-local value that must vary
                                      per-arena (datastore host/port/user/
                                      password, downstream service URLs,
                                      DCS env, Basic-Auth pairs) was replaced
                                      with a {{TOKEN}} placeholder by a
                                      one-off transform script (not part of
                                      this runtime path); everything else is
                                      untouched spike content.
                                    * "default.toml" / "env.default.toml" /
                                      "holiday.toml" -- copied byte-for-byte
                                      from the spike (these are the REAL
                                      repo default.toml files, still full of
                                      *.razorpay.* hosts that are never
                                      actually reached because the arena
                                      overlay file above re-declares every
                                      key that matters and is loaded AFTER
                                      it). No {{TOKEN}} substitution here --
                                      just the blanket forbidden-pattern
                                      sanitizer (see sanitize_forbidden())
                                      applied, like every other output file,
                                      as the final step before writing.
  - config/templates/mozart-mock.toml.tmpl
                                  UNCHANGED, per task instruction -- rendered
                                  through the original {{TOKEN}} path, same
                                  as always.

Writes:
  - generated/<svc>/*.toml       (bind-mounted read-only into each container
                                  by docker-compose.yml -- never baked into
                                  an image)
  - generated/<svc>/<svc>.env    (payouts/ledger/fts/cfa/xbalances only --
                                  dummy/real values for every literal
                                  "env|VARNAME" placeholder left over in that
                                  service's rendered files, so a service
                                  whose own config loader eagerly resolves
                                  every such placeholder at boot -- confirmed
                                  true for fts, unconfirmed either way for
                                  the other four -- never panics on one this
                                  script didn't know to treat specially.
                                  docker-compose.yml's `env_file:` entries
                                  point at these. ALWAYS written, even if
                                  empty, so `docker compose config` never
                                  fails on a missing env_file path.)

This is "config generated from scratch per arena": every run overwrites
`generated/` completely (see --clean, on by default) rather than patching a
previous run's output, so a stale value from a prior arena can never survive
into a new one.

Exit non-zero (and print every offending token) if any `{{TOKEN}}`
placeholder is left unresolved after substitution -- fail closed, never
silently ship a template literal into a running container's config.

This script ALSO applies the same forbidden-hostname/ARN/link-local pattern
sanitizer preflight/preflight.py enforces, to every file it writes (not just
the ones that came from a real repo default.toml) -- belt and suspenders,
since a proven-safe spike file can still carry a *.razorpay.* mention in an
explanatory COMMENT (preflight does not parse TOML, it grep-scans raw
lines). preflight/preflight.py remains the authoritative, independent gate
run as a separate step against this script's OWN output (`generated/`).
"""
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _miniyaml  # noqa: E402
from routes import apply_route_profile, PROFILES
ROUTE_PROFILE = os.environ.get("ARENA_ROUTE_PROFILE", "monolith")
if ROUTE_PROFILE not in PROFILES:
    raise SystemExit("Invalid ARENA_ROUTE_PROFILE: " + ROUTE_PROFILE)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARENA_YAML = os.path.join(ROOT, "config", "arena.yaml")
TEMPLATES_DIR = os.path.join(ROOT, "config", "templates")
BASE_DIR = os.path.join(TEMPLATES_DIR, "base")
SECRETS_DIR = os.path.join(ROOT, "secrets")
GENERATED_DIR = os.path.join(ROOT, "generated")

TOKEN_RE = re.compile(r"\{\{([A-Za-z0-9_.\-]+)\}\}")

# Literal "env|VARNAME" placeholders left over in a rendered file (a
# DIFFERENT substitution convention than {{TOKEN}}, owned by each Go
# service's own config loader, not this script) -- collected per service so
# a <svc>.env file can dummy/real-fill every one of them. Distinct from
# TOKEN_RE: these are plain quoted-string contents in the TOML, e.g.
# `password = "env|PAYOUTS_DCS_PASSWORD"` or, for ledger,
# `exporterHost = "env|LEDGER_TELEMETRY_EXPORTER_HOST|127.0.0.1"` (3-part,
# inline-default form) -- the regex only needs the var name, so it stops
# at the next "|" or closing quote either way.
ENV_PLACEHOLDER_RE = re.compile(r"env\|([A-Za-z0-9_]+)")

# ---------------------------------------------------------------------
# Per-service file manifest: (path under config/templates/base/<svc>/,
# output filename under generated/<svc>/, mode).
#   "token"    -- {{TOKEN}}-substitute, THEN sanitize (fails loudly on any
#                 unresolved {{TOKEN}})
#   "sanitize" -- copy verbatim, then sanitize only (no {{TOKEN}} in these
#                 files -- they are real repo default.toml-family files)
# Every file, regardless of mode, gets sanitize_forbidden() as the final
# step (see module docstring).
# ---------------------------------------------------------------------
SERVICE_FILES = {
    "payouts": [
        ("default.toml", "default.toml", "sanitize"),
        ("arena.toml", "arena.toml", "token"),
        ("e2e.toml", "e2e.toml", "sanitize"),
    ],
    "ledger": [
        ("default.toml", "default.toml", "sanitize"),
        ("arena.toml", "arena.toml", "token"),
    ],
    "cfa": [
        ("default.toml", "default.toml", "sanitize"),
        ("arena.toml", "arena.toml", "token"),
    ],
    "xbalances": [
        ("default.toml", "default.toml", "sanitize"),
        ("arena.toml", "arena.toml", "token"),
    ],
    "fts": [
        ("env.default.toml", "env.default.toml", "sanitize"),
        ("holiday.toml", "holiday.toml", "sanitize"),
        ("env.arena.toml", "env.arena.toml", "token"),
        ("env.arena-migration.toml", "env.arena-migration.toml", "token"),
    ],
}

# mozart-mock keeps the ORIGINAL single-{{TOKEN}}-template convention
# (config/templates/mozart-mock.toml.tmpl, UNCHANGED per task instruction),
# rendered to this filename. Not part of SERVICE_FILES/BASE_DIR at all.
MOZART_MOCK_TEMPLATE = os.path.join(TEMPLATES_DIR, "mozart-mock.toml.tmpl")
MOZART_MOCK_OUT = "env.arena.toml"


def load_secrets():
    """Every secrets/*.txt file becomes SECRET.<basename-without-ext> in the
    substitution map, value = file contents with exactly one trailing
    newline stripped (gen-secrets.sh writes one newline per value)."""
    secrets = {}
    if not os.path.isdir(SECRETS_DIR):
        return secrets
    for name in sorted(os.listdir(SECRETS_DIR)):
        if not name.endswith(".txt"):
            continue
        path = os.path.join(SECRETS_DIR, name)
        if not os.path.isfile(path):
            continue
        with open(path) as f:
            value = f.read()
        if value.endswith("\n"):
            value = value[:-1]
        key = name[: -len(".txt")]
        secrets[key] = value
    # derived: the arena passport public key as a single-line TOML basic string (PEM newlines -> \n),
    # the shape payouts' [passport.<x>].publicKey expects
    if "passport_public_key" in secrets:
        secrets["passport_public_key_toml"] = secrets["passport_public_key"].replace("\n", "\\n")
    return secrets


def flatten_services(arena):
    """services.<key>.host / services.<key>.port -> flat SVC.<key>.host etc.
    Also synthesizes SVC.<key>.url = "http://<host>:<port>" for every entry
    that has both a host and a port -- every {{SVC.*.url}} token templates
    reference for a downstream HTTP client host is built here, not
    hand-maintained per entry."""
    flat = {}
    for key, val in (arena.get("services") or {}).items():
        if not isinstance(val, dict):
            continue
        for subkey, subval in val.items():
            flat["SVC.%s.%s" % (key, subkey)] = subval
        if "host" in val and "port" in val:
            flat["SVC.%s.url" % key] = "http://%s:%s" % (val["host"], val["port"])
    return flat


def build_token_map(arena, secrets):
    tokens = {}
    tokens.update(flatten_services(arena))

    for key, value in secrets.items():
        tokens["SECRET.%s" % key] = value

    merchants = arena.get("merchants") or []
    for i, m in enumerate(merchants, start=1):
        tokens["MERCHANT.M%d.ID" % i] = m.get("id", "")
        tokens["MERCHANT.M%d.MODE" % i] = m.get("mode", "test")

    # Which Mozart implementation is active. Default AND primary is
    # mozart-sim -- the real mozart binary cannot be built in this
    # environment (go build 404s on the private module
    # github.com/razorpay/integrations-utils, confirmed during the
    # substitutes pass; see substitutes/mozart-sim/CONTRACT.md). mozart-mock
    # (the real binary) is available only via the opt-in "mozart-real"
    # compose profile, selected by explicitly setting ARENA_MOZART_IMPL.
    # Read from the process environment (scripts/up.sh sources .env.arena
    # before invoking this).
    mozart_impl = os.environ.get("ARENA_MOZART_IMPL", "mozart-sim")
    if mozart_impl not in ("mozart-mock", "mozart-sim"):
        mozart_impl = "mozart-sim"
    mozart_key = "SVC.mozart_mock" if mozart_impl == "mozart-mock" else "SVC.mozart_sim"
    tokens["MOZART.host"] = tokens.get("%s.host" % mozart_key, "")
    tokens["MOZART.port"] = tokens.get("%s.port" % mozart_key, "")
    tokens["MOZART.url"] = tokens.get("%s.url" % mozart_key, "")

    # The Workflow/Cadence approval-engine substitute (payouts [workflow].host).
    #
    # M2 introduced this token with the FROZEN dead address http://127.0.0.1:1
    # as its default, so workflow-applicable payouts failed to reach `pending`
    # exactly as in twin-v1.0.
    # M6 (I3): .env.arena now SETS ARENA_WORKFLOW_HOST=http://workflow-engine:8093
    # (the durable maker/checker engine, substitutes/workflow-engine), making the
    # approval family live by default. The dead address remains the fallback here
    # for anyone invoking generate.py with an empty environment, and .env.arena
    # documents how to revert or to select the thinner workflow-sim instead.
    #
    # scripts/up.sh loads .env.arena into its own environment before invoking
    # this script (it did NOT until M6 -- `docker compose --env-file` only feeds
    # compose's ${VAR} interpolation, never this process).
    tokens["WORKFLOW_HOST"] = os.environ.get("ARENA_WORKFLOW_HOST", "http://127.0.0.1:1")

    # M4 (T10): merchant whitelist for payouts [configs.account_statement_source_event] (the REAL
    # x_account_statement_source_event producer gate). Empty = every Direct merchant (source semantics:
    # enabled + empty whitelist => all), so a provisioner only needs to set this when it wants to RESTRICT
    # the producer to specific merchants. Comma-separated 14-char merchant ids.
    tokens["XAS_SOURCE_EVENT_WHITELIST"] = os.environ.get("ARENA_XAS_SOURCE_EVENT_WHITELIST", "")


    tokens["ARENA.NAME"] = arena.get("arena_name", "env2")

    return tokens


def render_template(template_text, tokens, template_name):
    unresolved = set()

    def _sub(match):
        token = match.group(1)
        if token not in tokens:
            unresolved.add(token)
            return match.group(0)
        return str(tokens[token])

    rendered = TOKEN_RE.sub(_sub, template_text)
    if unresolved:
        raise ValueError(
            "unresolved tokens in %s: %s" % (template_name, ", ".join(sorted(unresolved)))
        )
    return rendered


# ---------------------------------------------------------------------
# Forbidden-pattern sanitizer -- the SAME four patterns
# preflight/preflight.py enforces (kept in sync deliberately; if that
# script's FORBIDDEN_PATTERNS ever changes, update this too). Applied to
# EVERY file this script writes, regardless of mode -- a proven-safe spike
# file can still carry a *.razorpay.* mention in an explanatory comment
# (preflight grep-scans raw lines, it does not parse TOML), and this is the
# cheapest way to guarantee preflight passes on this script's own output.
# ---------------------------------------------------------------------
# Deliberately as BROAD as preflight/preflight.py's own two hostname
# patterns (bare substring match, no hostname-boundary logic) -- anything
# narrower risks missing a case preflight would still flag (e.g. an email
# address like "no-reply@razorpay.in", or a comment like "*.razorpay.in"),
# which defeats the point of this belt-and-suspenders pass.
_RAZORPAY_HOST_RE = re.compile(r"razorpay\.(?:com|in|vpc)", re.IGNORECASE)
_AWS_HOST_RE = re.compile(r"amazonaws\.com", re.IGNORECASE)
_THIRD_PARTY_URL_RE = re.compile(r"https?://[a-z0-9.-]+\.(?:com|in|io|net|org)(?::\d+)?(?![a-z0-9.-])", re.IGNORECASE)
_AWS_ARN_RE = re.compile(r"arn:aws", re.IGNORECASE)
# ARNs in LocalStack's synthetic account 000000000000 are arena-local resources (queues/topics created by
# seeds/localstack/init-queues.sh); LocalStack rejects any other ARN scheme, so these must survive the rewrite.
_LOCALSTACK_ARN_RE = re.compile(r"arn:aws:[a-z0-9-]+:[a-z0-9-]+:000000000000", re.IGNORECASE)
LOCALSTACK_ARN_RE = _LOCALSTACK_ARN_RE
_LINK_LOCAL_RE = re.compile(r"169\.254\.")

# TOML forbids a leading zero on an integer literal (except the bare literal
# "0"); Go's toml libraries (BurntSushi/viper) are lenient about this and
# happily loaded the real repo's env.default.toml at spike-boot time, but
# Python's stdlib tomllib -- used by this task's own validation step -- is
# spec-strict and rejects it. Only matches a line whose ENTIRE value is a
# bare zero-padded integer (e.g. `START_HOUR = 00`), never touching a
# quoted string or a float.
_LEADING_ZERO_INT_RE = re.compile(r"^(\s*\S.*=\s*)0+([0-9])(\s*(?:#.*)?)$")


def sanitize_forbidden(text):
    text = _RAZORPAY_HOST_RE.sub("blocked.invalid", text)
    text = _AWS_HOST_RE.sub("blocked.invalid", text)
    keep = {}
    def _stash(m):
        key = "\x00ARN%d\x00" % len(keep)
        keep[key] = m.group(0)
        return key
    text = _LOCALSTACK_ARN_RE.sub(_stash, text)
    text = _THIRD_PARTY_URL_RE.sub("http://127.0.0.1:9", text)  # public third-party hosts -> blackhole
    text = _AWS_ARN_RE.sub("arn:local", text)
    for key, val in keep.items():
        text = text.replace(key, val)
    text = _LINK_LOCAL_RE.sub("192.0.2.", text)  # TEST-NET-1, never link-local/metadata
    text = "\n".join(_LEADING_ZERO_INT_RE.sub(r"\1\2\3", line) for line in text.split("\n"))
    return text


# ---------------------------------------------------------------------
# Per-service <svc>.env: dummy/real-fill every literal "env|VARNAME" left
# in that service's rendered output. Explicit overrides win; anything else
# gets a generic, obviously-fake per-var dummy value (never a real secret,
# since these are -- per every spike finding this pass could independently
# confirm -- either genuinely inert at boot, or not independently verified
# either way; see findings/30_env2_substitutes.md's "config derivation from
# spike" section for the per-service list of which is which).
# ---------------------------------------------------------------------
def build_env_overrides(svc, tokens):
    if svc == "fts":
        user = tokens["SVC.mysql_fts.user"]
        pw = tokens["SECRET.mysql_fts_root_password"]
        return {
            "DATABASE_USERNAME": user,
            "DATABASE_PASSWORD": pw,
            "LIVE_MIGRATION_DB_USERNAME": user,
            "LIVE_MIGRATION_DB_PASSWORD": pw,
            # fts -> payouts-api service identity ([payouts_service.auth] env| placeholders) = payouts [auth.fts]
            "PAYOUTS_SERVICE_AUTH_KEY": "fts",
            "PAYOUTS_SERVICE_AUTH_SECRET": tokens["SECRET.auth_fts_payouts"],
            # fts status webhook -> monolith(-stub) /v1/update_fts_fund_transfer ([webhook.payout.auth]) = monolith inbound identity
            "WEBHOOK_PAYOUT_AUTH_KEY": "rzp_live",
            "WEBHOOK_PAYOUT_AUTH_SECRET": tokens["SECRET.auth_monolith_shared"],
            # fts inbound users ([users.*] env| placeholders): PS = payouts-api's FTS client identity, API = monolith
            "USERS_PS_USERNAME": "ps_payouts",   # fts maps username prefix before "_" to the route app (middleware/auth.go:94)
            "USERS_PS_PASSWORD": tokens["SECRET.auth_fts_payouts"],
            "USERS_API_USERNAME": "api_monolith",
            "USERS_API_PASSWORD": tokens["SECRET.auth_monolith_shared"],
            # fts -> ledger-api ([ledger.auth]) = ledger [auth.fts]
            "LEDGER_AUTH_KEY": "fts_key",
            "LEDGER_AUTH_SECRET": tokens["SECRET.auth_fts_ledger"],
            "TELEMETRY_EXPORTERHOST": "127.0.0.1",
            "JAEGER_HOSTNAME": "127.0.0.1",
            # M6: the machinery retry queue ([queue.redis] in env.default.toml is env|REDIS_QUEUE_HOST/PORT).
            # Left as a dummy, the env var wins over the arena TOML overlay and fts-worker-retry-transfer*
            # never dequeues a retryable attempt (M6 journey idempotency-retries/retry). Point it at the arena redis.
            "REDIS_QUEUE_HOST": tokens["SVC.redis.host"],
            "REDIS_QUEUE_PORT": str(tokens["SVC.redis.port"]),
        }
    if svc == "ledger":
        return {
            "LEDGER_ARENA_PG_PASSWORD": tokens["SECRET.postgres_ledger_password"],
            "LEDGER_TELEMETRY_EXPORTER_HOST": "127.0.0.1",
            "LEDGER_TELEMETRY_EXPORTER_PORT": "4318",
            # typed placeholders (a dummy string would fail mapstructure decoding)
            "LEDGER_PROFILING_ENABLED": "false",
        }
    if svc == "payouts":
        return {}  # spike booted payouts with no env vars; TOML overlay is sufficient
    if svc == "cfa":
        return {}  # QUEUE_DRIVER=sqs_local is set on cfa-worker only (compose); cfa-server hardcodes "sqs"
    return {}


def _dummy_value(varname):
    return "arena-dummy-%s" % varname.lower().replace("_", "-")


def _cfa_mongo_local_forward(svc, text):
    """CFA_MONGO_LOCAL_FORWARD: cfa's Mongo client only skips TLS when Endpoint is localhost/127.0.0.1
    (cfa/pkg/storage/mongodb/mongo.go:64). The cfa containers run a local socat forward
    127.0.0.1:27017 -> mongo-cfa:27017 (build/cfa-entry.sh), so the config points at 127.0.0.1."""
    if svc != "cfa":
        return text
    return text.replace('Endpoint              = "mongo-cfa"', 'Endpoint              = "127.0.0.1"')


def write_env_file(svc, rendered_texts, tokens):
    overrides = build_env_overrides(svc, tokens)
    found = set()
    for text in rendered_texts:
        for m in ENV_PLACEHOLDER_RE.finditer(text):
            found.add(m.group(1))
    lines = []
    # Only fts panics on unresolved "env|VAR" placeholders. The other services' loaders let
    # process env vars OVERRIDE the TOML (viper AutomaticEnv), so emitting dummies for them
    # silently replaced real arena values (e.g. PAYOUTS_DB_MASTER_URL) -- root-agent fix:
    # dummies for fts only; explicit overrides only for everyone else.
    for var in sorted(found):
        if var in overrides:
            lines.append("%s=%s" % (var, overrides[var]))
        elif svc == "fts":
            lines.append("%s=%s" % (var, _dummy_value(var)))
    for var, value in overrides.items():
        if var not in found:
            lines.append("%s=%s" % (var, value))
    out_dir = os.path.join(GENERATED_DIR, svc)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "%s.env" % svc)
    with open(out_path, "w") as f:
        if lines:
            f.write("\n".join(lines) + "\n")
    print("wrote %s (%d var%s)" % (out_path, len(lines), "" if len(lines) == 1 else "s"))


def render_service(svc, file_specs, tokens):
    out_dir = os.path.join(GENERATED_DIR, svc)
    os.makedirs(out_dir, exist_ok=True)
    rendered_texts = []
    for src_rel, out_name, mode in file_specs:
        src_path = os.path.join(BASE_DIR, svc, src_rel)
        if not os.path.exists(src_path):
            raise ValueError("missing base file for %s: %s" % (svc, src_path))
        with open(src_path) as f:
            text = f.read()
        if mode == "token":
            text = render_template(text, tokens, src_path)
        elif mode == "sanitize":
            pass  # copied verbatim below, only the blanket sanitizer runs
        else:
            raise ValueError("unknown mode %r for %s" % (mode, src_path))
        text = sanitize_forbidden(text)
        text = _cfa_mongo_local_forward(svc, text)
        if svc == "payouts":
            text = text.replace("http://ledger-api:8080", "http://ledger-gate:8080")
        if out_name in ("arena.toml", "env.arena.toml"):
            text = apply_route_profile(text, svc, ROUTE_PROFILE)
        out_path = os.path.join(out_dir, out_name)
        with open(out_path, "w") as f:
            f.write(text)
        os.chmod(out_path, 0o600)  # rendered configs inline per-arena credentials: same hygiene as secrets/
        print("wrote %s" % out_path)
        rendered_texts.append(text)
    write_env_file(svc, rendered_texts, tokens)


def render_mozart_mock(tokens):
    if not os.path.exists(MOZART_MOCK_TEMPLATE):
        print("WARNING: no %s, skipping mozart-mock" % MOZART_MOCK_TEMPLATE, file=sys.stderr)
        return
    with open(MOZART_MOCK_TEMPLATE) as f:
        template_text = f.read()
    rendered = render_template(template_text, tokens, MOZART_MOCK_TEMPLATE)
    rendered = sanitize_forbidden(rendered)
    out_dir = os.path.join(GENERATED_DIR, "mozart-mock")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, MOZART_MOCK_OUT)
    with open(out_path, "w") as f:
        f.write(rendered)
    print("wrote %s" % out_path)


def main():
    clean = "--no-clean" not in sys.argv

    if not os.path.exists(ARENA_YAML):
        print("ERROR: %s not found" % ARENA_YAML, file=sys.stderr)
        sys.exit(1)

    arena = _miniyaml.load_file(ARENA_YAML)
    secrets = load_secrets()
    if not secrets:
        print(
            "WARNING: no secrets found under %s -- run secrets/gen-secrets.sh first, "
            "or every {{SECRET.*}} token below will fail to resolve." % SECRETS_DIR,
            file=sys.stderr,
        )

    tokens = build_token_map(arena, secrets)

    if clean and os.path.isdir(GENERATED_DIR):
        shutil.rmtree(GENERATED_DIR)
    os.makedirs(GENERATED_DIR, exist_ok=True)

    failures = []
    for svc, file_specs in SERVICE_FILES.items():
        try:
            render_service(svc, file_specs, tokens)
        except ValueError as exc:
            failures.append(str(exc))
            print("ERROR: %s" % exc, file=sys.stderr)

    try:
        render_mozart_mock(tokens)
    except ValueError as exc:
        failures.append(str(exc))
        print("ERROR: %s" % exc, file=sys.stderr)

    if failures:
        print("\nconfig/generate.py FAILED: %d file(s) had unresolved tokens" % len(failures),
              file=sys.stderr)
        sys.exit(1)

    print("config/generate.py: OK, rendered %d service configs into %s" %
          (len(SERVICE_FILES) + 1, GENERATED_DIR))


if __name__ == "__main__":
    os.umask(0o077)  # apply restrictive creation modes before the first write
    main()
