# Orbital Agents: common tasks. `make help` lists them.

PY ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
ORBITAL ?= $(if $(wildcard .venv/bin/orbital),.venv/bin/orbital,$(PY) -m orbital.cli.main)
UNAME := $(shell uname -s)
SWIFT := $(shell command -v swift 2>/dev/null)
XCODEBUILD := $(shell xcodebuild -version >/dev/null 2>&1 && command -v xcodebuild)
NODE := $(shell command -v node 2>/dev/null)
APP_DERIVED := apps/orbital-app/.derivedData apps/orbital-app/.derivedData-device apps/orbital-app/.derivedData-mac

.PHONY: help setup test test-python test-swift test-app test-live lint doctor clean

help:
	@echo "make setup      install and configure this machine (./setup)"
	@echo "make test       Python tests + tracker swift build/test + the app's unit tests on a simulator + lint"
	@echo "make test-live  operational checks against the running install (orbital test --live)"
	@echo "make lint       compile all Python, node --check the extension, bash -n the scripts"
	@echo "make doctor     diagnose this machine (orbital doctor)"
	@echo "make clean      remove build output and caches (keeps .venv and your settings)"

setup:
	./setup

test: test-python test-swift test-app lint

test-python:
	$(PY) -m pytest -m "not live" -q

test-swift:
ifeq ($(UNAME),Darwin)
ifneq ($(SWIFT),)
	cd apps/tracker && swift build && swift test
else
	@echo "skip: no swift toolchain (xcode-select --install)"
endif
else
	@echo "skip: the tracker is macOS only"
endif

# The app's unit tests on an iPhone simulator (apps/orbital-app/scripts/test.sh boots one that is shut down
# and shuts it down again). Without a simulator it only builds the app unsigned; without Xcode it skips.
test-app:
ifneq ($(XCODEBUILD),)
	@if xcrun simctl list devices available 2>/dev/null | grep -q "iPhone"; then \
	  bash apps/orbital-app/scripts/test.sh; \
	else \
	  echo "skip: no iPhone simulator installed (Xcode → Settings → Components); building the app unsigned instead"; \
	  bash apps/orbital-app/scripts/build.sh; \
	fi
else
	@echo "skip: full Xcode isn't installed (the app's unit tests and unsigned build)"
endif

test-live:
	$(ORBITAL) test --live

lint:
	$(PY) -m compileall -q orbital feeds tests
	bash -n setup
	@for f in apps/tracker/scripts/*.sh apps/orbital-app/scripts/*.sh; do [ -f "$$f" ] && bash -n "$$f" || true; done
ifneq ($(NODE),)
	@for f in $$(find browser-extension -name '*.js' -not -path '*/node_modules/*'); do node --check "$$f" || exit 1; done
	@echo "extension JS OK"
else
	@echo "skip: node isn't installed (extension lint)"
endif

doctor:
	$(ORBITAL) doctor

clean:
	rm -rf .pytest_cache build dist $(APP_DERIVED) apps/tracker/.build apps/tracker/build
	find . -name __pycache__ -type d -not -path './.venv/*' -prune -exec rm -rf {} +
