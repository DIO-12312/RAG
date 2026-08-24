# Earthfile 与 Makefile 统一构建入口设计

> 日期：2026-08-25
> 状态：已完成交互设计，等待书面复核
> 范围：protobuf 生成与校验、Python 质量门禁、离线测试、Docker Compose 启停及真实测试编排

## 1. 背景与问题

仓库当前把完整命令分别写在 `docs/README.md`、`docs/testing-guide.md`、Git pre-commit hook 和 GitHub Actions 中。虽然这些入口目前大致一致，但任何测试目录、pytest marker、覆盖率范围或 Compose 参数变化都需要同步修改多处，容易产生以下问题：

- 开发者执行的命令与 CI 实际命令不同；
- protobuf 生成、生成物检查和质量门禁之间缺少统一入口；
- Docker 配置校验、镜像构建、服务启动和健康等待被拆成多条手工命令；
- 真实 integration、Docker resilience 和 real eval 的长命令难以发现和复用；
- README 被大量低层命令占据，无法突出项目定位和最短使用路径。

本次调整把 `Earthfile` 定义为执行逻辑的唯一来源，把 `Makefile` 定义为稳定、简短的用户接口。README、Git Hook 和 GitHub Actions 只调用 Makefile，不再复制底层命令。

## 2. 目标与非目标

### 2.1 目标

1. 使用固定 Linux 构建环境执行 protobuf、Lint、类型检查和离线测试，使本地与 CI 使用相同工具链。
2. 使用 Earthly `LOCALLY` target 编排宿主机 Docker Compose，同时保留现有 `.env`、Docker 网络、持久卷和进程级恢复测试语义。
3. 将 Makefile 的公开命令压缩为八个高层入口，并为每个 Make target 和 Earthly target 添加用途及副作用注释。
4. 让 Git pre-commit hook、GitHub 快速质量工作流和真实 Docker 工作流复用同一套命令。
5. 让后续 README 只展示短命令，把参数、测试边界和排错说明留在测试指南。

### 2.2 非目标

- 不修改 Python 应用架构、gRPC 契约、Job/Task 状态机或数据存储语义。
- 不用 Earthly 替换现有多阶段 `Dockerfile`；应用运行镜像仍由该 Dockerfile 定义。
- 不引入 Earthly Cloud、Satellite、远程缓存、镜像推送或发布流程。
- 不将模型 API Key 写入 Earthfile build argument、镜像层、构建日志或仓库文件。
- 不删除真实测试与离线测试之间的边界，也不让普通 pre-commit 调用真实模型。

## 3. 分层架构

```text
README / 开发者 / Git Hook / GitHub Actions
                    │
                    ▼
                Makefile
       稳定别名、帮助信息、参数转发
                    │
                    ▼
                Earthfile
     唯一保存完整命令、依赖关系与测试矩阵
          ┌─────────┴──────────┐
          ▼                    ▼
固定 Linux Python 环境       LOCALLY 宿主机执行
protobuf / lint / mypy       Docker Compose 启停
offline pytest / coverage    真实测试与日志检查
```

职责约束如下：

- `Makefile` 不直接执行 `uv`、`pytest`、`ruff`、`mypy` 或 `docker compose`，只调用 `earthly +target`。
- `Earthfile` 是完整命令及参数的唯一权威来源。
- Git Hook 和 GitHub Actions 不复制底层命令，只调用 Makefile。
- `Dockerfile` 继续定义生产与测试镜像内容；Earthfile 不复制其镜像装配逻辑。
- `docs/testing-guide.md` 解释测试类型、失败定位和参数选择；README 只提供常用入口。

## 4. Makefile 公共接口

Makefile 只暴露以下八个目标。每个目标上方必须有注释，`make help` 也必须展示相同说明。

