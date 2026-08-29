EARTHLY ?= earthly
EARTHLY_ENV_FILE ?= .earthly.env
EARTHLY_FLAGS ?=
SUITE ?= all
EVAL_FIXTURE ?= rephrased

.PHONY: all proto lint test ci docker-up docker-test docker-down clear help

# 默认所有的检验与启动
all: proto lint test docker-up docker-test

# 重新生成并校验 protobuf
proto:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +proto

# Ruff、format check、mypy、生成物一致性，必须得在uv的虚拟环境下运行
lint:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +lint

# 全部确定性离线测试与覆盖率门禁,必须得在uv的虚拟环境下运行
test:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +test

# - 运行完整的免密钥质量门禁
ci:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +ci

# 启动所有的docker容器
docker-up:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +docker-up

# 全部容器启动后的在线测试
docker-test:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +docker-test --SUITE=$(SUITE) --EVAL_FIXTURE=$(EVAL_FIXTURE)

# 关闭所有的docker容器
docker-down:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +docker-down

# 查找 tests/**/log 目录下的所有文件并删除
clear:
	find tests -type d -name log -exec find {} -maxdepth 1 -type f -delete \;

# 显示命令说明
help:
	@echo make all    - 运行 proto、lint、test、docker-up 和 docker-test，必须得在uv的虚拟环境下运行
	@echo make proto  - 重新生成并校验 protobuf 代码
	@echo make lint   - 运行 Ruff、格式化、mypy 和 protobuf 检查，必须得在uv的虚拟环境下运行
	@echo make test   - 运行所有确定性的离线测试及覆盖率检查，必须得在uv的虚拟环境下运行
	@echo make ci     - 运行完整的免密钥质量门禁
	@echo make docker-up                  - 校验、构建并启动所有服务
	@echo make docker-test SUITE=VALUE EVAL_FIXTURE=original	实际评估数据集选择器
	@echo make docker-down                - 扫描日志并停止服务（不删除数据卷）
	@echo make clear                      - 删除 tests/**/log 目录下的文件
	@echo make help   - 显示此命令列表