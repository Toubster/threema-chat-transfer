# SPDX-License-Identifier: AGPL-3.0-or-later
# Owner: pack. Every target is a thin wrapper around a script; CI calls the scripts directly (DESIGN §12).
# Nothing here touches a device or the network, except: python/core (pinned downloads, hash-checked),
# model-verify without MOMD_SOURCE (shallow clone of threema-ios at the pinned tag) and source-bundle (sdists).
PY ?= $(if $(wildcard .venv/bin/python),$(CURDIR)/.venv/bin/python,python3)
OFFLINE ?=
MOMD_SOURCE ?=
VERSION := $(shell sed -nE 's/^__version__ = "([^"]+)".*/\1/p' core/tmcore/__init__.py)
APP_SLUG := threema-chat-transfer
DMG := build/dist/$(APP_SLUG)-$(VERSION).dmg

.PHONY: help hooks scrub validate codes strings test test-core test-importer contract model-verify \
        python core importer stage app dmg release-artifacts sbom source-bundle smoke verify verify-quarantine \
        demo-screenshots clean

help:
	@echo "checks:    make test | test-core | test-importer | contract | model-verify [MOMD_SOURCE=dir] | scrub | validate | strings"
	@echo "bundle:    make python | core | importer | stage | app | dmg | verify | verify-quarantine | smoke  [OFFLINE=1]"
	@echo "release:   make release-artifacts (sbom + source-bundle; needs a clean, committed tree)"
	@echo "app/data:  make demo-screenshots | record-scenarios | check-scenarios"
	@echo "version $(VERSION); output in build/ (git-ignored)"

hooks:
	git config core.hooksPath scripts/hooks

scrub:
	$(PY) scripts/scrub_check.py

validate:
	$(PY) scripts/validate_schemas.py

codes:
	$(PY) scripts/gen_codes.py

strings:
	$(PY) scripts/check_strings.py

# ---- tests --------------------------------------------------------------------------------------------------------
test: test-core test-importer

test-core:
	cd core && $(PY) -m pytest -q

# the importer check suite (swift build -c release --arch arm64 + every check against synthetic fixtures)
test-importer:
	$(PY) importer/Tests/run_checks.py

contract: validate strings
	cd core && $(PY) -m pytest -q tests/contract

# compiled model == committed model == compat/threema-ios.json (full Xcode; MOMD_SOURCE = existing threema-ios checkout)
model-verify:
	model/build-momd.sh --verify $(if $(MOMD_SOURCE),--source "$(MOMD_SOURCE)")

# ---- bundle (DESIGN §14.1) ----------------------------------------------------------------------------------------
python:
	packaging/fetch-python.sh $(if $(OFFLINE),--offline)

core:
	packaging/build-core.sh $(if $(OFFLINE),--offline)

importer:
	packaging/build-importer.sh

# build/stage/Resources complete: core + importer + models + compat + legal + bundle-manifest.json
stage: core importer

# the signed app: xcodegen + xcodebuild (app/project.yml), Resources merged, ad-hoc signed inside out, verified
app: stage
	packaging/build-app.sh
	packaging/sign-adhoc.sh
	packaging/verify-bundle.sh

dmg: app
	packaging/make-dmg.sh
	packaging/verify-bundle.sh --dmg "$(DMG)"

verify:
	packaging/verify-bundle.sh $(if $(wildcard $(DMG)),--dmg "$(DMG)")

# quarantined copy in a path with spaces and umlauts (CI/VM: may show a Gatekeeper dialog on a desktop Mac)
verify-quarantine:
	packaging/verify-bundle.sh --quarantine

sbom:
	packaging/make-sbom.sh

source-bundle:
	packaging/make-source-bundle.sh $(if $(OFFLINE),--offline)

release-artifacts: sbom source-bundle
	cd build/dist && shasum -a 256 "$(APP_SLUG)-$(VERSION).dmg" sbom.cdx.json source-bundle.tar.gz THIRD_PARTY_LICENSES.txt > SHA256SUMS

# the whole packaging chain with a stub app (no Xcode project needed)
smoke:
	packaging/tests/smoke.sh $(if $(OFFLINE),--offline)

# ---- app and scenario data ----------------------------------------------------------------------------------------
# Demo screenshots (DESIGN §13.1 level 7): the signed app in demo mode with the real engine on the virtual iPhone,
# driven by the app's demo autopilot (devtools/demo_screenshots.py, no UI automation permission needed); same call as
# .github/workflows/demo-screenshots.yml. Output in build/demo/<lang>/; INSTALL=1 marks them (tm-demo=1), copies them
# to docs/images/app/<lang>/ and rewrites the allowlist block "demo screenshots".
demo-screenshots: app
	$(PY) devtools/demo_screenshots.py $(if $(INSTALL),--install)
	$(PY) docs/tools/check_docs.py --screenshots-dir build/demo

# Scenario recording is coreB's tool (DESIGN §13.3); the targets exist as soon as devtools/record_scenarios.py does,
# so the CI gate (make -n check-scenarios) switches the drift check on by itself.
ifneq ($(wildcard devtools/record_scenarios.py),)
.PHONY: record-scenarios check-scenarios
record-scenarios:
	$(PY) devtools/record_scenarios.py

check-scenarios:
	$(PY) devtools/record_scenarios.py --check
else
# (check-scenarios stays undefined here: `make -n check-scenarios` must fail while the tool is missing)
.PHONY: record-scenarios
record-scenarios:
	@echo "devtools/record_scenarios.py does not exist yet (owner: coreB, DESIGN §13.3)"; exit 2
endif

clean:
	rm -rf build/stage build/dist build/app build/dmg build/smoke build/importer-checks build/model build/source-bundle