| 命令 | 语义 | 外部依赖或副作用 |
|---|---|---|
| `make proto` | 在固定环境生成 protobuf，并检查生成物 | 会更新 `src/rag_mvp/rpc/generated/` |
| `make lint` | 执行 Ruff Lint、Ruff format check、mypy 和 generated-code check | 不访问真实基础设施，不修改源码 |
| `make test` | 执行全部离线测试、离线评测和 85% 核心覆盖率门禁 | 不读取真实模型配置 |
| `make ci` | 组合 `lint` 与 `test`，作为 pre-commit 和快速 CI 入口 | 不访问网络服务或 Docker Compose |
| `make docker-up` | 校验 Compose、构建应用镜像、启动完整拓扑并等待健康 | 需要 Docker 和有效 `.env`；保留持久卷 |
| `make docker-test` | 按 `SUITE` 运行真实 Docker 测试 | 调用真实模型，可能产生费用；resilience 会控制容器进程 |
| `make docker-down` | 扫描日志中的 API Key，并停止 Compose 服务 | 不带 `-v`，不得删除持久卷 |
| `make help` | 展示命令、`SUITE` 取值、默认值和环境要求 | 无副作用 |

Makefile 使用可覆盖变量：

```make
EARTHLY ?= earthly
EARTHLY_FLAGS ?=
SUITE ?= integration
```

这允许 Linux、macOS 和安装了原生 Earthly 的 Windows 环境直接使用 `make`；Windows 推荐在 WSL2 中运行。调用方也可以覆盖 `EARTHLY`，但仓库不会自动下载或静默安装构建工具。

所有公开目标声明为 `.PHONY`。Makefile 不维护另一份 shell 业务逻辑；它只负责把 `SUITE` 等参数传给相应 Earthly target。

## 5. Earthfile 内部设计

### 5.1 固定 Python 开发环境

Earthfile 使用与 CI、Dockerfile 一致的 Python 3.12 和锁定 uv 版本建立共享基础 target。基础 target 复制以下构建输入并执行 `uv sync --frozen --group dev`：

- `pyproject.toml`、`uv.lock`、`LICENSE` 和 package README；
- `src/`、`proto/`、`scripts/`、`migrations/`；
- 离线测试 target 额外复制 `tests/`。

质量与测试 target 运行在 Earthly 管理的 Linux 容器中，不使用 `LOCALLY`。这样 PowerShell、GNU Make 和 GitHub Actions 的 shell 差异不会改变 Python 门禁结果。

### 5.2 Protobuf targets

- `+proto`：运行 `scripts/generate_proto.py`，随后运行 `scripts/check_generated.py`，最后以本地 artifact 更新生成目录。
- `+proto-check`：只运行生成物一致性检查，不修改工作区。

`+proto` 是唯一允许写回 Python generated 目录的 Earthly target。CI 和 pre-commit 只调用 `+proto-check`；当 `.proto` 发生变化时，开发者先运行 `make proto`，检查 diff 后再提交契约与两端生成物。

### 5.3 质量 targets

内部 target 分别执行：

- `+ruff-check`：`ruff check`；
- `+format-check`：`ruff format --check`；
- `+type-check`：mypy strict 检查；
- `+proto-check`：protobuf 生成物一致性检查；
- `+lint`：聚合以上四项。

细分 target 用于 Earthly 缓存、并行和失败定位，不作为 Makefile 公共命令。每个 target 上方写明负责的检查和是否修改文件。

### 5.4 离线测试 targets

内部 target 保持现有测试边界：

- `+test-fast`：unit、contract 和 functional；
- `+test-resilience`：排除 `docker_resilience` 的 Fake resilience；
- `+test-eval`：排除真实 E2E 的确定性离线评测；
- `+test-coverage`：排除 integration、model integration、E2E 和 Docker resilience，执行核心模块 85% 覆盖率门禁；
- `+test`：聚合全部离线测试 target；
- `+ci`：聚合 `+lint` 与 `+test`。

