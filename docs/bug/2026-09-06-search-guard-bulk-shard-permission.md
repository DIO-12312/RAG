# 上传失败：Search Guard 缺少 bulk 分片权限

Go 产品联调中，SubmitDocument 成功返回 Job，Worker 最终 FAILED(SEARCH_UNAVAILABLE)。ES 2026-09-06 日志明确显示 `indices:data/write/index` 已授权，但 `indices:data/write/bulk[s]` MISSING。

原角色只列出 `indices:data/write/bulk`，未覆盖内部的分片子动作。将同一 `rag-chunks-v1*` index_permissions 下的动作改为 `indices:data/write/bulk*`，不扩大索引范围，不授予全局 ALL_ACCESS。

已有集群须由持有 admin 证书的运维入口显式更新 sg_roles.yml；重建应用镜像本身不会更新 ES 中已存在的角色。验证使用真实上传→Worker→ES→Retrieve 集成链路；契约测试 `test_search_guard_assets_pin_tls_and_least_privilege` 固定该权限要求。
