EARTHLY ?= earthly
EARTHLY_ENV_FILE ?= .earthly.env
EARTHLY_FLAGS ?=
SUITE ?= all
EVAL_FIXTURE ?= rephrased
PRODUCTION_ENV_FILE ?= /etc/rag-mvp/.env.production
CADDY_SCALE ?= 0

.PHONY: all proto lint test ci docker-up docker-test docker-down run production-run web-restart clear help

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

# 一键启动 RAG、Go 产品后端、产品 MySQL 与 Vue 前端容器
run:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +run

# 校验外部生产材料、构建镜像并启动生产栈；裸 IP 阶段默认不启动 Caddy
production-run:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +production-run --PRODUCTION_ENV_FILE="$(PRODUCTION_ENV_FILE)" --CADDY_SCALE="$(CADDY_SCALE)"

# 重新构建并仅重启前端容器，保留后端服务与数据卷
web-restart:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +web-restart

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
	@echo make run                        - 一键启动 RAG、Go 后端、产品 MySQL 和 Vue 前端容器
	@echo make production-run             - 校验材料、构建并启动生产栈（默认 Caddy 副本数为 0）
	@echo make production-run CADDY_SCALE=1 - 域名与 HTTPS 就绪后启动生产公网入口
	@echo make web-restart                - 重新构建并仅重启 Vue 前端容器
	@echo make docker-test SUITE=VALUE EVAL_FIXTURE=original	实际评估数据集选择器
	@echo make docker-down                - 扫描日志并停止服务（不删除数据卷）
	@echo make clear                      - 删除 tests/**/log 目录下的文件
	@echo make help   - 显示此命令列表