离线 target 不接收或读取 Embedding 模型 URL、名称、API Key 和维度。缺少 `.env` 不应影响 `make lint`、`make test` 或 `make ci`。

### 5.5 Docker targets

Docker target 使用 `LOCALLY`，在 Earthfile 所在仓库根目录调用宿主机 Docker Compose。它们不得运行会输出完整 Secret 展开结果的 `docker compose config`，只能使用 `config --quiet`。

- `+docker-up`：依次进行 Compose 静默校验、构建 `rag-server`/`rag-worker`/`rag-outbox`/`rag-test`，再用 `up -d --wait` 启动 Server、Worker 和 Outbox 及其依赖。
- `+docker-test`：根据 `SUITE` 选择真实测试矩阵；若服务未启动，先保证完整拓扑已启动并健康。测试失败后不自动清理，以便保留容器和日志现场。
- `+docker-down`：先把 Compose 日志通过 `check_secret_leaks.py` 检查，再执行 `docker compose down --remove-orphans`。即使日志检查失败，也必须尝试停止服务；最终退出码必须保留日志泄漏或停止失败。

`SUITE` 只允许以下值：

| `SUITE` | 执行内容 |
|---|---|
| `integration` | 真实 MySQL/ES/NATS adapter、真实 Embedding、四格式 gRPC E2E；默认值 |
| `resilience` | Docker 进程强杀、重复投递、恢复与并发栅栏测试 |
| `eval` | 真实模型、真实 ES 和固定 30 问检索评测 |
| `all` | 按 integration → resilience → eval 顺序执行全部真实测试 |

未知 `SUITE` 必须在执行任何测试前返回清晰的非零错误，不得静默回退到默认 suite。`resilience` 只允许使用测试 override 文件，且任何 down 操作都禁止 `-v`。

## 6. Git Hook 与 GitHub Actions

### 6.1 Git pre-commit

`.githooks/pre-commit` 只保留严格 shell 设置和一条 `make ci`。Hook 不再复制 Ruff、mypy、pytest 或 coverage 参数，也不得使用 `--no-verify` 绕过失败。

这意味着提交前需要安装 Docker、Earthly 和 GNU Make；Python 工具链由 Earthly 容器提供。首次执行可能需要拉取基础镜像，后续执行复用 Earthly 缓存。

### 6.2 快速 GitHub Actions

`.github/workflows/quality.yml`：

1. checkout；
2. 安装固定版本 Earthly；
3. 执行 `make ci`。

不再单独安装 Python、uv 或逐条复制质量命令。Earthly target 内的固定环境是权威工具链。

### 6.3 真实 Docker GitHub Actions

`.github/workflows/docker-quality.yml` 保留 Secret 校验和 `if: always()` 清理语义：

- integration/E2E job 调用 `make docker-test SUITE=integration`；
- nightly job 调用 `make docker-test SUITE=resilience`，随后调用 `make docker-test SUITE=eval`；
- 每个 job 的 always cleanup 调用 `make docker-down`。

Secret 是否存在仍由工作流在 Earthly 启动前检查，从而输出具体缺失变量名称；Secret 的值不得作为 Earthly build arg 传入。`LOCALLY` target 只继承 CI 进程环境，并将环境留给 Docker Compose 消费。

## 7. 错误处理与安全边界

- 所有 target 在子命令失败时返回非零退出码。
- `make proto` 只有在生成和复查都成功后才导出本地 artifact，避免输出半生成状态。
- `make docker-up` 的 Compose 校验失败时不得开始构建或启动服务。
- `make docker-test` 失败时保留现场；README 和测试指南必须提示执行 `make docker-down`。
- `make docker-down` 必须保证“扫描失败仍执行 down”，且不能在输出中打印检测到的 Secret。
- Earthfile、Makefile、GitHub Actions 和文档不得包含真实 `.env` 内容。
- Docker 操作不得调用 `down -v`，不得删除 MySQL、Elasticsearch、NATS 或对象存储持久卷。
- 普通 `make ci` 不得调用真实模型、真实基础设施或破坏性容器测试。

