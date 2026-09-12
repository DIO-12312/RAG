# 应用镜像自动发布与主机回退

本方案采用用户确认的参数：root@49.235.110.118:22，工作区 /data/RAG，仅 main push，
单机短暂切换，失败回退应用镜像。公网继续使用现有 IP HTTP 模式。发布仓库前缀由
`scripts/release.py` 的 `REGISTRY` 唯一决定，发布、部署校验与 CI 登录都从它推导。

**仓库选择有一条硬前提：生产主机必须能高速拉取。** 2026-09-12 实测本机直连 GHCR 的 CDN
仅 28.7 KB/s（`pkg-containers.githubusercontent.com` → 185.199.x），而单次发布需拉取约
153 MiB（rag 83.3 + api 49.3 + web 20.1 MiB），必然超过单镜像 1200s 拉取超时；同地域
`ccr.ccs.tencentyun.com` 的连接/TLS 握手实测为 5ms/46ms。因此改用同地域 registry 是让
自动发布可用的前置工作，切换步骤见"切换发布仓库"。

## GitHub 准备

Repository Actions Secrets：MIRROR（登录私钥）、HOST（已核验的 known_hosts 整行）、
REGISTRY_USERNAME（对该发布仓库有推/拉权限的账号）、REGISTRY_TOKEN（对应令牌：GHCR 需
write:packages 的 classic PAT，TCR 使用控制台配置的访问凭证）。
runner 与服务器都用这一对凭据登录发布仓库主机；Token 只经 stdin 传递，不放入发布包、
不作为命令行参数、不写入仓库。工作流仅授予 contents:read/packages:write。
首次发布的镜像可能是 private，Token 到期或权限收紧要立即更换该 Secret。

## 首次启用

1. 确认 /etc/rag-mvp/.env.production 及外部 Secret 已就绪，当前生产栈 healthy。
2. 在确实对应当前运行版本的 /data/RAG 源码工作区记录基线：

   ```bash
   make production-baseline RELEASE_SHA=$(git rev-parse HEAD)
   ```

   该命令不重建服务，但给现有镜像增加本地 rollback 标签，并记录生产配置。
   不要对与运行版本不一致的源码建立基线；已有 active.json 时拒绝覆盖。
3. 将发布实现合入 main 后 GitHub Actions 自动执行；代码 push 仍需仓库所有者明确授权。
   服务器须能访问所选 registry；部署包放在 /data/RAG/.releases/<SHA>-<run>-<attempt>，
   不执行 git reset/pull，不覆盖已有开发分支。服务器需要 Docker/Compose、Python3、Make、Earthly、systemd。

## 发布与观察

`make release-check` 通过 Earthfile 校验 Python 全门禁、Go test、Web test/build。
`make release-publish RELEASE_SHA=<完整SHA>` 构建 5 个镜像并推送 `REGISTRY`，生成只有
SHA/兼容摘要/digest 的 release.json。镜像命名为
`<REGISTRY>/rag-{rag,api,web,search-guard,elasticsearch}`；`python3 scripts/release.py registry`
打印当前前缀，CI 与 `send-release.sh` 都用它推导 `docker login` 主机，避免两处硬编码漂移。
实际部署只拉取和替换 rag/api/web 对应的 5 个应用容器。bootstrap/ES 镜像发布供维护升级使用。

Web 镜像在发布时以 `--build-arg VITE_GIT_COMMIT=<sha>` 内嵌发布 commit；发布完成后页面左侧栏
会显示短 SHA（如 `386f681`），用于确认线上镜像是否是自己推送的那次提交。现场 `production-run`
本地构建的镜像没有该参数，显示 `unknown`，不能据此判断自动发布结果。

工作流用固定主机公钥连接，先确认主机已有 active.json，发送该 SHA 的源码 archive 和 release.json，
然后由 systemd 调用 `make production-deploy`。健康检查包括生产依赖、gRPC 容器、API DB readiness、
Web 页面/JSON 与 Caddy 路由；没有真实模型调用，健康通过不代表所有模型供应商可用。
Worker/Outbox 当前只有进程存活检查，尚无独立业务 readiness。短暂切换会中断部分请求或 SSE。

```bash
systemctl list-units 'rag-deploy-*'
journalctl -u rag-deploy-<run>-<attempt> --no-pager
```

同一生产并发组不取消正在执行的部署；新 push 可能替换尚未开始的排队任务，只部署当时的 main HEAD。
主机用 flock 与成功序号避免并发/倒序发布。GitHub 显示 SSH 失败时，应先检查 systemd 日志，
不能假定服务器任务已停止。

## 切换发布仓库

1. 在目标云创建命名空间与仓库（腾讯云 TCR：容器镜像服务 → 命名空间 → 访问凭证），记录
   `ccr.ccs.tencentyun.com/<命名空间>`、账号与密码。
2. 先在主机验证拉取速度，再改代码：

   ```bash
   docker login ccr.ccs.tencentyun.com -u <账号>
   time docker pull <该仓库中的测试镜像>   # 应显著快于 GHCR 直连的 ~29 KB/s
   ```

3. 把 `scripts/release.py` 的 `REGISTRY` 改为 `ccr.ccs.tencentyun.com/<命名空间>`，并同步
   `SPEC.md`、本文档与 `tests/contract/test_release_deployment.py`（用例从常量推导，
   通常只需确认前缀形状仍为 `host/namespace`）。
4. 把 GitHub Secrets 的 REGISTRY_USERNAME/REGISTRY_TOKEN 换成目标仓库凭据后推送 main；
   旧 GHCR 凭据可保留或删除，主机 `docker logout ghcr.io` 可清掉残留登录。
5. 发布成功后核对页面侧栏短 SHA（形如 `7ee76c0`）与 `release.json` 中的 digest 前缀。

切换 registry 不改变 digest 白名单校验、成功序号防倒退、pending 回退等语义；旧仓库中的历史
镜像可保留作审计，回退仍使用主机本地 rollback 标签，因此不需要旧仓库在线。

## 回退与限制

/var/lib/rag-deploy/active.json 记录成功版本；previous.json 记录上一次成功版本；
pending.json 是切换前写入的恢复记录。拉取失败不停止旧服务；切换/探针失败恢复旧镜像，
回退失败保留 pending，恢复依赖后执行：

```bash
make production-recover
```

该命令恢复未完成的切换，不是任意历史版本降级入口。主机断电后可执行同一命令；下一次部署
也会优先恢复 pending。保留所有 release 配置及镜像，不自动 prune；磁盘回收需先确认不涉及
active/previous/pending 引用。不执行 down -v，不恢复/覆盖用户数据库。

兼容性摘要变化（schema、migration、RPC、生产拓扑、Search Guard 等）会提前阻断自动发布。
维护升级需按生产 runbook 备份/验收，确认新版本健康后将旧状态目录归档，再建立新基线。
Go API 内置初始化不会因为自动发布而被关闭，因此 storage/启动代码变化也列入阻断范围。
基线记录的 Compose 配置固定，主机 env/Secret 路径修改需维护验收后重建基线。
镜像回退不等于业务数据回退，任意数据语义变更都必须审查向后兼容性。

`make production-run` 仍为现场构建的维护启动入口；自动部署使用 production-deploy。
自动部署后再手工 production-run 可能换回 env 中的本地镜像；此后自动发布会检测应用镜像漂移并拒绝继续。
本次不提供漏洞扫描/签名、异机备份或数据自动降级。真实 main push 发布与回退需在 GitHub 实际验收。
