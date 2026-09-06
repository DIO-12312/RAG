# DeepSeek 思考模式与强制工具调用冲突

2026-09-06 使用已保存的 `deepseek-v4-flash` 配置真实验收时，Chat 返回 HTTP 400：`Thinking mode does not support this tool_choice`。

页面未勾选思考模式，但原模型客户端只有开启时发送 `enable_thinking=true`，未传递 DeepSeek 的关闭参数。服务端默认思考模式与首轮指定 `rag_retrieve` 的 tool_choice 冲突。

修复：识别 DeepSeek 官方域名或 `deepseek-` 模型名，将开关转换为 `thinking.type=enabled/disabled`。开启思考时使用 `tool_choice=auto`，Agent 仍校验首轮确实调用检索工具；工具轮次保留供应商返回的 reasoning_content 供下一次请求使用，不将其发送给前端或保存为回答。兼容依据：[DeepSeek 思考模式文档](https://api-docs.deepseek.com/guides/thinking_mode/)。

验证：Go test/vet 通过；确定性测试覆盖开关参数、工具选择与供应商中间上下文保留。显式开启 `PRODUCT_TEST_SAVED_CHAT=true` 后，使用实际已保存的模型完成真实 MySQL/Python RPC/Worker/ES/Embedding/Chat 闭环测试（6.64 秒），验证答案包含测试文档事实、有效引用、消息持久化及跨用户隔离。临时账号和知识库已清理。浏览器完整交互尚未在此测试中自动验收。

真实模型模式仅在显式开启时运行，要求数据库恰有一条 Chat 配置，凭据只在测试进程内读取和解密，不打印、不改写原用户配置；会调用真实供应商并产生正常 API 用量。
