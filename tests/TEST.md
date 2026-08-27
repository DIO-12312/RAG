# 测试目录与职责清单

本文件是 `tests/` 的索引和维护清单。新增、删除、重命名测试文件或 `test_*` 函数时，必须在同一改动中更新本文件的目录树和对应职责表。参数化测试在表中按一个测试函数记录，pytest 的实际用例数可能更多。

完整的执行命令、门禁和故障排查见 [`../docs/testing-guide.md`](../docs/testing-guide.md)。本仓库当前的 Functional 与 Resilience 测试使用测试专用 Fake ports；其结果只能证明 Mock Functional / Mock Reliability，不替代真实 MySQL、Elasticsearch、NATS JetStream 或 Docker KILL 验收。

## 目录树

```text
tests/
├─ TEST.md                                 # 本目录的测试索引与职责清单
├─ __init__.py
├─ conftest.py                              # 共享 pytest 配置与 fixture
├─ contract/                                # gRPC、protobuf 与 Port 语义契约
│  ├─ test_build_entrypoints.py
│  ├─ test_container_artifacts.py
│  ├─ test_delete_document_contract.py
│  ├─ test_generated_code.py
│  ├─ test_grpc_application_contract.py
│  ├─ test_metadata_repository_contract.py
│  ├─ test_model_gateway_contract.py
│  ├─ test_object_storage_contract.py
│  ├─ test_parser_chunker_contract.py
│  ├─ test_proto_contract.py
│  ├─ test_retry_job_contract.py
│  ├─ test_search_engine_contract.py
│  └─ test_task_queue_contract.py
├─ e2e/                                     # 真实 Compose 与模型的 gRPC 业务闭环
│  ├─ conftest.py                            # generated gRPC client、真实运行配置和轮询 helpers
│  ├─ test_local_computer_architecture_pdf.py # 可选本地真实 PDF 的长文档用户场景
│  └─ test_real_upload_ingest_retrieve.py
├─ eval/                                    # 固定问题集的检索质量评测
│  ├─ conftest.py                            # 复用真实 E2E gRPC client 与模型运行配置
│  ├─ fixtures/
│  │  ├─ computer_architecture_knowledge.json
│  │  ├─ computer_architecture_knowledge_original.json
│  │  └─ retrieval_quality.json
│  ├─ test_retrieval_quality.py
│  ├─ test_real_computer_architecture_pdf_quality.py
│  └─ test_real_retrieval_quality.py
├─ fakes/                                   # 测试专用 Port 实现；生产代码不得导入
│  ├─ chunker.py
│  ├─ clock.py
│  ├─ container.py
│  ├─ metadata.py
│  ├─ model.py
│  ├─ parser.py
│  ├─ search_engine.py
│  ├─ storage.py
│  └─ task_queue.py
├─ fixtures/                                # 可复用的输入与可靠性证据
│  ├─ documents/
│  │  ├─ guide.md
│  │  ├─ knowledge.txt
│  │  ├─ manual.pdf
│  │  └─ sample.py
│  ├─ golden_chunks/
│  │  ├─ code.json
│  │  ├─ markdown.json
│  │  ├─ pdf.json
│  │  └─ txt.json
│  └─ reliability_matrix.json
├─ functional/                              # 无 Docker 的真实调用链闭环
│  ├─ test_mock_cancel_job.py
│  ├─ test_mock_dedup_and_redelivery.py
│  ├─ test_mock_delete_document.py
│  ├─ test_mock_four_formats.py
│  ├─ test_mock_retry_job.py
│  └─ test_mock_upload_ingest_retrieve.py
├─ integration/                             # 依赖真实 Docker 基础设施的 adapter 验证
│  ├─ conftest.py                            # MySQL DSN、显式迁移根目录、清库和 Repository fixture
│  ├─ test_elasticsearch_adapter.py
│  ├─ test_mysql_concurrency.py
│  ├─ test_mysql_lifecycle.py
│  ├─ test_mysql_migrations.py
│  ├─ test_mysql_outbox_worker.py
│  ├─ test_mysql_submission.py
│  ├─ test_nats_jetstream_adapter.py
│  └─ test_real_embedding_model.py
├─ object/                                  # Git 忽略的本地真实 E2E 输入；不属于仓库 fixture
│  └─ 计组复习.pdf                           # 可选；可由 RAG_E2E_PDF_PATH 覆盖
├─ resilience/                              # 故障、竞态、重投与恢复矩阵
│  ├─ docker/                               # 显式控制真实容器 KILL/stop/start 的恢复验收
│  │  ├─ conftest.py                        # Docker/barrier/gRPC/MySQL/ES/NATS fixtures 与恢复清理
│  │  ├─ docker-compose.resilience.yml       # test-only root、Docker socket 与专用 barrier 卷
│  │  ├─ test_real_concurrency_fences.py
│  │  ├─ test_relay_nats_recovery.py
│  │  └─ test_worker_kill_recovery.py
│  ├─ test_cancel_races.py
│  ├─ test_concurrent_uniqueness.py
│  ├─ test_finalizer_recovery.py
│  ├─ test_generation_fences.py
│  ├─ test_redelivery_idempotency.py
│  └─ test_spec_invariant_matrix.py
└─ unit/                                    # 领域纯规则和单组件行为
   ├─ adapters/
   │  ├─ test_elasticsearch_mapping.py
   │  ├─ test_mysql_schema.py
   │  ├─ test_nats_delivery_mapping.py
   │  └─ test_openai_compatible_model.py
   ├─ application/
   │  ├─ test_document_service.py
   │  ├─ test_job_service.py
   │  └─ test_retrieval_service.py
   ├─ domain/
   │  ├─ test_ids.py
   │  ├─ test_models.py
   │  └─ test_state_machines.py
   ├─ ingestion/
   │  ├─ test_failpoints.py
   │  ├─ test_multiformat_parsers.py
   │  ├─ test_pipeline.py
   │  ├─ test_recursive_chunker.py
   │  ├─ test_text_parser.py
   │  └─ test_worker.py
   ├─ outbox/
   │  ├─ test_finalizer.py
   │  └─ test_relay.py
   ├─ retrieval/
   │  ├─ test_context_builder.py
   │  ├─ test_hybrid.py
   │  ├─ test_provenance.py
   │  └─ test_rerank.py
   ├─ test_config.py
   ├─ test_container_roles.py
   ├─ test_dev_cli.py
   ├─ test_generated_comparison.py
   ├─ test_import_boundaries.py
   ├─ test_observability.py
   └─ test_process_lifecycle.py
```

