EARTHLY ?= earthly
EARTHLY_FLAGS ?=
SUITE ?= integration

.PHONY: proto lint test ci docker-up docker-test docker-down help

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

# Validate, build, start, and wait for the complete Docker Compose topology.
docker-up:
	$(EARTHLY) $(EARTHLY_FLAGS) +docker-up

# Run a real Docker suite; SUITE accepts integration, resilience, eval, or all.
docker-test:
	$(EARTHLY) $(EARTHLY_FLAGS) +docker-test --SUITE=$(SUITE)

# Scan service logs and stop Compose services without deleting persistent volumes.
docker-down:
	$(EARTHLY) $(EARTHLY_FLAGS) +docker-down

# Show the stable public commands without requiring Earthly to be installed.
help:
	@echo make proto  - regenerate and verify protobuf code
	@echo make lint   - run Ruff, format, mypy, and protobuf checks
	@echo make test   - run all deterministic offline tests and coverage
	@echo make ci     - run the complete Secret-free quality gate
	@echo make docker-up                  - validate, build, and start all services
	@echo make docker-test SUITE=VALUE    - run integration, resilience, eval, or all
	@echo make docker-down                - scan logs and stop services without deleting volumes
	@echo make help   - show this command list
