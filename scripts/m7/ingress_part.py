#!/usr/bin/env python3
"""M7 graph part: the shared API-monolith ingress (reports/domain/parts/m7-ingress.json).

Nodes: the substitute (sub:api-ingress, high_fidelity_replacement = CONTRACT_FAITHFUL_REPLACEMENT), the
api-monolith routes it serves (re-declared with fidelity high_fidelity_replacement + twin_ref, from
substitutes/api-ingress/contract/routes.json, so the identity-ingress lane's graph_only nodes are upgraded
only where the ingress actually serves them), the four identity contexts, the persistent tables that carry
ownership / idempotency / audit / tag-back, the trust boundaries (as identity:trust-boundary:* nodes), and
the M7 families. Edges: implements, authenticates, gated_by, calls (route -> PS route), reads/writes, owns.
Everything cites contract/routes.json (itself derived from Route.php) or CONTRACT.md.
"""
import json, sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONTRACT = REPO / "ENV2_COMPOSE/substitutes/api-ingress/contract/routes.json"
OUT = REPO / "reports/domain/parts/m7-ingress.json"
SUB = "sub:api-ingress"
API_SHA = None

ROLE_IDENTITY = {"merchant": "identity:merchant-api-key", "dashboard": "identity:dashboard-session",
                 "batch": "identity:service-basic-auth:batch", "internal": "identity:ingress-app-secret",
                 "admin": "identity:admin-token"}
ROLE_BOUNDARY = {"merchant": "identity:trust-boundary:public-merchant", "dashboard": "identity:trust-boundary:public-merchant",
                 "batch": "identity:trust-boundary:internal-service", "internal": "identity:trust-boundary:internal-service",
                 "admin": "identity:trust-boundary:administrator"}


def node(nid, label, fidelity, crit="P0", **kw):
    n = {"id": nid, "kind": nid.split(":", 1)[0], "label": label, "owner_domain": kw.pop("owner_domain", "api-monolith"),
         "criticality": crit, "fidelity": fidelity, "confidence": kw.pop("confidence", "confirmed")}
    n.update(kw)
    return n


def edge(a, b, t, via, refs=None, **kw):
    e = {"from": a, "to": b, "type": t, "via": via, "confidence": "confirmed"}
    if refs:
        e["source_refs"] = refs
    e.update(kw)
    return e