## 测试类型与运行入口

| 类型 | 负责内容 | 运行命令 | 当前基础设施边界 |
| --- | --- | --- | --- |
| Unit | 领域规则、单个 application service、parser、chunker、Worker、Outbox 与纯检索算法 | `uv run pytest tests/unit` | 不依赖真实外部服务 |
| Contract | protobuf、gRPC DTO、Port 抽象及各实现必须共同遵守的语义 | `uv run pytest tests/contract` | 使用确定性 Fake 或本地对象存储；真实 adapter 契约待补充 |
| Functional | upload → Finalizer → Relay → Worker → Retrieve 的完整调用链 | `uv run pytest tests/functional` | 真实 gRPC/application 流程 + Fake Metadata/Queue/Search/Model |
| Integration | migration 和真实基础设施 adapter 的协议、约束与幂等性 | `uv run pytest -m integration tests/integration` | 要求对应 Docker 服务健康；缺少基础设施时失败而非跳过 |
| E2E | 四格式及可选本地长 PDF 的 upload → 异步摄取 → hybrid Retrieve 容器业务闭环 | `docker compose --profile test run --rm rag-test uv run pytest -m e2e tests/e2e -q` | generated gRPC client + 真实 MySQL/ES/NATS/模型；禁止 Fake；本地 PDF 缺失时仅跳过对应用户场景 |
| Resilience | failpoint、重投、取消、并发、generation fence 与恢复不变量 | `uv run pytest -m resilience tests/resilience` | Mock Reliability；不替代进程强杀和真实中间件恢复 |
| Docker Resilience | Worker/Relay KILL、NATS 停启和真实并发栅栏 | `docker compose -f docker-compose.yml -f tests/resilience/docker/docker-compose.resilience.yml --profile test run --rm rag-test uv run pytest -m docker_resilience tests/resilience/docker -q` | test-only Docker socket + barrier 卷；必须显式选择 marker，禁止删除数据卷 |
| Eval | 固定 30 问检索集及本地真实 PDF 五十问的 Recall@6、MRR@6、locator/page accuracy 与答案包含度 | 离线：`EVAL_FIXTURE=original uv run pytest -m "eval and not e2e" tests/eval` 或默认改写集；真实：`make docker-test SUITE=eval EVAL_FIXTURE=original`；日志清理：`make clear` | 离线 fixture 负责算法门槛；真实评测通过 gRPC 使用真实 MySQL/ES/NATS/模型，禁止 Fake；本地 PDF 缺失时只 skip 五十问用例；`make clear` 只清理 `tests/**/log` 下的文件 |

## Unit 测试函数

Unit 测试负责验证不依赖真实基础设施的最小规则和组件行为。它们应快速定位失败原因，优先覆盖新增分支、错误码和状态转换。

