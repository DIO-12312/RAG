EARTHLY ?= earthly
.DEFAULT_GOAL := all
EARTHLY_ENV_FILE ?= .earthly.env
EARTHLY_FLAGS ?=
SUITE ?= all
EVAL_FIXTURE ?= rephrased
PRODUCTION_ENV_FILE ?= /etc/rag-mvp/.env.production
PUBLIC_MODE ?= ip
RELEASE_SHA ?=
RELEASE_SEQUENCE ?=

.PHONY: release-check release-publish production-baseline production-deploy production-recover

# 校验 Python、Go 和前端发布门禁
release-check:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +release-check

# 构建推送 GHCR 镜像并记录不可变 digest
release-publish:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +release-publish --RELEASE_SHA="$(RELEASE_SHA)"

# 记录当前健康生产栈以便首次自动发布回退
production-baseline:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +production-baseline --RELEASE_SHA="$(RELEASE_SHA)" --PRODUCTION_ENV_FILE="$(PRODUCTION_ENV_FILE)"

# 从 release.json 指定的镜像切换生产应用
production-deploy:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +production-deploy --RELEASE_SHA="$(RELEASE_SHA)" --RELEASE_SEQUENCE="$(RELEASE_SEQUENCE)"

# 恢复中断的生产发布
production-recover:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +production-recover

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

# 校验外部生产材料、构建镜像并启动生产栈；默认提供公网 IP HTTP 入口
production-run:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +production-run --PRODUCTION_ENV_FILE="$(PRODUCTION_ENV_FILE)" --PUBLIC_MODE="$(PUBLIC_MODE)"

# 重新构建并仅重启前端容器，保留后端服务与数据卷
web-restart:
	$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS) +web-restart

# 查找 tests/**/log 目录下的所有文件并删除
clear:
	find tests -type d -name log -exec find {} -maxdepth 1 -type f -delete \;

# 显示命令说明
help:
	@echo make release-check - 校验 Python、Go 与前端发布门禁
	@echo make release-publish RELEASE_SHA=SHA - 推送 GHCR 镜像与 digest 清单
	@echo make production-baseline RELEASE_SHA=SHA - 记录现有生产回退基线
	@echo make production-deploy RELEASE_SHA=SHA RELEASE_SEQUENCE=N - 从 release.json 部署应用
	@echo make production-recover - 恢复中断的应用切换
	@echo make all    - 运行 proto、lint、test、docker-up 和 docker-test，必须得在uv的虚拟环境下运行
	@echo make proto  - 重新生成并校验 protobuf 代码
	@echo make lint   - 运行 Ruff、格式化、mypy 和 protobuf 检查，必须得在uv的虚拟环境下运行
	@echo make test   - 运行所有确定性的离线测试及覆盖率检查，必须得在uv的虚拟环境下运行
	@echo make ci     - 运行完整的免密钥质量门禁
	@echo make docker-up                  - 校验、构建并启动所有服务
	@echo make run                        - 一键启动 RAG、Go 后端、产品 MySQL 和 Vue 前端容器
	@echo make production-run             - 校验材料、构建并启动生产栈（默认公网 IP HTTP 入口）
	@echo make production-run PUBLIC_MODE=domain - 域名与 HTTPS 配置就绪后启动生产入口
	@echo make web-restart                - 重新构建并仅重启 Vue 前端容器
	@echo make docker-test SUITE=VALUE EVAL_FIXTURE=original	实际评估数据集选择器
	@echo make docker-down                - 扫描日志并停止服务（不删除数据卷）
	@echo make clear                      - 删除 tests/**/log 目录下的文件
	@echo make help   - 显示此命令列表
