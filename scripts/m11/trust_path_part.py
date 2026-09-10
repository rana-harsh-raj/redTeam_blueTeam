#!/usr/bin/env python3
"""M11: emit reports/domain/parts/m11-trust-path.json -- the discovery lane for the promoted real trust-path services
(edge gateway, Shield, banking-accounts, Workflow service), their datastores, the identities that flow between them, the
gateway routes derived from terraform-kong, and the `trust-path` journey family. Deterministic (no clock); every node
cites its source. Rank 190 (scripts/domain/build_graph.py LANE_RANK) so these design facts outrank the twin-inventory's
substitute view; the compose-definition overlay (rank 200) still decides `real_source_running` from the compose files.
"""
import csv
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KONG = json.loads((REPO / "ENV2_COMPOSE" / "trustpath" / "edge" / "kong-config.json").read_text())
INV = REPO / "reports" / "domain" / "REPOSITORY_INVENTORY_M6.csv"
OUT = REPO / "reports" / "domain" / "parts" / "m11-trust-path.json"


def shas():
    out = {}
    for row in csv.DictReader(INV.open()):
        if row.get("kind") == "repository" and row.get("sha"):
            out[row["repository"].split("/")[-1]] = row["sha"]
    out["terraform-kong"] = KONG["derived_from"]["sha"]
    return out


def node(nid, label, owner, crit, fid, refs, twin_ref, **extra):
    n = {"id": nid, "kind": nid.split(":")[0], "label": label, "owner_domain": owner, "criticality": crit, "fidelity": fid,
         "confidence": "confirmed", "source_refs": refs, "twin_ref": twin_ref}
    n.update(extra)
    return n