| 文件 | 测试函数 | 职责 |
| --- | --- | --- |
| `adapters/test_elasticsearch_mapping.py` | `test_index_definition_fixes_dense_cosine_and_searchable_field_types` | ES mapping 固定向量维度/cosine，并声明 keyword、text、object 与 flattened 字段。 |
| 同上 | `test_index_definition_rejects_non_positive_dimension` | 非正向量维度不能生成 ES mapping。 |
| 同上 | `test_indexed_chunk_round_trips_through_source_and_hit_without_score_loss` | IndexedChunk 经 `_source`/hit 往返后完整保留 Chunk、定位、metadata 和原始分数。 |
| 同上 | `test_bulk_action_uses_versioned_physical_id_and_rejects_mismatch` | Bulk action 强制使用 document/version/chunk 组成的物理 `_id`，拒绝伪造 ID。 |
| 同上 | `test_bulk_action_rejects_vector_dimension_mismatch` | 写入向量维度必须与 ES mapping 声明一致。 |
| `adapters/test_nats_delivery_mapping.py` | `test_delivery_uses_task_id_consumer_sequence_and_redelivery_count` | 将 JetStream task_id、consumer sequence 和投递次数映射为稳定 Delivery。 |
| 同上 | `test_delivery_rejects_invalid_payload_and_metadata` | 参数化拒绝空/非 UTF-8 task_id 及非法 sequence/delivery metadata。 |
| `adapters/test_mysql_schema.py` | `test_core_schema_declares_all_authoritative_tables_and_innodb` | ORM metadata 声明全部权威表，并固定为 InnoDB。 |
| 同上 | `test_schema_declares_business_uniqueness_constraints` | Fingerprint、版本、幂等记录、manifest 和 Outbox 具有业务唯一约束。 |
| 同上 | `test_schema_declares_aggregate_foreign_keys` | Dataset、Document、Job、Task、Outbox 和 manifest 的聚合外键完整。 |
| 同上 | `test_dataset_deletion_schema_tracks_lifecycle_and_dataset_ownership` | Dataset 生命周期、可空 document Job 与强制 dataset 归属列完整。 |
| 同上 | `test_schema_uses_precise_json_time_and_digest_columns_without_vectors` | JSON、DATETIME(6) 与 64 位摘要字段类型正确，manifest 不保存向量。 |
| `adapters/test_openai_compatible_model.py` | `test_embed_normalizes_url_preserves_batch_order_and_bearer_header` | 规范 endpoint、仅以 Bearer header 鉴权，并对分批乱序响应恢复全局输入顺序。 |
| 同上 | `test_embed_bisects_provider_rejected_multi_input_batches` | 多输入批次被供应商以 HTTP 400 拒绝时按顺序二分，成功后恢复完整向量顺序。 |
| 同上 | `test_embed_empty_input_does_not_call_provider` | 空输入直接返回空向量集合，不产生外部请求。 |
| 同上 | `test_embed_rejects_invalid_schema_count_dimension_and_numbers` | 参数化拒绝错误 object/data、数量、重复 index、维度和非有限数值。 |
| 同上 | `test_auth_failure_is_non_retryable_and_redacts_provider_body` | 401/403 不重试，映射稳定鉴权错误且不泄漏供应商正文或密钥。 |
| 同上 | `test_embed_does_not_duplicate_existing_embeddings_suffix` | 已带 `/embeddings` 的 endpoint 不被重复拼接。 |
| 同上 | `test_transient_statuses_retry_with_a_bound_and_recover` | 429/5xx 按有上限的指数退避重试，并在后续成功时恢复。 |
| 同上 | `test_timeout_exhaustion_maps_to_retryable_unavailable` | 网络超时耗尽重试后映射为可重试 `EMBEDDING_UNAVAILABLE`。 |
| `application/test_document_service.py` | `test_create_dataset_rejects_runtime_embedding_mismatch` | Dataset 声明的 Embedding 模型或维度与运行配置不一致时返回稳定错误。 |
| 同上 | `test_delete_dataset_command_carries_idempotency_and_dataset_scope` | 数据集删除 command 必须携带请求幂等键与 Dataset 作用域。 |
| 同上 | `test_submit_writes_staging_and_atomically_creates_waiting_work` | 上传先写 staging，再原子创建 Document、Job、Task 和 WAITING Outbox。 |
| 同上 | `test_same_file_different_key_reuses_canonical_job_and_cleans_loser_staging` | 相同内容不同幂等键复用 canonical Job，并删除未被引用的 staging。 |
| 同上 | `test_same_idempotency_key_with_different_bytes_is_rejected_without_overwrite` | 同一幂等键不同字节被稳定拒绝，已有对象不被覆盖。 |
| 同上 | `test_sha_and_size_validation_happen_before_metadata_creation` | 大小和 SHA 校验必须先于元数据创建。 |
| 同上 | `test_repository_failure_cleans_staging_object` | Repository 失败后清理本次 staging object。 |
| 同上 | `test_delete_dataset_is_idempotent_and_blocks_new_submissions` | Dataset 删除受理支持同 key 幂等复用，并立即拒绝新上传。 |
| `application/test_job_service.py` | `test_job_service_returns_job_and_task_snapshot` | 查询返回 Job 及 Task 快照。 |
| 同上 | `test_job_service_returns_stable_not_found_failure` | 不存在 Job 返回稳定业务错误。 |
| `application/test_retrieval_service.py` | `test_dense_retrieve_filters_stale_versions_and_preserves_scores` | 过滤已删除或非 active version 命中，同时保留阶段分数。 |
| 同上 | `test_retrieve_rejects_invalid_or_unavailable_requests` | 非法请求、数据集不可用等场景返回稳定失败。 |
| 同上 | `test_rerank_failure_degrades_to_rrf_evidence` | Rerank 不可用时降级为 RRF evidence。 |
| `domain/test_ids.py` | `test_new_id_is_uuid7_compatible` | 新 ID 符合 UUIDv7 兼容格式。 |
| 同上 | `test_canonical_json_and_digests_are_stable` | 规范 JSON 与 digest 在相同输入下稳定。 |
| 同上 | `test_chunk_id_matches_ragflow_xxhash64_rule` | `chunk_id` 遵循 RAGFlow xxHash64 规则。 |
| 同上 | `test_physical_es_id_preserves_document_version_and_chunk` | ES 物理 ID 同时包含 document、version、chunk。 |
| `domain/test_models.py` | `test_dataset_requires_embedding_dimension_and_model` | Dataset 必须具有 embedding 模型及维度。 |
| 同上 | `test_dataset_starts_active_with_a_non_negative_lifecycle_generation` | Dataset 生命周期初始为 ACTIVE，generation 不得为负。 |
| 同上 | `test_dataset_cleanup_job_has_dataset_scope_but_no_document` | dataset cleanup Job 关联 Dataset 而不关联 Document。 |
| 同上 | `test_every_job_requires_dataset_scope` | 所有 Job 必须显式携带 Dataset 作用域。 |
| 同上 | `test_dataset_preserves_tenant_boundary` | Dataset 显式保留所属 tenant，防止后续持久化丢失租户边界。 |
| 同上 | `test_document_versions_and_generation_are_non_negative` | Document 版本号和 generation 不允许为负。 |
| 同上 | `test_job_progress_is_normalized` | Job 进度被规范化到有效范围。 |
| 同上 | `test_task_delivery_counters_cannot_be_negative` | Task 投递计数不允许为负。 |
| `domain/test_state_machines.py` | `test_valid_state_transitions` | Job/Task 合法状态迁移可执行。 |
| 同上 | `test_terminal_states_cannot_be_reopened` | 终态不得重新变为可执行状态。 |
| `ingestion/test_failpoints.py` | `test_non_test_environment_rejects_configured_fault_injection` | 非 TEST 环境一旦配置 file barrier 即在 Settings 校验期失败。 |
| 同上 | `test_factory_defends_against_unconfigured_or_unvalidated_settings` | 未配置时不装配，且 factory 对绕过 Settings 校验的非 TEST/半配置对象保持防御。 |
| 同上 | `test_enabled_checkpoint_writes_reached_and_blocks_until_release` | 启用 checkpoint 写 reached、等待 release，并以持久 reached 实现跨进程一次性触发。 |
| 同上 | `test_disabled_checkpoint_is_noop_and_cancellation_does_not_hang` | 未启用 checkpoint 无副作用，取消 barrier await 会立即传播。 |
| `ingestion/test_multiformat_parsers.py` | `test_markdown_parser_preserves_heading_sections_and_lines` | Markdown 保留标题分段及行定位。 |
| 同上 | `test_code_parser_preserves_language_symbols_and_lines` | 代码保留语言、符号和行定位。 |
| 同上 | `test_pdf_parser_returns_one_traceable_segment_per_text_page` | 文本 PDF 每页输出可追溯片段。 |
| 同上 | `test_router_selects_supported_parser` | Router 为各受支持后缀选择正确 parser。 |
| 同上 | `test_router_rejects_unsupported_source_type` | 不支持的类型返回稳定错误。 |
| 同上 | `test_pdf_parser_rejects_corrupt_bytes` | 损坏 PDF 返回稳定错误。 |
| `ingestion/test_pipeline.py` | `test_pipeline_builds_stable_versioned_chunks_and_upserts_search` | Pipeline 生成稳定的版本化 chunk 并幂等写入检索端。 |
| `ingestion/test_recursive_chunker.py` | `test_recursive_chunker_is_stable_bounded_and_overlapping` | 切块边界稳定、长度受限且 overlap 正确。 |
| 同上 | `test_recursive_chunker_rejects_invalid_overlap` | 非法 overlap 参数被拒绝。 |
| 同上 | `test_recursive_chunker_matches_txt_golden_fixture` | TXT 切块结果与 golden fixture 一致。 |
| `ingestion/test_text_parser.py` | `test_text_parser_normalizes_bom_and_newlines_with_line_locator` | 规范 BOM/换行并生成行定位。 |
| 同上 | `test_text_parser_rejects_invalid_utf8_with_stable_error` | 非法 UTF-8 返回稳定错误码。 |
| `ingestion/test_worker.py` | `test_worker_claims_executes_completes_then_acks` | Worker 的认领、执行、完成、ACK 顺序正确。 |
| 同上 | `test_worker_returns_false_when_queue_is_empty` | 空队列时 Worker 不执行任务并返回空结果。 |
| 同上 | `test_worker_naks_retryable_failure_then_fails_at_delivery_limit` | 可重试失败 NAK，达到投递上限后写入失败终态。 |
| `outbox/test_finalizer.py` | `test_finalizer_promotes_object_before_outbox_becomes_ready` | 仅正式对象提升成功后，Outbox 才能 READY。 |
| `outbox/test_relay.py` | `test_relay_only_publishes_ready_outbox` | Relay 只发布 READY Outbox。 |
| 同上 | `test_publish_then_crash_before_mark_is_safely_retried` | 发布成功但标记前崩溃时，重复发布可幂等收敛。 |
| `retrieval/test_context_builder.py` | `test_context_builder_keeps_whole_evidence_within_budget` | ContextBuilder 在预算内保留完整 evidence。 |
| 同上 | `test_token_estimate_is_deterministic_and_nonzero` | token 估算稳定且非零。 |
| `retrieval/test_hybrid.py` | `test_rrf_fuses_routes_deduplicates_and_keeps_stage_scores` | RRF 融合双路候选、去重并保留阶段分数。 |
| 同上 | `test_rrf_uses_record_id_as_stable_final_tie_breaker` | RRF 同分时使用 record ID 稳定排序。 |
| 同上 | `test_rrf_rejects_invalid_constant` | 非法 RRF 常量被拒绝。 |
| `retrieval/test_provenance.py` | `test_dense_evidence_preserves_traceable_chunk_fields` | evidence 保留可追溯 chunk 字段。 |
| `retrieval/test_rerank.py` | `test_rerank_scores_reorder_stably_and_keep_fusion_data` | Rerank 稳定重排并保留 fusion 数据。 |
| 同上 | `test_rerank_rejects_score_count_mismatch` | 候选数和重排分数数目不一致时拒绝。 |
| 同上 | `test_rerank_rejects_invalid_top_n` | 非法 Top-N 参数被拒绝。 |
| `test_config.py` | `test_package_import_has_no_runtime_side_effects` | 包导入不建立运行时外部连接。 |
| 同上 | `test_settings_can_be_constructed_explicitly_for_tests` | 测试可显式构造 Settings，migration root 有稳定默认值。 |
| 同上 | `test_settings_builds_a_normalized_secret_embedding_profile` | 完整模型配置生成规范化 `/embeddings` endpoint，并在对象表示中隐藏 API Key。 |
| 同上 | `test_embedding_profile_rejects_missing_or_partial_configuration` | Server/Worker 获取模型配置时拒绝缺失或不完整的 provider 设置。 |
| 同上 | `test_parser_and_chunk_runtime_settings_reject_inconsistent_values` | parser 版本和 chunk overlap/size 的运行配置必须自洽。 |
| 同上 | `test_production_rejects_grpc_reflection` | 生产环境禁止 gRPC Reflection。 |
| 同上 | `test_production_rejects_development_mysql_credentials` | 生产环境拒绝开发 MySQL 凭据。 |
| 同上 | `test_test_markers_are_registered` | pytest 的快速、真实模型、E2E、Mock/Docker 恢复和评测标记均已注册。 |
| 同上 | `test_runtime_adapter_dependencies_are_importable` | MySQL、ES、NATS、模型 HTTP adapter 的运行依赖可导入。 |
| `test_container_roles.py` | `test_role_factories_build_only_allowed_dependencies_and_services` | Server、Worker、Outbox 只装配各自允许的 adapter 与 application service。 |
| 同上 | `test_container_close_is_reverse_order_and_idempotent` | 容器资源按创建逆序关闭，重复关闭不重复执行。 |
| 同上 | `test_test_only_failpoint_is_wired_only_into_worker_and_outbox_roles` | 只有 TEST Worker/Outbox 装配 file barrier，Server 永不注入。 |
| `test_dev_cli.py` | `test_mutating_commands_require_request_and_idempotency_keys` | gRPC 调试 CLI 的变更命令强制要求 request/idempotency key。 |
| 同上 | `test_submit_document_streams_one_header_then_bounded_data_frames` | 调试 CLI 按一个 header 加有界 data 帧流式上传文件。 |
| `test_generated_comparison.py` | `test_generated_comparison_ignores_only_line_endings` | protobuf 生成物同步检查忽略 Windows/Unix 换行符差异。 |
| 同上 | `test_generated_comparison_rejects_content_changes` | protobuf 生成物同步检查仍拒绝真实文本变化。 |
| `test_import_boundaries.py` | `test_final_layer_packages_exist` | 规定的分层包存在。 |
| 同上 | `test_source_imports_respect_layer_boundaries` | source import 遵守 domain/application/adapter 分层。 |
| 同上 | `test_production_source_never_imports_test_fakes` | 生产源码不得导入 `tests/fakes`。 |
| 同上 | `test_all_declared_ports_are_protocols` | 所有 Port 均以 Protocol 声明。 |
| `test_observability.py` | `test_rag_event_always_contains_correlation_and_stage_fields` | 结构化事件包含关联 ID 与阶段字段。 |
| `test_process_lifecycle.py` | `test_empty_background_process_stops_without_external_connections` | Worker/Outbox 即使处于长轮询等待，也可由 stop event 立即退出且不连接外部服务。 |
| 同上 | `test_grpc_server_starts_and_stops_cleanly` | gRPC Server 可启动并优雅停止。 |
| 同上 | `test_all_unopened_rpc_methods_return_feature_not_available` | 未开放 RPC 返回 `FEATURE_NOT_AVAILABLE`。 |

