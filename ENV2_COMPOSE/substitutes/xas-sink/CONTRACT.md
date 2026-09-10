# xas-sink — CONTRACT (skeleton)

Placeholder for x-account-statements' role in Env 2 (BOM §2 F2 substitutes:
"XAS queue sink" — present because payouts/ledger/FTS enqueue
statement-relevant events that a real XAS would consume, but Env 2's golden
flows B/G1-G8 don't assert on XAS behaviour; that's Env 4's job, per BOM §3
"Additional F3: x-account-statements"). This container exists so those
enqueues have *something* draining them (avoiding unbounded SQS queue growth
during a long-running arena) without standing up real x-account-statements.

## Behaviour

`GET /health` -> 200 (only endpoint implemented in this pass).

**TODO (explicit gap, not implemented in this scaffold)**: actual SQS
long-polling of the XAS queues named in
`INITIAL_PAYOUTS_ENVIRONMENT_BOM.md` §1 ("xas 12 queues") against
`localstack:4566`. Deferred because (a) it needs an AWS SigV4 client, which
this stub deliberately avoids pulling in as a pip dependency to stay
stdlib-only per the Env 2 constraints, and (b) Env 2's own flow catalogue
does not require it. When Env 4 is scaffolded, replace this file with either
a `boto3`-based drainer (accepting the one pip dependency) or a minimal
stdlib SigV4-signed poller.

## Health

`GET /health` -> 200.