def main():
    S = shas()
    nodes, edges = [], []
    # ---- promoted services
    nodes.append(node("svc:edge-kong", "Edge gateway (razorpay/edge: Kong 3.4.2 + custom auth plugins; routes/plugins from terraform-kong prod)", "edge", "P0", "real_source_running",
                      ["edge/Dockerfile", "edge/kong.conf", "edge/kong-plugins/kong-plugin-basic-auth-x/kong/plugins/basic-auth-x/access.lua",
                       "edge/kong-plugins/kong-plugin-consumer-authentication/kong/plugins/consumer-authentication/access.lua",
                       "edge/kong-plugins/kong-plugin-upstream-jwt/kong/plugins/upstream-jwt/access.lua",
                       "edge/kong-plugins/kong-plugin-passport-enabler/kong/plugins/passport-enabler/access.lua",
                       "terraform-kong/prod/api/api.tf", "terraform-kong/module/plugins.tf", "terraform-kong/templates/payouts/payouts-ext.tf",
                       "ENV2_COMPOSE/trustpath/edge/kong-config.json", "ENV2_COMPOSE/build/m11/edge-kong.Dockerfile"],
                      "ENV2_COMPOSE/docker-compose.m11.yml service `edge-kong` (+ postgres-kong, edge-kong-migrate, edge-kong-config, edge-bridge)",
                      repo="repo:edge", sha=S.get("edge"), fidelity_evidence="real Kong + every plugin from the pinned edge clone; Postgres-backed; consumer-authentication/basic-auth-x/impersonation-grant/passport-enabler/ip-restriction-x per route and upstream-jwt (kid edgev2) per service exactly as terraform-kong prod-api declares",
                      identity_model="Kong consumers (username = merchant id, tags tenant~razorpay) + basicauth_x_credentials (username = rzp_<mode>_<mid>, sha512(pw..consumer_id), tags m~l/m~t, r~<role>); anonymous consumer for unauthenticated fall-through",
                      apis=["%s %s -> %s" % (",".join(r["methods"] or ["ANY"]), r["paths"][0], s["service"]["host"]) for s in KONG["services"].values() for r in s["routes"].values()],
                      entry_points=["edge-kong :8000 (proxy) / :8001 (admin, arena-internal)"], variants=["real (edge-kong)", "substitute (kong-lite)"]))
    nodes.append(node("svc:shield", "Shield risk rule engine (razorpay/shield, payouts deployment profile APP_MODE=rzpxprod)", "shield", "P1", "real_source_running",
                      ["shield/main.go", "shield/app/app.go", "shield/app/router/router.go", "shield/app/middlewares/auth.go", "shield/app/services/ruler/ruler.go",
                       "shield/conf/rzpxprod.toml", "shield/migrations/", "ENV2_COMPOSE/build/m11/build-shield.sh", "ENV2_COMPOSE/trustpath/shield/seed_rules.py"],
                      "ENV2_COMPOSE/docker-compose.m11.yml service `shield-web` (+ mysql-shield, redis-shield, shield-migrate, shield-seed)",
                      repo="repo:shield", sha=S.get("shield"),
                      fidelity_evidence="real source ADAPTED: the pinned repository built with ONE stubbed module (github.com/razorpay/fingerprint-sdk, 404 to the build identity, 4 symbols on the card cross-border path); real migrations, real rule engine, real BasicAuth over shieldAuthUser pairs, rules created through the real rule API",
                      apis=["POST /v1/rules/evaluate/payout (shield-sdk PayoutEvaluateRequest)", "POST /v1/merchants/{merchant_id}/rules", "GET /v1/status"],
                      identity_model="HTTP Basic over configured shieldAuthUser pairs (shieldAuthUser.payout <- payouts [shield.auth]); mode==test payouts skip evaluation (SKIPPING_SHIELD_RULE_EVALUATION)",
                      variants=["real (shield-web)", "substitute (shield-stub)"]))
    nodes.append(node("svc:banking-accounts", "Banking Accounts service (razorpay/banking-accounts)", "banking-accounts", "P1", "real_source_running",
                      ["banking-accounts/main.go", "banking-accounts/internal/bootstrap/routes.go", "banking-accounts/internal/routing/middleware/auth.go",
                       "banking-accounts/internal/database/repos/banking_account_repo.go", "banking-accounts/internal/database/migrations/", "ENV2_COMPOSE/build/m11/build-banking-accounts.sh",
                       "ENV2_COMPOSE/trustpath/bankingaccounts/render_seed.py"],
                      "ENV2_COMPOSE/docker-compose.m11.yml service `banking-accounts-api` (+ mysql-bas, bas-migrate, bas-seed)",
                      repo="repo:banking-accounts", sha=S.get("banking-accounts"),
                      fidelity_evidence="real binary from the pinned clone with its goose SQL migrations; the payouts routes GET /v0.2/payouts/shield/merchant/{mid}/details and GET /v0.2/merchant/{mid}/banking_account_by_account_number/{acct}/credentials run the real handlers/queries (businesses x banking_accounts on merchant_id)",
                      apis=["GET /v0.2/payouts/shield/merchant/{merchant_id}/details?account_number=", "GET /v0.2/merchant/{merchant_id}/banking_account_by_account_number/{account_number}/credentials", "GET /status"],
                      identity_model="Api-Token header == [api].token (middleware VerifyApiAuth); payouts sends Api-Token + X-Razorpay-MerchantId (pkg/bankingAccountService)",
                      variants=["real (banking-accounts-api)", "substitute (bankingaccounts-stub)"]))
    nodes.append(node("svc:workflows", "Workflow Service API (razorpay/workflows, Twirp; Cadence-backed approvals)", "workflows", "P0", "real_source_running",
                      ["workflows/cmd/api/main.go", "workflows/internal/boot/hooks/auth.go", "workflows/internal/entities/workflow/interactor.go",
                       "workflows/internal/entities/config/validation.go", "workflows/internal/client/types/common/callback.go", "workflows/internal/database/migrations/",
                       "proto/workflows/workflow/v1/workflow_api.proto", "proto/workflows/action/v1/action_api.proto", "proto/workflows/config/v1/config_api.proto",
                       "ENV2_COMPOSE/build/m11/build-workflows.sh", "ENV2_COMPOSE/trustpath/workflows/seed_configs.py"],
                      "ENV2_COMPOSE/docker-compose.m11.yml service `workflows-api` (+ mysql-workflows, cadence, workflows-migrate, workflows-seed)",
                      repo="repo:workflows", sha=S.get("workflows"),
                      fidelity_evidence="real Twirp API from the pinned clone (bindings generated with buf from the pinned razorpay/proto clone), real goose migrations, real Cadence server (ubercadence/server v1.4.1, MySQL persistence); Create resolves the config by owner/service/org and starts the Cadence workflow",
                      apis=["POST /twirp/rzp.workflows.workflow.v1.WorkflowAPI/{Create,Get,List,ListPending,Terminate}", "POST /twirp/rzp.workflows.action.v1.ActionAPI/{Create,CreateWithEntityId,List}",
                            "POST /twirp/rzp.workflows.config.v1.ConfigAPI/{Create,List,Get,Delete}", "POST /twirp/rzp.common.health.v1.HealthCheckAPI/Check"],
                      identity_model="HTTP Basic over [auth.*] pairs (hooks.Auth): payouts presents [workflow.auth] (== [auth.payouts]); callbacks into payouts use clients.payouts_live (rzp_live + payouts [auth.workflow])",
                      variants=["real (workflows-api + workflows-worker + cadence)", "substitute (workflow-engine, M5 reconstruction)"]))
    nodes.append(node("svc:workflows-workers", "Workflow Service Cadence workers (cmd/workers: the approval state machine)", "workflows", "P0", "real_source_running",
                      ["workflows/cmd/workers/main.go", "workflows/internal/client/types/approval/workflow.go", "workflows/pkg/cadence/factory.go"],
                      "ENV2_COMPOSE/docker-compose.m11.yml service `workflows-worker` (WORKFLOW_TYPE=approval, WORKFLOW_DOMAINS=payouts)",
                      repo="repo:workflows", sha=S.get("workflows"),
                      fidelity_evidence="real worker binary polling the real Cadence server (TChannel :7933, domain payouts, task list payouts-approval)"))
    # ---- datastores
    for nid, label, img, twin in (("db:edge/postgres", "PostgreSQL 15 -- Kong schema + plugin tables (basicauth_x_credentials, impersonation_grant, key identifiers)", "postgres:15-alpine", "postgres-kong"),
                                  ("db:shield/mysql", "MySQL 8.0 -- shield schema (razorpay/shield migrations)", "mysql:8.0", "mysql-shield"),
                                  ("db:shield/redis", "Redis 7 single-node cluster (shield uses a go-redis ClusterClient)", "redis:7-alpine", "redis-shield"),
                                  ("db:banking-accounts/mysql", "MySQL 8.0 -- banking_account schema (goose SQL migrations)", "mysql:8.0", "mysql-bas"),
                                  ("db:workflows/mysql", "MySQL 8.0 -- workflows schema + cadence/cadence_visibility", "mysql:8.0", "mysql-workflows"),
                                  ("db:workflows/cadence", "Uber Cadence server v1.4.1 (auto-setup, MySQL persistence, domain payouts)", "ubercadence/server:v1.4.1-auto-setup", "cadence")):
        owner = nid.split(":")[1].split("/")[0]
        nodes.append(node(nid, label, owner, "P0" if owner in ("edge", "workflows") else "P1", "real_source_running", ["ENV2_COMPOSE/docker-compose.m11.yml"],
                          "ENV2_COMPOSE/docker-compose.m11.yml service `%s` (image %s)" % (twin, img)))
    # ---- promoted repositories (the lane upgrades their realization)
    for repo, label in (("edge", "razorpay/edge (Kong plugins: passport minting, auth flows)"), ("shield", "razorpay/shield (risk rule engine)"),
                        ("banking-accounts", "razorpay/banking-accounts"), ("workflows", "razorpay/workflows (REAL Workflow Service, Go/Twirp/Cadence)"),
                        ("terraform-kong", "razorpay/terraform-kong (declarative Kong routes/plugins)")):
        nodes.append(node("repo:" + repo, label, repo, "P0", "real_source_running" if repo != "terraform-kong" else "real_source_mapped_not_running",
                          ["reports/domain/REPOSITORY_INVENTORY_M6.csv"], "ENV2_COMPOSE/build/m11/" if repo != "terraform-kong" else "ENV2_COMPOSE/trustpath/edge/kong-config.json (derived)", sha=S.get(repo)))
    # ---- identities
    ids = [("identity:edge-passport-edgev2", "Edge passport (X-Passport-JWT-V1, RS256 kid edgev2) minted by kong-plugin-upstream-jwt per authenticated request", ["edge/kong-plugins/kong-plugin-upstream-jwt/kong/plugins/upstream-jwt/access.lua", "terraform-kong/prod/api/api.tf"]),
           ("identity:monolith-passport-apiv1", "Monolith passport (kid apiv1) minted toward the Payouts service by the monolith replacement (api-ingress) in the api slot payouts registers (pkg/passport/handler.go)", ["ENV2_COMPOSE/substitutes/api-ingress/server.py", "payouts/pkg/passport/handler.go"]),
           ("identity:kong-merchant-credential", "Merchant API key as a Kong basic-auth-x credential (username rzp_<mode>_<mid>, tags m~l/m~t, r~<role>) on the merchant's consumer", ["edge/kong-plugins/kong-plugin-basic-auth-x/kong/plugins/basic-auth-x/daos.lua", "ENV2_COMPOSE/trustpath/edge/provision_kong.py"]),
           ("identity:shield-basic-payouts", "payouts -> Shield HTTP Basic (payouts [shield.auth] == shieldAuthUser.payout)", ["shield/app/middlewares/auth.go", "payouts/config/prod.toml"]),
           ("identity:bas-api-token", "payouts -> banking-accounts Api-Token header (== [api].token)", ["banking-accounts/internal/routing/middleware/auth.go", "payouts/pkg/bankingAccountService/fetch_merchant_details.go"]),
           ("identity:workflows-basic-payouts", "payouts -> Workflow service HTTP Basic (payouts [workflow.auth] == workflows [auth.payouts])", ["workflows/internal/boot/hooks/auth.go", "payouts/pkg/workflow/workflow_create.go"]),
           ("identity:workflows-callback-rzp-live", "Workflow service -> payouts callbacks: clients.payouts_live (rzp_live + payouts [auth.workflow]) on /v1/workflow/state and /v1/payouts/payouts_internal/{id}/{approve,reject}", ["workflows/internal/client/types/common/callback.go", "payouts/internal/routing/router/workflow_routes.go", "payouts/internal/routing/router/payout_internal_routes.go"])]
    for nid, label, refs in ids:
        nodes.append(node(nid, label, "trust-path", "P0", "real_source_running", refs, "ENV2_COMPOSE/docker-compose.m11.yml + config/generate.py TRUST tokens"))
    # ---- gateway routes
    for sname, s in KONG["services"].items():
        for rname, r in s["routes"].items():
            rid = "route:edge-kong/%s %s" % ((r["methods"] or ["ANY"])[0], r["paths"][0])
            nodes.append(node(rid, "%s (%s, %s)" % (r["kong_route_name"], r["class"], ", ".join(p["name"] for p in r["plugins"])), "edge", "P0", "real_source_running",
                              [r["source"], "terraform-kong/module/plugins.tf"], "ENV2_COMPOSE/trustpath/edge/kong-config.json services.%s.routes.%s" % (sname, rname),
                              kong_route=rname, kong_service=sname, plugins=[p["name"] for p in r["plugins"]]))
            edges.append({"from": "svc:edge-kong", "to": rid, "type": "owns", "confidence": "confirmed", "source_refs": [r["source"]]})
            edges.append({"from": rid, "to": "identity:kong-merchant-credential", "type": "gated_by", "via": "consumer-authentication (basic-auth-x, rollout %s)" % r["rollout"], "confidence": "confirmed", "source_refs": ["terraform-kong/module/plugins.tf"]})
            target = "sub:api-ingress" if sname == "prod-api" else "svc:payouts-api"
            edges.append({"from": rid, "to": target, "type": "calls", "via": "Kong service %s -> %s:%s" % (sname, s["service"]["host"], s["service"]["port"]), "confidence": "confirmed", "source_refs": [s["source"]]})
    # ---- edges: call/callback/datastore/implements
    E = [("svc:edge-kong", "sub:api-ingress", "calls", "prod-api service (the monolith replacement); upstream-jwt passport kid edgev2 + X-PASSPORT-USABLE"),
         ("svc:edge-kong", "svc:payouts-api", "calls", "payouts-ext service (direct-to-Payouts read route)"),
         ("svc:edge-kong", "identity:edge-passport-edgev2", "authenticates", "kong-plugin-upstream-jwt mints per request"),
         ("sub:api-ingress", "identity:monolith-passport-apiv1", "authenticates", "monolith replacement mints the api-slot passport toward Payouts"),
         ("sub:api-ingress", "identity:edge-passport-edgev2", "authenticates", "DecodePassportJwt/PassportUtil ingest: verify + consumer/mode cross-check"),
         ("svc:payouts-api", "svc:shield", "calls", "POST /v1/rules/evaluate/payout (pkg/shield, [shield].host, 200ms fail-open)"),
         ("svc:payouts-api", "svc:banking-accounts", "calls", "GET /v0.2/payouts/shield/merchant/{mid}/details (pkg/bankingAccountService)"),
         ("svc:payouts-api", "svc:workflows", "calls", "POST /twirp/rzp.workflows.workflow.v1.WorkflowAPI/Create (pkg/workflow WfCreate, 100ms)"),
         ("svc:payouts-api", "identity:shield-basic-payouts", "authenticates", "[shield.auth]"),
         ("svc:payouts-api", "identity:bas-api-token", "authenticates", "[banking_account_service.auth].password as Api-Token"),
         ("svc:payouts-api", "identity:workflows-basic-payouts", "authenticates", "[workflow.auth]"),
         ("svc:workflows-workers", "svc:payouts-api", "callback", "clients.payouts_live: /v1/workflow/state (created/processed) and /v1/payouts/payouts_internal/{id}/{approve,reject}"),
         ("svc:workflows-workers", "identity:workflows-callback-rzp-live", "authenticates", "clients.payouts_live Basic"),
         ("svc:workflows", "db:workflows/cadence", "depends_on", "StartWorkflow via TChannel :7933"),
         ("svc:workflows", "db:workflows/mysql", "depends_on", "gorm"), ("svc:workflows-workers", "db:workflows/cadence", "depends_on", "worker polling"),
         ("svc:workflows-workers", "db:workflows/mysql", "depends_on", "gorm"), ("db:workflows/cadence", "db:workflows/mysql", "depends_on", "cadence + cadence_visibility schemas"),
         ("svc:shield", "db:shield/mysql", "depends_on", "gorm"), ("svc:shield", "db:shield/redis", "depends_on", "ClusterClient + asynq + redis queue driver"),
         ("svc:banking-accounts", "db:banking-accounts/mysql", "depends_on", "gorm"), ("svc:edge-kong", "db:edge/postgres", "depends_on", "kong.db (consumers, credentials, routes, plugins)"),
         ("svc:banking-accounts", "sub:api-ingress", "calls", "GET /jwks at boot (goutils passport v4 InitHandler)"),
         ("sub:kong-lite", "svc:edge-kong", "implements", "substitute variant of role gateway (R12)"),
         ("sub:shield-stub", "svc:shield", "implements", "substitute variant of role shield (R12)"),
         ("sub:bankingaccounts-stub", "svc:banking-accounts", "implements", "substitute variant of role banking-accounts (R12)"),
         ("sub:workflow-engine", "svc:workflows", "implements", "substitute variant of role workflows (R12; M5 reconstruction)")]
    for f, t, ty, via in E:
        edges.append({"from": f, "to": t, "type": ty, "via": via, "confidence": "confirmed", "source_refs": ["ENV2_COMPOSE/docker-compose.m11.yml"]})
    fam = {"id": "family:trust-path", "label": "Critical trust path through real components (M11)", "priority": "P0",
           "description": "merchant API-key authentication and identity propagation through the real edge gateway, route-level authorization, cross-merchant denial, beneficiary ownership, service-to-service identity, real Shield rule evaluation, real banking-accounts merchant lookup, maker-checker through the real Workflow service, restart/cache invalidation and idempotency",
           "entry_points": ["edge-kong :8000 /v1/payouts, /v1/contacts, /v1/fund_accounts"],
           "components": ["svc:edge-kong", "sub:api-ingress", "svc:payouts-api", "svc:shield", "svc:banking-accounts", "svc:workflows", "svc:workflows-workers",
                          "db:edge/postgres", "db:shield/mysql", "db:shield/redis", "db:banking-accounts/mysql", "db:workflows/mysql", "db:workflows/cadence",
                          "identity:edge-passport-edgev2", "identity:monolith-passport-apiv1", "identity:kong-merchant-credential", "identity:shield-basic-payouts",
                          "identity:bas-api-token", "identity:workflows-basic-payouts", "identity:workflows-callback-rzp-live"],
           "variants": ["api_key_auth", "identity_propagation", "dashboard_session", "internal_app_auth", "route_authorization", "cross_merchant_denial",
                        "beneficiary_ownership", "service_identity", "maker_checker", "restart_cache", "idempotency", "shield_rules", "bas_lookup"],
           "source_refs": ["reports/implementation/M11_FINAL_REPORT.md"]}
    doc = {"lane": "m11-trust-path",
           "sources": [{"repo": "terraform-kong", "path": "prod/api/api.tf", "sha": S.get("terraform-kong")}, {"repo": "edge", "path": "kong-plugins/", "sha": S.get("edge")},
                       {"repo": "shield", "path": "app/", "sha": S.get("shield")}, {"repo": "banking-accounts", "path": "internal/", "sha": S.get("banking-accounts")},
                       {"repo": "workflows", "path": "internal/", "sha": S.get("workflows")}, {"repo": "twin", "path": "ENV2_COMPOSE/docker-compose.m11.yml"}],
           "nodes": sorted(nodes, key=lambda n: n["id"]), "edges": sorted(edges, key=lambda e: (e["from"], e["to"], e["type"], e.get("via") or "")), "families": [fam],
           "open_questions": ["PU-M11-1 production merchant-credential sync into Kong (not in any granted repository)",
                              "PU-M11-2 production rate-limiter/global plugin configuration and Redis cluster",
                              "PU-M11-3 production values of the edge/monolith/BAS/Shield/Workflow secrets and the Shield auth slot payouts uses (PAYOUTS_SHIELD_AUTH_USERNAME)",
                              "PU-M11-4 the API monolith itself cannot be booted (private composer packages 404, registry credentials): api-ingress remains a contract-faithful replacement",
                              "PU-M11-5 razorpay/account-service (ASV) is not granted to this identity: asv-stub remains"]}
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=False) + "\n")
    print("wrote %s: %d nodes, %d edges" % (OUT, len(nodes), len(edges)))


if __name__ == "__main__":
    main()