## Contract 测试函数

Contract 测试负责固定 protobuf、gRPC 及各基础设施 Port 的可替换语义。新增 adapter、修改 RPC 或 Port 时，必须先更新相关 contract 测试。

| 文件 | 测试函数 | 职责 |
| --- | --- | --- |
| `test_build_entrypoints.py` | `test_makefile_offline_targets_are_commented_earthly_only_entrypoints` | Makefile 的离线公共入口均有说明，并且只负责转发 Earthly target。 |
| 同上 | `test_earthfile_pins_tools_and_separates_offline_targets` | Earthfile 固定 Python/uv 工具链，显式导出 protobuf 文件且不携带缓存，并定义质量、离线测试与 Secret 边界。 |
| 同上 | `test_docker_entrypoints_validate_suites_scan_logs_and_preserve_volumes` | Docker 公共入口使用合法的复用 Function，验证 suite、静默校验 Compose、扫描日志并安全停服；eval suite 必须同时收集既有 30 问和本地 PDF 五十问，且禁止删除持久卷。 |
| 同上 | `test_hook_and_quick_workflow_delegate_only_to_make_ci` | Git Hook 与无 Secret quick workflow 只调用固定 Earthly 环境下的 `make ci`。 |
| 同上 | `test_docker_workflow_delegates_real_suites_and_always_cleans_up` | Secret-backed Docker workflow 只转发三个真实测试 suite，并保证两个 job 始终调用统一清理入口。 |
| `test_container_artifacts.py` | `test_package_and_container_use_canonical_root_readme` | GitHub 首页、Python package、Docker 镜像与 Earthly 依赖安装统一使用仓库根 README，禁止保留重复入口。 |
| `test_container_artifacts.py` | `test_runtime_image_and_context_exclude_secrets_and_test_artifacts` | runtime/test 镜像目标、非 root 用户及 build context 排除规则正确。 |
| 同上 | `test_compose_declares_migration_health_role_secrets_and_shared_storage` | Compose 固定迁移顺序、健康依赖、共享对象卷及模型密钥角色边界。 |
| 同上 | `test_secret_scanner_fails_without_echoing_the_secret` | 日志命中模型密钥时扫描失败且不回显 Secret。 |
| 同上 | `test_healthcheck_parses_ndjson_and_requires_every_process_to_be_healthy` | 健康检查要求基础设施和应用健康、迁移成功退出。 |
| 同上 | `test_healthcheck_decodes_docker_output_as_utf8` | Windows 宿主机按 UTF-8 安全解析 Docker Unicode 输出。 |
| 同上 | `test_quality_workflows_keep_offline_and_secret_backed_suites_separate` | PR 门禁通过 `make ci` 保持离线，真实 Docker 作业只由 push/手动/夜间触发，并经 Make/Earthly 使用 Secret-backed 测试入口。 |
| `test_delete_document_contract.py` | `test_delete_atomically_hides_document_cancels_ingest_and_creates_cleanup` | 删除原子隐藏文档、取消摄取并创建清理任务。 |
| 同上 | `test_new_delete_request_for_deleted_document_is_rejected` | 已删除文档的新删除请求返回稳定错误。 |
| `test_generated_code.py` | `test_generated_python_is_in_sync_with_proto` | Python protobuf 生成物与 `.proto` 保持同步。 |
| `test_grpc_application_contract.py` | `test_open_rpc_methods_convert_application_results` | 已开放 RPC 正确转换 application 结果，上传摘要使用注入的 parser/chunk/model 配置。 |
| 同上 | `test_delete_dataset_maps_success_reuse_and_stable_failures` | DeleteDataset 映射成功与幂等复用，并保留删除中、不存在和缺少幂等键错误码。 |
| 同上 | `test_rpc_maps_domain_failures_and_keeps_future_methods_closed` | 领域错误映射正确，未来方法保持关闭。 |
| 同上 | `test_submit_document_rejects_data_before_header` | 上传流首帧必须为 header。 |
| 同上 | `test_open_methods_work_through_generated_grpc_transport` | 已开放方法可经生成的 gRPC transport 调用。 |
| `test_metadata_repository_contract.py` | `test_submit_atomically_creates_task_and_waiting_outbox_and_deduplicates` | 提交原子创建 Task/WAITING Outbox，并分别验证同 key 与同 fingerprint 去重。 |
| 同上 | `test_metadata_port_exposes_dataset_deletion_lifecycle` | Metadata Port 声明 Dataset 删除、对象快照和最终 purge 契约。 |
| 同上 | `test_submit_failure_does_not_leave_partial_metadata` | 提交失败不留下部分元数据。 |
| 同上 | `test_finalizer_transition_and_task_claim_are_conditional` | Finalizer/Relay 转换为条件更新；Task 按 delivery sequence 去重并允许更高序号重投。 |
| 同上 | `test_complete_and_fail_are_conditional_and_visibility_uses_active_version` | 完成/失败为条件更新，检索可见性复核 active version。 |
| `test_model_gateway_contract.py` | `test_fake_model_is_deterministic_and_dimensionally_stable` | Fake Model 的 embedding 确定且维度稳定。 |
| 同上 | `test_unconfigured_rerank_is_explicitly_retryable_unavailable` | 未配置真实 Rerank endpoint 时返回可降级、可重试的稳定错误，不伪造分数。 |
| `test_object_storage_contract.py` | `test_object_storage_write_promote_read_and_delete_are_idempotent` | ObjectStorage 的写、提升、读、删具备幂等语义。 |
| 同上 | `test_local_object_storage_has_the_same_semantics` | LocalObjectStorage 与端口契约语义一致。 |
| 同上 | `test_local_object_storage_rejects_path_traversal` | LocalObjectStorage 拒绝路径穿越。 |
| `test_parser_chunker_contract.py` | `test_parser_and_chunker_preserve_text_order_and_locator` | Parser/Chunker 保持文本顺序和 locator。 |
| `test_proto_contract.py` | `test_rag_service_defines_the_complete_rpc_surface` | proto 定义完整 RagService RPC 面。 |
| 同上 | `test_delete_dataset_contract_keeps_job_history_scoped_to_dataset` | DeleteDataset 字段号、结果和 dataset 作用域 JobType 保持兼容。 |
| 同上 | `test_every_response_has_result_and_business_error_outcome` | 每个响应都有 result 或 BusinessError 的 oneof。 |
| 同上 | `test_upload_request_is_a_header_or_data_frame` | 上传请求只允许 header 或 data 帧。 |
| 同上 | `test_idempotency_context_is_only_used_by_commands` | 幂等上下文仅用于命令型 RPC。 |
| 同上 | `test_evidence_contains_provenance_and_stage_scores_but_no_answer` | Evidence 包含 provenance/阶段分数，不生成答案。 |
| 同上 | `test_business_error_has_stable_machine_readable_fields` | BusinessError 具有稳定的机器可读字段。 |
| `test_retry_job_contract.py` | `test_retry_creates_new_job_task_and_ready_outbox_without_reviving_original` | Retry 创建新 Job/Task/READY Outbox，不复活原 Job。 |
| 同上 | `test_retry_rejects_failure_without_final_object` | 无正式对象的失败不可 Retry。 |
| 同上 | `test_retry_enforces_user_retry_limit` | Retry 强制执行用户重试上限。 |
| `test_search_engine_contract.py` | `test_search_upsert_is_idempotent_and_dense_sparse_are_separate` | Search upsert 幂等，Dense 与 Sparse 候选分离，并共同遵守 Dataset/metadata 过滤。 |
| 同上 | `test_search_can_delete_an_entire_dataset_idempotently` | Search Port 可按 Dataset 幂等删除全部索引记录。 |
| `test_task_queue_contract.py` | `test_queue_preserves_at_least_once_delivery_and_explicit_ack_nak` | Queue 保持至少一次投递、重复 publish 和显式 ACK/NAK。 |
| 同上 | `test_unacked_delivery_can_be_redelivered` | 未 ACK delivery 可重新投递。 |

