EARTHLY ?= earthly
EARTHLY_FLAGS ?=
SUITE ?= integration

.PHONY: proto lint test ci help

# Regenerate and verify protobuf generated code.
proto:
	$(EARTHLY) $(EARTHLY_FLAGS) +proto

# Run Ruff lint/format checks, mypy, and protobuf generated-code verification.
lint:
	$(EARTHLY) $(EARTHLY_FLAGS) +lint

# Run all deterministic offline tests, evaluation, and coverage gates.
test:
	$(EARTHLY) $(EARTHLY_FLAGS) +test

# Run the complete Secret-free pre-commit and pull-request gate.
ci:
	$(EARTHLY) $(EARTHLY_FLAGS) +ci

# Show the stable public commands without requiring Earthly to be installed.
help:
	@echo make proto  - regenerate and verify protobuf code
	@echo make lint   - run Ruff, format, mypy, and protobuf checks
	@echo make test   - run all deterministic offline tests and coverage
	@echo make ci     - run the complete Secret-free quality gate
	@echo make help   - show this command list
