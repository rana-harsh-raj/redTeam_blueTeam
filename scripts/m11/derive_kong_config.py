#!/usr/bin/env python3
"""M11: derive the twin's Kong configuration MECHANICALLY from the pinned razorpay/terraform-kong production
definitions (no route flag, rollout, plugin or plugin config is invented).

Inputs (read from the clone root, pinned SHA recorded in the output):
  prod/api/api.tf ............ the `prod-api` Kong service: every payouts-facing public route the twin serves is an
                               `authenticated_paths` entry (name, path, methods, rollout, impersonation_rollout,
                               impersonation_mode, passport_enabler_config)
  prod/api/regex.yaml ........ the path regex fragments (`entity_id`)
  prod/api/variables.tf ...... local.passport_enabler_global_config + local.public_api_hosts
  templates/payouts/*.tf ..... the `payouts-ext` service (direct-to-Payouts route with rollout 1)
  module/plugins.tf .......... the plugin set every route class receives (reproduced below, cited per plugin)
  prod/edge/consumers.tf ..... the `anonymous` consumer

Output: ENV2_COMPOSE/trustpath/edge/kong-config.json -- services, routes, per-route plugins and service-level
plugins in Kong Admin API shape, plus `derivation` (source refs per element) and `deviations` (the twin's explicit
departures: no DNS so routes are host-agnostic; upstream hosts are twin services; key/secret file locations are the
twin's; consumers are seeded from the arena merchant seed because the production credential-sync path is unknown).
Route selection = the route names the M7 shared-ingress contract serves (contract/routes.json) that exist on prod-api,
plus contact/fund-account public routes the journeys use.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CLONES = Path((REPO / ".local" / "repos-root").read_text().strip()) if (REPO / ".local" / "repos-root").is_file() else Path("/Users/rana.singh/rzp-payouts-clones")
TK = CLONES / "terraform-kong"
OUT = REPO / "ENV2_COMPOSE" / "trustpath" / "edge" / "kong-config.json"
CONTRACT = REPO / "ENV2_COMPOSE" / "substitutes" / "api-ingress" / "contract" / "routes.json"

# routes the twin serves through the real gateway (public merchant/dashboard surface of the M7 contract + the
# contact/fund-account public routes the journeys exercise). Internal-app routes (rzp_live + app secret) are not
# Kong-authenticated merchant routes in production (api-internal service, plain `paths`), see deviations.
WANTED = ["payout_create", "payout_fetch_by_id", "payout_fetch_multiple", "payout_approve", "payout_reject", "payout_cancel",
          "payout_create_with_otp", "payout_purpose_get", "payouts_batch_create",
          "contact_create", "contact_get", "contact_list", "contact_update",
          "fund_account_create", "fund_account_get", "fund_account_list", "fund_account_update"]


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True).stdout.strip()


def parse_regex_yaml():
    out = {}
    for line in (TK / "templates" / "common" / "regex.yaml").read_text().splitlines():
        m = re.match(r'^([a-z_]+):\s*"(.*)"\s*$', line.strip())
        if m:
            out[m.group(1)] = m.group(2).replace("\\\\", "\\")
    return out


def hcl_block(text, start):
    """Return the text of the {...} block whose opening brace is the first '{' at/after `start`."""
    i = text.index("{", start)
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1:j]
    raise ValueError("unbalanced block")


def parse_route_block(block, regex):
    def val(key, default=None):
        m = re.search(r"^\s*%s\s*=\s*(.+?)\s*$" % re.escape(key), block, re.M)
        return m.group(1) if m else default
    path = val("path")
    paths = re.findall(r'"([^"]+)"', path or "")
    paths = [p.replace("${local.regex.entity_id}", regex["entity_id"]).replace("${local.regex.merchant_id}", regex["merchant_id"]) for p in paths]
    methods = re.findall(r'"([A-Z]+)"', val("methods") or "") or None
    def num(k):
        v = val(k)
        return float(v) if v is not None and re.match(r"^[0-9.]+$", v) else None
    return {"paths": paths, "methods": methods, "rollout": num("rollout"), "impersonation_rollout": num("impersonation_rollout"),
            "impersonation_mode": (val("impersonation_mode") or "").strip('"') or None,
            "passport_enabler_config": "global" if "passport_enabler_global_config" in (val("passport_enabler_config") or "") else None,
            "hide_credentials": (val("hide_credentials") or "false") == "true", "ip_restrictions_rollout": num("ip_restrictions_rollout")}


def class_of(text, pos):
    cls = None
    for m in re.finditer(r"^\s{4}(authenticated_paths|identified_paths|paths|oauth_paths|login_as_mx_critical_paths|login_as_mx_non_critical_paths)\s*=\s*\{", text, re.M):
        if m.start() < pos:
            cls = m.group(1)
    return cls


def derive():
    regex = parse_regex_yaml()
    api_tf = (TK / "prod" / "api" / "api.tf").read_text()
    vars_tf = (TK / "prod" / "api" / "variables.tf").read_text()
    hosts = re.findall(r'"([a-z0-9.\-]+razorpay\.com)"', hcl_block(vars_tf, vars_tf.index("public_api_hosts")))
    # passport-enabler global config (prod/api/variables.tf)
    pe = hcl_block(vars_tf, vars_tf.index("passport_enabler_global_config"))
    af = re.search(r"auth_flows\s*=\s*\[(.*?)\]", pe, re.S).group(1)
    auth_flows = re.findall(r'"([a-z\-]+:[a-z\-]*)"', af)
    passport_enabler_global = {"conditions": [{"rollout": 1, "priority": 1, "match": {"auth_flows": auth_flows}}]}
    routes = {}
    for name in WANTED:
        key = name + "-prod-api"
        m = re.search(r"^\s{6}%s\s*=\s*\{" % re.escape(key), api_tf, re.M)
        if not m:
            routes[name] = {"missing": True}
            continue
        blk = hcl_block(api_tf, m.start())
        r = parse_route_block(blk, regex)
        r["class"] = class_of(api_tf, m.start())
        r["kong_route_name"] = key
        r["source"] = "terraform-kong/prod/api/api.tf:%d" % (api_tf[:m.start()].count("\n") + 1)
        routes[name] = r
    # payouts-ext (templates/payouts/payouts-ext.tf): direct-to-Payouts read route
    ext_tf = (TK / "templates" / "payouts" / "payouts-ext.tf").read_text()
    m = re.search(r"^\s{6}payout_get_by_id\s*=\s*\{", ext_tf, re.M)
    ext_route = parse_route_block(hcl_block(ext_tf, m.start()), regex)
    ext_route.update({"class": "authenticated_paths", "kong_route_name": "payout_get_by_id-payouts-ext", "source": "terraform-kong/templates/payouts/payouts-ext.tf:%d" % (ext_tf[:m.start()].count("\n") + 1)})

    def plugins_for(route, service_has_upstream_jwt=True):
        """module/plugins.tf: plugin set for one `authenticated_paths` route (cited)."""
        out = []
        out.append({"name": "consumer-authentication", "config": {
            "basic-auth-x": {"hide_credentials": route["hide_credentials"]},
            "jwt-x": {"default_secret_key": "authservice", "default_es_secret_key": "authservice_es", "claims_to_verify": ["exp", "nbf"], "secret_is_base64": False},
            "anonymous": "anonymous", "rollout": route["rollout"] if route["rollout"] is not None else 0},
            "source": "module/plugins.tf resource kong_plugin.consumer_authenticator"})
        out.append({"name": "basic-auth-x", "config": {"skip": True}, "source": "module/plugins.tf resource kong_plugin.disabled_basic_auth (route-level skip of the service-level basic-auth-x)"})
        out.append({"name": "jwt-x", "config": {"skip": True}, "source": "module/plugins.tf resource kong_plugin.disabled_jwt_x"})
        out.append({"name": "impersonation-grant", "config": {"skip": route["impersonation_mode"] == "skip", "anonymous": "anonymous",
                                                               "rollout": route["impersonation_rollout"] or 0, "merchant_rollout": 0,
                                                               "whitelisted": route["impersonation_mode"] == "optional"},
                    "source": "module/plugins.tf resource kong_plugin.impersonation_grant_authenticated_paths"})
        out.append({"name": "consumer-identifier", "config": {"skip": True}, "source": "module/plugins.tf resource kong_plugin.disabled_consumer_identifier"})
        if route.get("passport_enabler_config") == "global":
            out.append({"name": "passport-enabler", "config": passport_enabler_global, "source": "module/plugins.tf resource kong_plugin.passport_enabler + prod/api/variables.tf local.passport_enabler_global_config"})
        out.append({"name": "ip-restriction-x", "config": {"ip_restrictions_rollout": route["ip_restrictions_rollout"] or 0}, "source": "module/plugins.tf resource kong_plugin.ip-restriction-x"})
        return out

    services = {
        "prod-api": {
            "source": "terraform-kong/prod/api/api.tf module prod-api (upstream targets api.api.svc.cluster.local:80 = the API monolith)",
            "twin_upstream": {"host": "api-ingress", "port": 8080, "protocol": "http", "note": "CONTRACT_FAITHFUL_REPLACEMENT of the API monolith ingress (the monolith itself cannot be booted here, see M11_FINAL_REPORT.md)"},
            "service": {"name": "prod-api", "protocol": "http", "host": "api-ingress", "port": 8080, "path": "/", "retries": 0,
                        "connect_timeout": 60000, "write_timeout": 600000, "read_timeout": 600000},
            "hosts_in_production": hosts,
            "service_plugins": [
                {"name": "upstream-jwt", "config": {"issuer": "edge", "private_key_location": "/ssl/JWT_PRIVATE_KEY_V2", "public_key_location": "/ssl/JWT_PUBLIC_KEY_V2",
                                                     "header": "X-Passport-JWT-V1", "include_credential_type": False, "key_id": "edgev2", "shadow_mode": False},
                 "source": "prod/api/api.tf plugins.upstream-jwt (shadow_mode: schema default true only logs a missing key; the twin sets false so a missing key fails loudly -- documented deviation)"},
                {"name": "basic-auth-x", "config": {"anonymous": "__ANONYMOUS_CONSUMER_ID__", "hide_credentials": False},
                 "source": "prod/api/api.tf plugins.basic-auth-x (anonymous = prod/edge consumer `anonymous`)"},
            ],
            "routes": {n: r for n, r in routes.items() if not r.get("missing")},
        },
        "payouts-ext": {
            "source": "terraform-kong/templates/payouts/payouts-ext.tf (prod/payouts/config.tf: target payouts.razorpay.com:443 = the Payouts service)",
            "twin_upstream": {"host": "payouts-api", "port": 9400, "protocol": "http"},
            "service": {"name": "payouts-ext", "protocol": "http", "host": "payouts-api", "port": 9400, "path": "/", "retries": 0,
                        "connect_timeout": 2000, "write_timeout": 5000, "read_timeout": 5000},
            "hosts_in_production": ["payouts-ext.razorpay.com"],
            "service_plugins": [
                {"name": "upstream-jwt", "config": {"issuer": "edge", "private_key_location": "/ssl/JWT_PRIVATE_KEY_V2", "public_key_location": "/ssl/JWT_PUBLIC_KEY_V2",
                                                     "header": "X-Passport-JWT-V1", "include_credential_type": False, "key_id": "edgev2", "shadow_mode": False},
                 "source": "module/plugins.tf local.create_upstream_jwt (default upstream-jwt when authenticated_paths exist)"},
            ],
            "routes": {"payout_get_by_id": ext_route},
        },
    }
    for sname, s in services.items():
        for rname, r in s["routes"].items():
            r["plugins"] = plugins_for(r)
            # Production separates the two services by host (api.razorpay.com vs payouts-ext.razorpay.com). The twin has no
            # DNS, so the prod-api routes stay host-less (the default host set behind the twin's public port) and ONLY the
            # payouts-ext routes carry their production hosts -- otherwise GET /v1/payouts/{id} would exist twice with equal
            # priority and Kong's tie-break would silently pick one. A payouts-ext caller sends `Host: payouts-ext.razorpay.com`.
            r["hosts"] = list(s["hosts_in_production"]) if sname != "prod-api" else None
    missing = [n for n, r in routes.items() if r.get("missing")]
    doc = {
        "kind": "twin_kong_config", "schema_version": "m11.1",
        "derived_from": {"repository": "razorpay/terraform-kong", "sha": sh("git", "-C", str(TK), "rev-parse", "HEAD"),
                         "files": ["prod/api/api.tf", "prod/api/variables.tf", "templates/common/regex.yaml", "templates/payouts/payouts-ext.tf", "module/plugins.tf", "module/service.tf", "prod/edge/consumers.tf"]},
        "consumers": {"anonymous": {"username": "anonymous", "source": "prod/edge/consumers.tf resource kong_consumer.anonymous"},
                      "merchants": {"note": "one consumer per arena merchant, username = merchant id (kong-utils common.lua extract_consumer_details: a username without '~' is a merchant), tags m~l / m~t (parse_tags) and r~<role>; one basicauth_x_credentials row per key id (username = rzp_<mode>_<merchant id>, password hashed by the plugin: sha512(password .. consumer_id)). Seeded from ENV2_COMPOSE/seeds/generated/merchants.json + secrets/merchant-keys by trustpath/edge/provision_kong.py because the production key-sync path into Kong is not in any granted repository (production unknown PU-M11-1)."}},
        "route_hosts": {"twin": {"prod-api": None, "payouts-ext": ["payouts-ext.razorpay.com"]},
                        "note": "prod-api routes carry no `hosts` in the twin (no DNS: the twin's public port is api.razorpay.com's position); the payouts-ext routes carry their PRODUCTION hosts so the overlapping GET /v1/payouts/{id} path resolves exactly as in production (by host). Every production host list is recorded per service in hosts_in_production."},
        "services": services,
        "not_provisioned": {
            "global_plugins": {"source": "prod/edge/global-plugins.tf", "names": ["prometheus", "prometheus-x", "log-tracer", "zipkin-x", "rate-limiter-non-contextual", "rate-limiter-contextual", "lake-events", "response-transformer", "geo-router", "otel"],
                               "reason": "observability / rate-limiting globals, not on the authentication path; the rate limiters need the Redis cluster + limits config that is a production unknown (PU-M11-2)"},
            "api-internal_service": {"reason": "internal-app traffic (rzp_live + app secret, X-Razorpay-Account) reaches the monolith through the api-internal Kong service as plain `paths` (prod/api/api-internal.tf); the twin's internal apps call api-ingress directly, as they did in M7"},
            "api-dashboard-merchant_service": {"reason": "dashboard traffic goes Kong (user-session) -> dashboard app -> API proxy auth; the dashboard app is not cloned. Dashboard/session identity stays the api-ingress `dashboard:<session>` proxy-auth contract (BasicAuth::proxyAuth)"},
        },
        "missing_wanted_routes": missing,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n")
    return doc


if __name__ == "__main__":
    d = derive()
    print("wrote %s: %d prod-api routes, %d payouts-ext routes, missing=%s" % (OUT, len(d["services"]["prod-api"]["routes"]), len(d["services"]["payouts-ext"]["routes"]), d["missing_wanted_routes"]))
