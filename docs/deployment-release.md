# GHCR 自动发布与主机回退

本方案采用用户确认的参数：GHCR，root@49.235.110.118:22，工作区 /data/RAG，仅 main push，
单机短暂切换，失败回退应用镜像。公网继续使用现有 IP HTTP 模式。

**主机拉取前提：Mihomo 代理/TUN 必须在线（采用方案 A）。** 这台主机直连 GitHub CDN
（185.199.x）实测只有 28.7 KB/s，而单次发布要拉约 153 MiB（rag 83.3 + api 49.3 + web 20.1 MiB），
必然超过单镜像 1200s 的拉取超时；代理/TUN 在线时同一层实测 3.78 MB/s，三个应用镜像分别
26.1s / 17.1s / 2.7s。依赖的 `clash-tui.service`（TUN + 本地代理端口）与
`rag-public-return-route.service`（公网回程策略路由）均已 enable。代理不可用时发布会在拉取
阶段超时失败，失败发生在写 `pending.json` 与停服之前，生产继续运行，恢复代理后重跑即可。

## GitHub 准备

Repository Actions Secrets 使用已有名称：MIRROR（登录私钥）、HOST（已核验的 known_hosts 整行）、
GHCR_USERNAME（有包读取权限的账号）、GHCR_TOKEN（classic PAT，read:packages）。
服务器部署时通过 stdin 登录 root 的 Docker 凭据存储；该 Token 不放入发布包、不作为命令行参数。
GitHub GITHUB_TOKEN 用于构建发布，工作流仅授予 contents:read/packages:write。
首次发布的镜像可能是 private；PAT 对应用户必须有包读取权限，Token 到期需更换该 Secret。

## 首次启用

1. 确认 /etc/rag-mvp/.env.production 及外部 Secret 已就绪，当前生产栈 healthy。
2. 在确实对应当前运行版本的 /data/RAG 源码工作区记录基线：

   ```bash
   make production-baseline RELEASE_SHA=$(git rev-parse HEAD)
   ```

   该命令不重建服务，但给现有镜像增加本地 rollback 标签，并记录生产配置。
   不要对与运行版本不一致的源码建立基线；已有 active.json 时拒绝覆盖。
3. 将发布实现合入 main 后 GitHub Actions 自动执行；代码 push 仍需仓库所有者明确授权。
   服务器须能访问 GitHub/GHCR（经主机代理，见上文"主机拉取前提"）；部署包放在
   /data/RAG/.releases/<SHA>-<run>-<attempt>，
   不执行 git reset/pull，不覆盖已有开发分支。服务器需要 Docker/Compose、Python3、Make、Earthly、systemd。

## 发布与观察

`make release-check` 通过 Earthfile 校验 Python 全门禁、Go test、Web test/build。
`make release-publish RELEASE_SHA=<完整SHA>` 构建 5 个镜像并推送 GHCR，生成只有 SHA/兼容摘要/digest
的 release.json。镜像命名为 ghcr.io/dio-12312/rag-{rag,api,web,search-guard,elasticsearch}。
实际部署只拉取和替换 rag/api/web 对应的 5 个应用容器。bootstrap/ES 镜像发布供维护升级使用。

Web 镜像在发布时以 `--build-arg VITE_GIT_COMMIT=<sha>` 内嵌发布 commit；发布完成后页面左侧栏
会显示短 SHA（如已上线的 `7ee76c0`），用于确认线上镜像是否是自己推送的那次提交。现场 `production-run`
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

## 代理与拉取排障

拉取变慢或失败时先确认代理与路由，再怀疑 registry：

```bash
systemctl is-active clash-tui.service            # 应为 active（enabled，重启自动恢复）
ip link show type tun | head -3                  # 应有 Meta 设备
ip route get 185.199.110.154                     # 应为 via 198.18.0.x dev Meta
docker pull ghcr.io/dio-12312/rag-rag:<sha>      # 单镜像应在 30s 量级完成
```

代理停掉时发布会在 `docker pull` 阶段超时失败并返回非零，此时主机仍运行旧版本、
`pending.json` 未写入；恢复代理后重新触发发布即可，不需要任何回退操作。
`rag-public-return-route.service` 保证 `172.19.0.0/16` 中源端口 80/443 的回包走主路由，
是公网入口正常的前提；它与 `clash-tui.service` 都不得随意停用。

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
