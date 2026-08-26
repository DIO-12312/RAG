EARTHLY ?= earthly
EARTHLY_ENV_FILE ?= .earthly.env
EARTHLY_FLAGS ?=
SUITE ?= integration
EVAL_FIXTURE ?= rephrased

.PHONY: proto lint test ci docker-up docker-test docker-down clear help

# Regenerate and verify protobuf generated code.
proto:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +proto

# Run Ruff lint/format checks, mypy, and protobuf generated-code verification.
lint:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +lint

# Run all deterministic offline tests, evaluation, and coverage gates.
test:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +test

# Run the complete Secret-free pre-commit and pull-request gate.
ci:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +ci

# Validate, build, start, and wait for the complete Docker Compose topology.
docker-up:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +docker-up

# Run a real Docker suite; SUITE accepts integration, resilience, eval, or all.
docker-test:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +docker-test --SUITE=$(SUITE) --EVAL_FIXTURE=$(EVAL_FIXTURE)

# Scan service logs and stop Compose services without deleting persistent volumes.
docker-down:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +docker-down

# Remove files from every tests/**/log directory while keeping the directories.
clear:
	find tests -type d -name log -exec find {} -maxdepth 1 -type f -delete \;

# Show the stable public commands without requiring Earthly to be installed.
help:
	@echo make proto  - regenerate and verify protobuf code
	@echo make lint   - run Ruff, format, mypy, and protobuf checks
	@echo make test   - run all deterministic offline tests and coverage
	@echo make ci     - run the complete Secret-free quality gate
	@echo make docker-up                  - validate, build, and start all services
	@echo make docker-test SUITE=VALUE EVAL_FIXTURE=original	real eval dataset selector
	@echo make docker-down                - scan logs and stop services without deleting volumes
	@echo make clear                      - remove files from tests/**/log directories
	@echo make help   - show this command list
