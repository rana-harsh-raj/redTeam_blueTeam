# Source build

`scripts/bootstrap.sh` verifies the exact Git objects in `source-lock.json` and installs the pinned generators into `/Users/rana.singh/.rzp-architecture-replica/toolchains/s2p-v1`. It never copies credentials or configuration files into this repository.

`scripts/build.sh` exports clean trees with `git archive`, copies only the locked proto modules, generates ignored RPC packages in `.build/staging`, and compiles production entry points into `.build/bin`. If the default stage already exists, a later invocation automatically uses a timestamp and PID-namespaced clean run directory. An existing stage inside an explicitly supplied `S2P_BUILD_ROOT` is refused as uncertain.

The successful staging path is written to ignored `.build/current-generated-stage`; runtime builds consume this pointer instead of silently reusing an older default stage.

Every fresh build also compiles the complete `./cmd/...` package set for all three repositories. Vendor Experience's boot-tagged migration commands receive an additional explicit `-tags boot` build. Exact commands, resolved package names, output, timing, and return codes are recorded in `artifacts/build-command-packages.{json,log}`.

Private `github.com/razorpay` modules remain a declared prerequisite. The build accepts them from the operator's Go module cache or existing authenticated Git access. Tool hashes, source verification, a complete build log, and binary hashes are written under `artifacts/`.

The supported runtime build cross-compiles the requested entrypoint on the host with `GOOS=linux GOARCH=arm64 CGO_ENABLED=0 GOFLAGS=-mod=readonly`, then packages only that binary with `runtime/images/source-runtime.Dockerfile`. This keeps application compilation outside Docker and avoids passing Git credentials or a private module cache into Docker.

The repositories intentionally omit generated test mocks. Production entrypoints build without them; `artifacts/build-limitations.json` records the two known whole-module test-package gaps.

`scripts/build-runtime.sh` verifies that the generated vendor-payments stage still matches every tracked byte of the pinned source and the expected generated-file counts. Each invocation creates a new full-input-hash and timestamp-namespaced runtime stage, adds the local driver, replaces exactly two anchored tax-payment account constants with synthetic identifiers, cross-compiles Linux ARM64, and builds a uniquely tagged image containing only the binary and migrations. The tag digest covers the source lock/SHA, codegen and toolchain inputs, generated RPC trees, driver files, and digest-pinned Dockerfile. Patch provenance records symbols and whole-file hashes while intentionally omitting the original bank identifiers. Runtime stage/output overrides are explicitly rejected because Docker inputs must stay within the domain context; the source and generated-stage overrides remain supported. The generated immutable tag is written to `.build/runtime-image.env` as `S2P_SOURCE_IMAGE` for Compose consumers.