## Integration 测试函数

Integration 测试直连真实中间件，验证 SDK、DDL 和服务端行为；显式选择该测试类型时，基础设施不可用必须失败。`integration/conftest.py` 的 `migrations_root` fixture 在宿主机解析仓库根目录，在非 editable 测试镜像中读取 `RAG_MIGRATIONS_ROOT=/app`，禁止依赖 site-packages 相对层级猜测迁移位置。

| 文件 | 测试函数 | 职责 |
| --- | --- | --- |
| `test_elasticsearch_adapter.py` | `test_real_es_upsert_dense_bm25_isolation_and_metadata_filters` | 真实 ES 验证 Bulk 幂等、KNN/BM25 召回、稳定排序、Dataset 隔离和 metadata 过滤。 |
| 同上 | `test_real_es_version_and_document_delete_are_idempotent` | 真实 ES 按版本和整文档删除均可重复执行并收敛到正确记录数。 |
| `test_nats_jetstream_adapter.py` | `test_real_jetstream_preserves_duplicate_publish_and_ack_removes_deliveries` | 真实 JetStream 保留重复 task_id 消息，PubAck 后可消费，显式 ACK 后移除。 |
| 同上 | `test_real_jetstream_redelivers_after_ack_wait_and_honors_delayed_nak` | 真实 durable consumer 在 ACK 超时后重投，并遵守 NAK delay。 |
| 同上 | `test_real_jetstream_provisioning_is_idempotent_and_rejects_incompatible_consumer` | stream/consumer 同配置装配幂等，不兼容 consumer 参数 fail fast。 |
| `test_mysql_concurrency.py` | `test_concurrent_retry_keys_reuse_one_active_child_job` | 八个并发 Retry key 只创建并复用一个活跃子 Job/Task，重试计数只增加一次。 |
| 同上 | `test_concurrent_rebuilds_allocate_distinct_index_versions` | 四个并发重建在 Document 行锁下分配互不重复的 index version，旧 active version 保持可见。 |
| 同上 | `test_out_of_order_rebuild_completion_never_regresses_active_version` | 高版本先完成、低版本迟到时 active version 只前进，迟到 IndexBuild 被废弃并创建版本清理 Job。 |
| 同上 | `test_delete_and_finalizer_race_never_leaves_ingest_outbox_ready` | 删除与 Finalizer 并发时，摄取 Outbox 最终必为 CANCELLED，且仅删除清理 Outbox 可发布。 |
| 同上 | `test_concurrent_dataset_delete_keys_create_one_cleanup_job` | 并发不同删除 key 只允许一个受理并创建一个 Dataset 清理 Job。 |
| `test_mysql_lifecycle.py` | `test_pending_cancel_is_immediate_idempotent_and_withdraws_outbox` | PENDING 取消原子终止 Job/Task、撤销未发布 Outbox，并支持同 key 幂等回放。 |
| 同上 | `test_running_cancel_converges_at_completion_without_activating_version` | RUNNING 取消先记录请求，再由完成 checkpoint 收敛为 CANCELLED、放弃索引版本并创建清理任务。 |
| 同上 | `test_delete_hides_immediately_cancels_ingest_and_cleanup_honors_generation` | 删除立即隐藏文档、阻断旧摄取完成，并由 generation 匹配的清理 Task 收敛终态。 |
| 同上 | `test_new_delete_key_for_deleted_document_is_rejected` | 已删除 Document 只允许原幂等 key 回放，新 key 返回稳定冲突。 |
| 同上 | `test_delete_dataset_atomically_fences_children_and_enqueues_cleanup` | Dataset 删除原子隔离子聚合、撤销旧 Outbox，并创建唯一 READY 清理任务。 |
| 同上 | `test_delete_dataset_rejects_new_key_and_new_ingestion` | DELETING Dataset 拒绝新删除 key 与新摄取。 |
| `test_mysql_migrations.py` | `test_upgrade_head_is_idempotent_and_creates_innodb_schema` | 对真实 MySQL 连续升级两次，验证 revision、默认租户、InnoDB 表和关键唯一约束。 |
| `test_mysql_outbox_worker.py` | `test_outbox_transitions_delivery_dedup_and_atomic_completion` | 验证 WAITING→READY→PUBLISHED、delivery 去重，以及 manifest/version/Job/Task 原子完成。 |
| 同上 | `test_finalizer_exhaustion_atomically_fails_and_releases_fingerprint` | Finalizer 耗尽后原子写 Task/Job FAILED、Outbox CANCELLED、Fingerprint RELEASED。 |
| 同上 | `test_deleted_generation_fence_prevents_object_ready_and_task_claim` | Document 删除/generation 失配时禁止对象就绪和 Task 认领。 |
| 同上 | `test_fail_task_persists_retryability_and_terminal_state_once` | Worker 失败只落一次终态，并按正式对象是否存在设置 Fingerprint 可重试状态。 |
| `test_mysql_submission.py` | `test_concurrent_same_fingerprint_creates_one_canonical_task_and_outbox` | 并发同内容上传只保留一个 canonical Document/Job/Task/Outbox，并记录两个幂等结果。 |
| 同上 | `test_exception_before_commit_rolls_back_all_submission_rows` | Outbox INSERT 前异常使 Document/Fingerprint/Job/Task/Outbox/IndexBuild/幂等记录全部回滚。 |
| 同上 | `test_same_idempotency_key_replays_result_and_rejects_changed_command` | 同 key 同命令回放首次结果，同 key 不同命令返回稳定冲突且不产生额外状态。 |
| `test_real_embedding_model.py` | `test_real_embedding_returns_finite_declared_dimension_and_stable_duplicates` | 真实 API 分批返回声明维度的有限向量，相同中文文本向量保持高度一致。 |
| 同上 | `test_real_embedding_ranks_related_chinese_text_above_unrelated_text` | 真实模型对中文相关语句的余弦相似度高于无关语句。 |