## 8. 文档调整

构建入口实现后同步更新：

- `docs/SPEC.md`：在工程基线、测试与本地运行部分声明 Makefile/Earthfile 是统一命令入口；
- `docs/testing-guide.md`：以 `make lint`、`make test` 和参数化 `make docker-test` 为主要入口，保留按单测试文件调试的底层 pytest 示例；
- `docs/README.md`：后续重写时只保留最短 Quickstart 和高层命令，不再复制长测试矩阵；
- `AGENTS.md`：提交前检查改为优先调用统一入口，同时保留“报告实际运行项”的要求。

README 迁移到仓库根目录属于下一步 README 重写的一部分，不与本构建编排提交混在一起。Earthfile 初次实现继续兼容当前 `pyproject.toml` 中的 `docs/README.md` 路径。

## 9. 测试与验收

### 9.1 静态契约测试

新增构建入口契约测试，至少验证：

- Makefile 只把公开命令转发到 Earthly，不直接包含底层质量或 Compose 命令；
- 八个 Make target 和对应 Earthly target 存在且带说明注释；
- `SUITE` 默认值和允许集合固定；
- Hook 只调用 `make ci`；
- GitHub Actions 使用 Makefile 入口，不再复制 Ruff、mypy、pytest 和 Docker 测试命令；
- Docker down 命令不含 `-v`，Compose 校验只使用 `--quiet`；
- 普通离线 target 不引用模型 Secret。

新增、移动或重命名测试后，同步更新 `tests/TEST.md` 的目录树和职责表。

### 9.2 行为验证

实施阶段按风险从低到高验证：

1. Earthfile 语法和 target 列表可解析；
2. `make lint` 成功；
3. `make test` 成功且覆盖率不低于 85%；
4. 修改临时 proto 副本或使用现有生成脚本验证 `make proto` 的输出与 `proto-check` 一致；
5. `make docker-up` 启动服务并等待健康；
6. `make docker-test SUITE=integration` 通过；
7. 条件允许时运行 `SUITE=resilience` 和 `SUITE=eval`；未运行时明确报告；
8. `make docker-down` 扫描日志并停止服务，确认持久卷仍存在。

当前开发机尚未安装 Earthly，因此实施计划必须把安装或提供可执行路径作为第一项环境前置检查；仓库代码不得擅自安装系统级工具。

## 10. 预计改动文件

| 文件 | 改动 |
|---|---|
| `Earthfile` | 新增完整执行图与注释 |
| `Makefile` | 新增八个稳定公共入口与帮助 |
| `.githooks/pre-commit` | 改为调用 `make ci` |
| `.github/workflows/quality.yml` | 安装 Earthly并调用 `make ci` |
| `.github/workflows/docker-quality.yml` | 调用参数化 Docker target 并保留 always cleanup |
| `tests/contract/test_build_entrypoints.py` | 新增构建入口、安全边界和 CI 对齐契约测试 |
| `tests/TEST.md` | 登记新增测试职责 |
| `docs/SPEC.md` | 固化统一构建入口约束 |
| `docs/testing-guide.md` | 使用 Makefile 重写常用入口 |
| `docs/README.md` | 暂时更新命令入口；完整结构重写另行提交 |
| `AGENTS.md` | 让协作约束引用统一门禁 |

## 11. 参考依据

- [Earthfile reference：`LOCALLY`、`BUILD` 与 target 语义](https://docs.earthly.dev/docs/earthfile)
- [Earthly GitHub Actions 集成](https://docs.earthly.dev/ci-integration/vendor-specific-guides/gh-actions-integration)
- [Earthly Windows 安装与 WSL2 前置条件](https://docs.earthly.dev/docs/misc/alt-installation)
