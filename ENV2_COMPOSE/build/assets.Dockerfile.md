# Runtime asset layers (root agent, 2026-09-04)
payouts and cfa open IFSC data files at paths relative to WORKDIR:
- payouts: `$WORKDIR/github.com/razorpay/ifsc/v2@v2.0.43/src/{banks,banknames,IFSC,sublet,custom-sublets}.json` (from the Go module cache)
- cfa: `$WORKDIR/github.com/razorpay/cfa/pkg/ifsc/*.json` (+ the ifsc module files)
The images `rzp-arena/payouts:local` and `rzp-arena/cfa:local` were rebuilt with these files under `/app/...`; run containers with `WORKDIR=/app`, `-w /app`, `APP_ENV=arena`, and mount generated config at `/app/config/`. Ledger/fts/x-balances resolve `config/` relative to WORKDIR too (fts: cwd).

## Milestone 1 addition (2026-09-05): payouts bank error-mapping files

`internal/helpers/read_file.go:ReadErrorFile` resolves `$WORKDIR/files/error/<errorType>.json`
(`WORKDIR=/app`) and, per `read_file.go:22-25`, returns `nil` with **no error** when the file is missing --
every bank error-code -> public `{source,reason,description}` mapping
(`internal/app/payoutStatusDetails/statusProcessor.go:112`, `internal/app/payouts/payout_error.go:63`,
`internal/app/favStatusDetails/core.go:141`) was silently degrading to the fallback because
`build/docker/prod/Dockerfile.api:80`'s `COPY --from=builder /src/files/ /app/files/` had no equivalent in
`build-host.sh`. `stage_assets_payouts` now also stages `$REPOS_ROOT/payouts/files/error/{payout_error,fav_error}.json`
into `/app/files/error/*.json` at 0644 via `stage_public_json` (same non-secret, public-reference-data treatment
as the IFSC files above; grepped for password/secret/api_key/private_key/token/credential -- zero value hits,
only unrelated `VAULT_TOKEN_*` error-code *keys*). Requires a `payouts` image rebuild to take effect --
`rzp-arena/payouts:local` / `:v1-candidate` built before this change do not have `/app/files/error/`.