## E2E 测试函数

E2E 测试只从 generated gRPC client 驱动已启动的 Compose 服务，不直接导入 application、adapter 或 Fake。`e2e/conftest.py` 提供真实 gRPC channel、Embedding 运行配置，以及 Dataset 创建、文档提交、Job 轮询和检索 helpers。

| 文件 | 测试函数 | 职责 |
| --- | --- | --- |
| `test_real_upload_ingest_retrieve.py` | `test_real_upload_ingest_and_hybrid_retrieve_preserves_provenance` | 分别上传 TXT、Markdown、Python 和 PDF，等待真实异步摄取成功，再验证 Dense/BM25/RRF evidence、active index version 与 line/symbol/language/page provenance。 |
| `test_local_computer_architecture_pdf.py` | `test_local_user_uploads_review_pdf_and_retrieves_distant_topics` | 从 generated gRPC client 上传 Git 忽略的 44 页本地 PDF，等待真实摄取后检索前部“计算机基本功能”和后部“DMA 传送方式”，验证中文正文、页码、分数及来源血缘。 |

## Functional 测试函数

Functional 测试负责验证跨层调用链。它们使用真实 gRPC、application、Outbox、Worker、pipeline 与 retrieval，只在基础设施 Port 上替换为 `tests/fakes/` 的确定性实现。

