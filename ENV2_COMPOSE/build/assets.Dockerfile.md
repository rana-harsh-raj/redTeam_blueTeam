# Runtime asset layers (root agent, 2026-09-04)
payouts and cfa open IFSC data files at paths relative to WORKDIR:
- payouts: `$WORKDIR/github.com/razorpay/ifsc/v2@v2.0.43/src/{banks,banknames,IFSC,sublet,custom-sublets}.json` (from the Go module cache)
- cfa: `$WORKDIR/github.com/razorpay/cfa/pkg/ifsc/*.json` (+ the ifsc module files)
The images `rzp-arena/payouts:local` and `rzp-arena/cfa:local` were rebuilt with these files under `/app/...`; run containers with `WORKDIR=/app`, `-w /app`, `APP_ENV=arena`, and mount generated config at `/app/config/`. Ledger/fts/x-balances resolve `config/` relative to WORKDIR too (fts: cwd).
