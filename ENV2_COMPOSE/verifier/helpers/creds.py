"""Credential resolution for service Basic-Auth pairs.

None of the arena's service credentials are generated at spec-writing time
(``ENV2_COMPOSE/secrets/`` is empty), so nothing here may fabricate a
username/password. Resolution order, first hit wins:

1. ``<PREFIX>_BASIC_AUTH`` env var, ``"user:pass"`` (e.g. ``LEDGER_TWIRP_BASIC_AUTH``).
2. ``<PREFIX>_BASIC_AUTH_FILE`` env var: path to a file containing ``"user:pass"``.
3. ``<SECRETS_DIR>/<prefix-as-slug>`` (default ``SECRETS_DIR=/run/secrets``), mirroring
   the convention ``ENV2_COMPOSE/substitutes/_common/base_stub.py`` already uses for the
   F2 stub containers (``STUB_BASIC_AUTH_FILE``).

Returns ``(username, password)`` or ``None``. Callers (conftest fixtures) turn a
``None`` into ``pytest.skip(...)`` naming the missing env var — this module never
invents a credential that would only work against a relaxed auth check.
"""
import os


def _read_file(path):
    if not path:
        return None
    try:
        with open(path, "r") as fh:
            content = fh.read().strip()
    except OSError:
        return None
    return content or None


def resolve_basic_auth(env_prefix):
    """env_prefix: e.g. "LEDGER_TWIRP", "PS_INTERNAL", "FTS", "FASTCRON", "MONOLITH"."""
    direct = os.environ.get("%s_BASIC_AUTH" % env_prefix)
    if direct and ":" in direct:
        user, _, pw = direct.partition(":")
        return user, pw

    content = _read_file(os.environ.get("%s_BASIC_AUTH_FILE" % env_prefix))
    if content and ":" in content:
        user, _, pw = content.partition(":")
        return user, pw

    secrets_dir = os.environ.get("SECRETS_DIR", "/run/secrets")
    slug = env_prefix.lower().replace("_", "-")
    content = _read_file(os.path.join(secrets_dir, slug))
    if content and ":" in content:
        user, _, pw = content.partition(":")
        return user, pw

    return None