| 文件 | 测试函数 | 职责 |
| --- | --- | --- |
| `test_mock_upload_ingest_retrieve.py` | `test_mock_grpc_upload_async_ingest_and_dense_retrieve` | 通过 gRPC 完成上传、异步摄取、Job 查询和 Dense evidence 检索。 |
| 同上 | `test_deleted_dataset_is_rejected_before_cleanup_worker_runs` | Dataset 删除 RPC 受理后、清理 Worker 执行前，检索已立即被拒绝。 |
| `test_mock_four_formats.py` | `test_four_supported_formats_return_precise_provenance` | 四种支持格式均能摄取、检索并返回精确 provenance。 |
| `test_mock_dedup_and_redelivery.py` | `test_mock_dedup_and_relay_duplicate_delivery_converge` | 内容去重与 Relay 重复投递最终收敛为一份可见索引。 |
| `test_mock_retry_job.py` | `test_retry_rpc_creates_new_job_and_worker_completes_it` | Retry RPC 创建新任务，Worker 可完成该重试任务。 |
| `test_mock_cancel_job.py` | `test_cancel_rpc_stops_pending_ingestion_and_is_idempotent` | Cancel RPC 停止 PENDING 摄取且重复取消幂等。 |
| `test_mock_delete_document.py` | `test_delete_is_immediately_invisible_then_worker_cleans_storage_and_index` | 删除后立即不可检索，随后 Worker 清理对象与索引。 |

## Resilience 测试函数

Resilience 测试负责覆盖 failpoint、重投、并发和状态栅栏。`reliability_matrix.json` 将这些测试映射到 SPEC T1～T25，并标记仍需真实基础设施复验的项目。

| 文件 | 测试函数 | 职责 |
| --- | --- | --- |
| `test_cancel_races.py` | `test_pending_cancel_cancels_task_and_unpublished_outbox` | PENDING 取消会终止 Task 并撤销未发布 Outbox。 |
| 同上 | `test_cancel_after_publish_before_claim_makes_worker_only_ack` | 发布后、认领前取消时 Worker 只 ACK。 |
| 同上 | `test_running_cancel_after_index_write_never_activates_version` | 运行中取消即使已索引也不得激活新版本。 |
| `test_concurrent_uniqueness.py` | `test_concurrent_same_file_upload_has_one_canonical_job_and_no_loser_staging` | 并发同文件上传只保留一个 canonical Job 和被引用 staging。 |
| 同上 | `test_concurrent_retry_calls_create_one_active_child` | 并发 Retry 只创建一个活跃子 Job。 |
| 同上 | `test_concurrent_rebuilds_allocate_distinct_index_versions` | 并发重建获得不同 index version。 |
| `test_finalizer_recovery.py` | `test_finalizer_exhaustion_reaches_stable_failure_terminal` | Finalizer 耗尽重试后进入稳定失败终态。 |
| 同上 | `test_staging_sweeper_preserves_waiting_reference_and_deletes_orphan` | Sweeper 保留 WAITING 引用、清理孤儿 staging。 |
| `test_generation_fences.py` | `test_cancel_after_index_write_creates_version_cleanup_task` | 索引写后取消会创建版本清理任务。 |
| 同上 | `test_delete_after_index_write_never_reactivates_and_cleanup_removes_everything` | 删除与索引写并发不能复活文档，清理全部残留。 |
| 同上 | `test_delete_between_promote_and_ready_compensates_final_object` | 提升正式对象与 READY 之间删除时补偿该对象。 |
| `test_redelivery_idempotency.py` | `test_crash_after_index_write_redelivers_and_upserts_idempotently` | 索引写后崩溃重投不会产生重复可见索引。 |
| 同上 | `test_crash_after_success_before_ack_redelivery_only_acks` | 成功但 ACK 前崩溃后，重投仅 ACK 不重跑 pipeline。 |
| `test_spec_invariant_matrix.py` | `test_every_spec_invariant_has_mock_evidence_and_explicit_real_validation_status` | 每项 SPEC 不变量都有 Mock 证据；所有要求真实复验的条目都指向仓库内真实测试文件。 |

## Docker Resilience 测试函数

