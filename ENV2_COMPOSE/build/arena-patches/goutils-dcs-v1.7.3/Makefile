DBG_MAKEFILE ?=
ifeq ($(DBG_MAKEFILE),1)
    $(warning ***** starting Makefile for goal(s) "$(MAKECMDGOALS)")
    $(warning ***** $(shell date))
else
    # If we're not debugging the Makefile, don't echo recipes.
    MAKEFLAGS += -s
endif
# We don't need make's built-in rules.
MAKEFLAGS += --no-builtin-rules
# Be pedantic about undefined variables.
MAKEFLAGS += --warn-undefined-variables
.SUFFIXES:

# TODO: push the image to razorpay org and fix this once u have the access
ORG ?= alokrzp

###
### These variables should not need tweaking.
###
# Used internally.  Users should pass GOOS and/or GOARCH.
OS := $(if $(GOOS),$(GOOS),$(shell go env GOOS))
ARCH := $(if $(GOARCH),$(GOARCH),$(shell go env GOARCH))

# Proto variables
PROTO_BRANCH := master
BUF_VERSION := v0.42.1
PROTO_BUILD_IMAGE  ?= alokrzp/bufbuild:1.6.0_$(OS)_$(ARCH)
PROTO_ROOT := proto/
RPC_ROOT := rpc/
PROTO_GIT_URL := "https://github.com/razorpay/proto"

proto-clean:
	rm -rf $(PROTO_ROOT)

.PHONY: proto-fetch ## Fetch proto files frrm remote repo // TODO to move this under docker container
proto-fetch: ## Fetch proto files from remote repo
	rm -rf proto
	echo "# fetching proto files from razorpay/proto repo, branch: $(PROTO_BRANCH)"
	mkdir $(PROTO_ROOT) && \
	cd $(PROTO_ROOT) && \
	git init --quiet && \
	git config core.sparseCheckout true && \
	cp $(CURDIR)/proto_modules .git/info/sparse-checkout && \
	git remote add origin $(PROTO_GIT_URL)  && \
	git fetch origin $(PROTO_BRANCH) --quiet && \
	git checkout $(PROTO_BRANCH) --quiet && \
	cp ../buf.yaml .

proto-generate: # @HELP generate code for clients from proto files
proto-generate:
	echo "# generating code under rpc/"
	docker run                                                  \
		-i                                                      \
		--rm                                                    \
		-v $$(pwd):/src                                         \
		-w /src                                                 \
		-v $${HOME}/.netrc:/.netrc                              \
	    -v $$(pwd)/.go/cache:/.cache                            \
	    -v $$(pwd)/.go/pkg:/go/pkg                              \
		$(PROTO_BUILD_IMAGE)                                    \
	 	/bin/sh -c "buf mod update proto && buf generate proto --timeout 10m10s"

proto-refresh: proto-clean proto-fetch proto-generate ## Refreshes proto files from remote repo