def main():
    c = json.loads(CONTRACT.read_text())
    api_sha = c["sources"]["api"]["sha"]
    nodes, edges = [], []
    twin = "ENV2_COMPOSE/substitutes/api-ingress/server.py"
    nodes.append(node(SUB, "api-ingress (shared API-monolith ingress)", "high_fidelity_replacement", repo="repo:api", sha=api_sha,
                      source_refs=["ENV2_COMPOSE/substitutes/api-ingress/CONTRACT.md", "ENV2_COMPOSE/substitutes/api-ingress/contract/routes.json"],
                      twin_ref=twin, entry_points=["compose service api-ingress :8080"], health="GET :8080/health",
                      identity_model="merchant key | dashboard session | internal app secret + X-Razorpay-Account | admin token",
                      authorization="Route.php group per route; Route::$internalApps allow-list; merchant-scoped ownership records",
                      tables=["table:api-ingress/resources", "table:api-ingress/idempotency", "table:api-ingress/audit", "table:api-ingress/payout_details", "table:api-ingress/banking_accounts"],
                      fidelity_evidence="CONTRACT_FAITHFUL_REPLACEMENT: 13 host contract tests + M7 journeys (reports/implementation/m6-journeys.json family:shared-ingress)",
                      m7_label="CONTRACT_FAITHFUL_REPLACEMENT"))
    edges.append(edge(SUB, "svc:api-monolith", "implements", "ingress responsibilities of the selected surface (contract/routes.json)"))
    for tid, label, what in (("table:api-ingress/resources", "ownership records (fund accounts, contacts)", "resource_ownership"),
                             ("table:api-ingress/banking_accounts", "merchant banking accounts", "resource_ownership"),
                             ("table:api-ingress/idempotency", "merchant idempotency keys", "MerchantIdempotencyHandler"),
                             ("table:api-ingress/audit", "request audit + correlation", "correlation"),
                             ("table:api-ingress/payout_details", "tax-payment tag-back", "PayoutsDetails updateTaxPayment"),
                             ("table:api-ingress/sessions", "synthetic dashboard sessions", "proxy auth"),
                             ("table:api-ingress/callbacks", "SourceUpdater relays to vendor-payments", "VendorPaymentUpdater")):
        nodes.append(node(tid, label, "high_fidelity_replacement", twin_ref=twin + " (SQLite /data/ingress.sqlite)", source_refs=["ENV2_COMPOSE/substitutes/api-ingress/CONTRACT.md"], notes=what))
        edges.append(edge(SUB, tid, "writes", what))
    for iid, label, refs in (("identity:ingress-app-secret", "internal application secret (rzp_live + app secret, appAuth)", ["api/app/Http/BasicAuth/BasicAuth.php:1145-1200", "api/app/Http/Route.php:12922"]),
                             ("identity:trust-boundary:public-merchant", "trust boundary: public merchant / dashboard user", ["ENV2_COMPOSE/substitutes/api-ingress/CONTRACT.md"]),
                             ("identity:trust-boundary:internal-service", "trust boundary: internal service applications", ["api/app/Http/Route.php:6027", "api/app/Http/Route.php:12922"]),
                             ("identity:trust-boundary:administrator", "trust boundary: administrator", ["api/app/Http/Route.php:8727"]),
                             ("identity:trust-boundary:payouts-service", "trust boundary: payouts-service -> api (auth_monolith_shared)", ["ENV2_COMPOSE/config/templates/base/payouts/arena.toml"])):
        nodes.append(node(iid, label, "high_fidelity_replacement", twin_ref=twin, source_refs=refs))
    for existing in ("identity:merchant-api-key", "identity:dashboard-session", "identity:dashboard-otp", "identity:admin-token", "identity:service-basic-auth:batch",
                     "identity:service-basic-auth:vendor-payments", "identity:service-basic-auth:workflows", "identity:passport-jwt-minted-by-api"):
        nodes.append(node(existing, existing.split(":", 1)[1], "high_fidelity_replacement", twin_ref=twin, source_refs=["ENV2_COMPOSE/substitutes/api-ingress/CONTRACT.md"],
                          fidelity_evidence="minted / verified by api-ingress in the twin"))
    edges.append(edge("identity:merchant-api-key", "identity:trust-boundary:public-merchant", "depends_on", "private auth"))
    edges.append(edge("identity:dashboard-session", "identity:trust-boundary:public-merchant", "depends_on", "proxy auth"))
    edges.append(edge("identity:ingress-app-secret", "identity:trust-boundary:internal-service", "depends_on", "privilege auth"))
    edges.append(edge("identity:admin-token", "identity:trust-boundary:administrator", "depends_on", "admin auth"))
    edges.append(edge("identity:merchant-api-key", "table:api-ingress/resources", "owns", "merchant_id column; findByPublicIdAndMerchant", ["api/app/Models/FundAccount/Service.php:210-212", "api/app/Base/RepositoryFetch.php:1431"]))
    edges.append(edge("identity:service-basic-auth:vendor-payments", "table:api-ingress/resources", "writes", "contact_create_internal / fund_account_create_internal create merchant-owned records"))
    for name, r in sorted(c["routes"].items()):
        rid = "route:api-monolith/%s %s" % (r["method"], r["path"].replace("/v1/", "", 1))
        role = r["ingress_role"]
        groups = sorted(r["groups"])
        nodes.append(node(rid, "%s %s (%s)" % (r["method"], r["path"], name), "high_fidelity_replacement",
                          repo="repo:api", sha=api_sha, source_refs=r["source_refs"], twin_ref=twin,
                          identity_model="Route::$%s" % "/".join(g for g in groups if g in ("private", "proxy", "internal", "admin")),
                          authorization=("Route::$internalApps " + ",".join(r["internal_apps"])) if r["internal_apps"] else "",
                          fidelity_evidence="served by api-ingress (contract/routes.json '%s'); PS upstream %s" % (name, r.get("ps_upstream") or "ingress-owned record"),
                          family="family:shared-ingress", m7_label="CONTRACT_FAITHFUL_REPLACEMENT", route_name=name))
        edges.append(edge(rid, SUB, "implements", "api-ingress route handler"))
        edges.append(edge(ROLE_IDENTITY[role], rid, "authenticates", "ingress context class", r["source_refs"][:2]))
        edges.append(edge(rid, ROLE_BOUNDARY[role], "gated_by", "trust boundary"))
        if r.get("ps_upstream"):
            m, p = r["ps_upstream"].split(" ", 1)
            edges.append(edge(rid, "route:payouts/%s %s" % (m, p), "calls", "forwarded with cred.API + passport", [r["ps_upstream_evidence"]["file"] + ":%d" % r["ps_upstream_evidence"]["line"]] if isinstance(r.get("ps_upstream_evidence"), dict) and r["ps_upstream_evidence"].get("file") else None))
        else:
            edges.append(edge(rid, "table:api-ingress/resources" if "fund_account" in name or "contact" in name else ("table:api-ingress/payout_details" if "tax" in name else "table:api-ingress/banking_accounts" if "banking" in name else "table:api-ingress/sessions"), "reads", "ingress-owned record"))
        if "idempotentRoutesConfig" in groups:
            edges.append(edge(rid, "table:api-ingress/idempotency", "writes", "MerchantIdempotencyHandler"))
    # callback relay (SourceUpdater -> vendor-payments) and the connected-journey queue/worker/topic set
    nodes.append(node("topic:s2p/add-tds-entry", "Kafka topic add-tds-entry (vendor-payments initiate-tds input)", "high_fidelity_replacement", owner_domain="vendor-payments",
                      twin_ref="ENV2_COMPOSE/docker-compose.s2p.yml s2p-kafka (Redpanda)", source_refs=["DOMAIN_REPLICAS/source_to_pay/spec/interfaces.yaml"], crit="P1"))
    edges.append(edge("route:payouts/POST /v1/payouts/internal_contact_payout", SUB, "callback", "PS re-resolves the beneficiary through GET fund_accounts_internal (x-razorpay-account = merchant)", ["payouts/internal/app/fundAccountCache/core.go:199"]))
    edges.append(edge(SUB, "route:vendor-payments/POST /twirp/vendorpayments.Vendorpayments/PayoutStatusChange", "callback",
                      "SourceUpdater relay: payouts_service/source_update (source_type tax_payments) -> PayoutStatusChange", ["api/app/Models/Payout/SourceUpdater/Factory.php:31-35", "api/app/Services/VendorPayments/Service.php:480-503"]))
    edges.append(edge("queue:prod-api-payout-source-updater-live", SUB, "produces", "payouts payout_source_updater worker -> POST payouts_service/source_update via the ingress"))
    families = [
        {"id": "family:shared-ingress", "label": "Shared API-monolith ingress (M7)", "priority": "P0",
         "description": "merchant, dashboard, internal-application and administrator paths through the shared ingress into the real Payouts service",
         "entry_points": ["api-ingress :8080 /v1/*"], "components": [SUB] + ["route:api-monolith/%s %s" % (r["method"], r["path"].replace("/v1/", "", 1)) for r in c["routes"].values()],
         "variants": ["success", "failure", "idempotency", "async_state", "direct", "approval", "batch", "admin", "tenant_isolation", "restart"],
         "fidelity": "high_fidelity_replacement", "source_refs": ["ENV2_COMPOSE/substitutes/api-ingress/CONTRACT.md"]},
        {"id": "family:cross-domain-s2p", "label": "Cross-domain Source-to-Pay TDS remittance (M7)", "priority": "P0",
         "description": "vendor-payments TDS accrual -> tag-back -> Pay -> shared ingress -> real payouts -> SourceUpdater callback",
         "entry_points": ["topic:s2p/add-tds-entry", "s2p-vp-source /_replica/pay"], "components": [SUB, "svc:s2p/vendor-payments", "topic:s2p/add-tds-entry"],
         "variants": ["success", "failure", "idempotency", "async_state"], "fidelity": "real_source_running", "source_refs": ["RED_LOOP/m6/journeys/j_s2p.py"]},
        {"id": "family:source-to-pay-tds", "label": "Source-to-Pay TDS domain (namespaced)", "priority": "P1",
         "description": "the vendor-payments / vendor-experience / accounting-integrations domain as mapped by DOMAIN_REPLICAS/source_to_pay",
         "entry_points": ["DOMAIN_REPLICAS/source_to_pay/integration/unified-graph-patch.json"], "components": ["svc:s2p/vendor-payments", "svc:s2p/vendor-experience", "svc:s2p/accounting-integrations"],
         "variants": ["success"], "fidelity": "real_source_running", "source_refs": ["DOMAIN_REPLICAS/source_to_pay/spec/runtime-topology.yaml"]},
    ]
    part = {"lane": "m7-ingress", "generated_at": datetime.now(timezone.utc).isoformat(),
            "sources": [{"repo": "razorpay/api", "sha": api_sha, "path": c["sources"]["api"]["path"]},
                        {"repo": "razorpay/payouts", "sha": c["sources"]["payouts"]["sha"], "path": c["sources"]["payouts"]["path"]}],
            "nodes": nodes, "edges": edges, "families": families,
            "open_questions": ["production route selection for payout create (Edge->PS direct vs Edge->API->PS) is not decidable from source alone (M7_PRODUCTION_UNKNOWNS.md)"]}
    OUT.write_text(json.dumps(part, indent=1))
    print(json.dumps({"nodes": len(nodes), "edges": len(edges), "families": len(families), "out": str(OUT.relative_to(REPO))}))


if __name__ == "__main__":
    main()