Docker Resilience 只在命令显式包含 `-m docker_resilience` 时执行；默认快速、Mock resilience 和 coverage 会跳过。`docker/conftest.py` 提供 generated gRPC stub、真实 MySQL/ES/NATS probes、Docker CLI 控制器和共享卷 barrier，并在每个测试后释放断点、重启精确容器；不会执行 `down -v`。

| 文件 | 测试函数 | 职责 |
| --- | --- | --- |
| `docker/test_worker_kill_recovery.py` | `test_worker_kill_after_index_write_redelivers_without_duplicate_chunks` | ES 已写、MySQL 未完成时 SIGKILL Worker，验证 JetStream 重投、attempt 递增且 manifest/ES 不重复。 |
| 同上 | `test_worker_kill_after_success_before_ack_redelivery_only_acks` | MySQL 已成功、ACK 前 SIGKILL，验证重投只 ACK，attempt、manifest 和 ES 均不重复。 |
| `docker/test_relay_nats_recovery.py` | `test_relay_kill_after_publish_before_mark_republishes_safely` | NATS PubAck 后、Outbox 标记前 SIGKILL Relay，验证 READY 重发并最终 PUBLISHED。 |
| 同上 | `test_ready_outbox_survives_nats_stop_and_publishes_after_restart` | NATS 停机期间事务创建 READY 删除清理事件，恢复后发布、消费并完成清理。 |
| `docker/test_real_concurrency_fences.py` | `test_concurrent_same_content_upload_reuses_one_canonical_job` | 八个真实 gRPC 同内容上传只产生一个 fingerprint/canonical Job/Task/Outbox。 |
| 同上 | `test_concurrent_retry_calls_create_one_child_and_one_delivery` | 八个并发 Retry RPC 只产生一个子 Job，真实队列最终收敛。 |
| 同上 | `test_concurrent_rebuilds_allocate_unique_monotonic_versions` | 并发 rebuild 分配唯一版本，并在乱序执行下保持 active version 单调。 |
| 同上 | `test_delete_during_blocked_rebuild_never_resurrects_document` | rebuild 写 ES 后阻塞并并发 Delete，验证立即不可见、generation fence、对象/ES 清理且不复活。 |

## Eval 测试函数

Eval 测试负责防止检索排序和 evidence 定位质量回退。不得以 LLM 自由文本逐字 snapshot 取代这些指标。

| 文件 | 测试函数 | 职责 |
| --- | --- | --- |
| `test_retrieval_quality.py` | `test_fixed_thirty_question_quality_baseline` | 在固定 30 问集上验证 Recall@6、MRR@6 和 locator accuracy 门槛。 |
| `test_real_computer_architecture_pdf_quality.py` | `test_real_computer_architecture_pdf_quality` | 一次完整摄取本地 44 页计组 PDF，执行 50 次 query embedding 与混合检索；默认使用语义改写集，也可通过 `EVAL_FIXTURE=original` 选择原始问题集，记录每条 query 的前 20 个 embedding 维度和服务实际 Top-K evidence 到 `eval/log/`，并按 PdfParser 页码和细粒度关键短语聚合 Recall@6、MRR@6、Top-1 页命中率及答案包含度。 |
| 同上 | `test_case_log_record_preserves_embedding_and_top_k_details` | 离线验证单条日志记录保留前 20 个 embedding 维度、Top-K 排名、分数和 evidence 原文。 |
| 同上 | `test_case_log_record_truncates_embedding_to_first_20_dimensions` | 离线验证超过 20 维的 embedding 只写入前 20 个维度。 |
| 同上 | `test_write_run_log_persists_json_with_completion_time` | 离线验证评测日志可写入 JSON 文件并包含完成时间。 |
| 同上 | `test_original_and_rephrased_fixtures_only_change_query` | 离线验证原始集与语义改写集的 50 条记录除 `query` 外完全一致。 |
| `test_real_retrieval_quality.py` | `test_real_thirty_question_quality_baseline` | 十份固定语料经真实 gRPC 摄取后执行 30 问，使用真实 chunk_id 验证 Recall@6、MRR@6 和来源行定位。 |

## Fake 与 Fixture 的职责

| 路径 | 职责 |
| --- | --- |
| `fakes/container.py` | 组装真实调用链和测试专用 Fake ports 的 Functional harness。 |
| `fakes/metadata.py` | 模拟元数据事务、状态机、条件更新和去重语义。 |
| `fakes/task_queue.py` | 模拟至少一次投递、ACK、NAK 与 redelivery。 |
| `fakes/search_engine.py` | 模拟版本化索引、Dense/Sparse 候选和删除。 |
| `fakes/model.py` | 提供确定性 embedding 与 rerank 行为。 |
| `fakes/storage.py`、`fakes/parser.py`、`fakes/chunker.py`、`fakes/clock.py` | 为单测提供可控的端口替身、时间和输入。 |
| `fixtures/golden_chunks/*.json` | 四种文档格式的切块和 locator 基准。 |
| `fixtures/documents/*` | 真实 Docker E2E 的 TXT、Markdown、Python 与确定性生成 PDF 输入；`scripts/build_test_fixtures.py --check` 防止 PDF 漂移。 |
| `fixtures/reliability_matrix.json` | SPEC T1～T25 与 Mock/真实测试节点、真实复验要求的可执行证据映射。 |
| `eval/fixtures/computer_architecture_knowledge.json`、`computer_architecture_knowledge_original.json` | 同一批 50 条计组评测样本的语义改写集与原始问题集；两者仅 `query` 不同，页码、参考答案和细粒度关键短语保持一致；源 PDF 保持本地且不进 Git。 |
| `eval/fixtures/retrieval_quality.json` | 固定问题、相关 chunk 与 locator 的检索质量基线。 |

## 维护规则

1. 新增、删除、移动或重命名测试文件时，同步更新“目录树”。
2. 新增、删除、重命名 `test_*` 函数或显著改变其断言职责时，同步更新对应类型的函数表。
3. 修改 RPC、Port、状态机、Outbox、Worker、重试、取消或删除语义时，同时检查 Contract、Functional、Resilience 三类表是否仍准确。
4. 新增 marker、fixture 或 Fake port 时，在本文件和 [`../docs/testing-guide.md`](../docs/testing-guide.md) 中补充运行边界；Fake 不得进入生产 bootstrap。
5. 测试文档与测试代码必须在同一提交中评审；缺少本文件同步的测试改动不视为完成。
