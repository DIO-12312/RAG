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
├─ eval/                                    # 固定问题集的检索质量评测
│  ├─ fixtures/
│  │  └─ retrieval_quality.json
│  └─ test_retrieval_quality.py
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
│  ├─ conftest.py                            # MySQL DSN、迁移、清库和 Repository fixture
│  ├─ test_elasticsearch_adapter.py
│  ├─ test_mysql_concurrency.py
│  ├─ test_mysql_lifecycle.py
│  ├─ test_mysql_migrations.py
│  ├─ test_mysql_outbox_worker.py
│  ├─ test_mysql_submission.py
│  ├─ test_nats_jetstream_adapter.py
│  └─ test_real_embedding_model.py
├─ resilience/                              # 故障、竞态、重投与恢复矩阵
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
| Resilience | failpoint、重投、取消、并发、generation fence 与恢复不变量 | `uv run pytest -m resilience tests/resilience` | Mock Reliability；不替代进程强杀和真实中间件恢复 |
| Eval | 固定 30 问检索集的 Recall@6、MRR@6、locator accuracy | `uv run pytest -m eval tests/eval` | 确定性 Fake 检索与固定 fixture |

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
| 同上 | `test_schema_uses_precise_json_time_and_digest_columns_without_vectors` | JSON、DATETIME(6) 与 64 位摘要字段类型正确，manifest 不保存向量。 |
| `adapters/test_openai_compatible_model.py` | `test_embed_normalizes_url_preserves_batch_order_and_bearer_header` | 规范 endpoint、仅以 Bearer header 鉴权，并对分批乱序响应恢复全局输入顺序。 |
| 同上 | `test_embed_empty_input_does_not_call_provider` | 空输入直接返回空向量集合，不产生外部请求。 |
| 同上 | `test_embed_rejects_invalid_schema_count_dimension_and_numbers` | 参数化拒绝错误 object/data、数量、重复 index、维度和非有限数值。 |
| 同上 | `test_auth_failure_is_non_retryable_and_redacts_provider_body` | 401/403 不重试，映射稳定鉴权错误且不泄漏供应商正文或密钥。 |
| 同上 | `test_embed_does_not_duplicate_existing_embeddings_suffix` | 已带 `/embeddings` 的 endpoint 不被重复拼接。 |
| 同上 | `test_transient_statuses_retry_with_a_bound_and_recover` | 429/5xx 按有上限的指数退避重试，并在后续成功时恢复。 |
| 同上 | `test_timeout_exhaustion_maps_to_retryable_unavailable` | 网络超时耗尽重试后映射为可重试 `EMBEDDING_UNAVAILABLE`。 |
| `application/test_document_service.py` | `test_create_dataset_rejects_runtime_embedding_mismatch` | Dataset 声明的 Embedding 模型或维度与运行配置不一致时返回稳定错误。 |
| 同上 | `test_submit_writes_staging_and_atomically_creates_waiting_work` | 上传先写 staging，再原子创建 Document、Job、Task 和 WAITING Outbox。 |
| 同上 | `test_same_file_different_key_reuses_canonical_job_and_cleans_loser_staging` | 相同内容不同幂等键复用 canonical Job，并删除未被引用的 staging。 |
| 同上 | `test_same_idempotency_key_with_different_bytes_is_rejected_without_overwrite` | 同一幂等键不同字节被稳定拒绝，已有对象不被覆盖。 |
| 同上 | `test_sha_and_size_validation_happen_before_metadata_creation` | 大小和 SHA 校验必须先于元数据创建。 |
| 同上 | `test_repository_failure_cleans_staging_object` | Repository 失败后清理本次 staging object。 |
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
| 同上 | `test_dataset_preserves_tenant_boundary` | Dataset 显式保留所属 tenant，防止后续持久化丢失租户边界。 |
| 同上 | `test_document_versions_and_generation_are_non_negative` | Document 版本号和 generation 不允许为负。 |
| 同上 | `test_job_progress_is_normalized` | Job 进度被规范化到有效范围。 |
| 同上 | `test_task_delivery_counters_cannot_be_negative` | Task 投递计数不允许为负。 |
| `domain/test_state_machines.py` | `test_valid_state_transitions` | Job/Task 合法状态迁移可执行。 |
| 同上 | `test_terminal_states_cannot_be_reopened` | 终态不得重新变为可执行状态。 |
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
| 同上 | `test_settings_can_be_constructed_explicitly_for_tests` | 测试可显式构造 Settings。 |
| 同上 | `test_settings_builds_a_normalized_secret_embedding_profile` | 完整模型配置生成规范化 `/embeddings` endpoint，并在对象表示中隐藏 API Key。 |
| 同上 | `test_embedding_profile_rejects_missing_or_partial_configuration` | Server/Worker 获取模型配置时拒绝缺失或不完整的 provider 设置。 |
| 同上 | `test_parser_and_chunk_runtime_settings_reject_inconsistent_values` | parser 版本和 chunk overlap/size 的运行配置必须自洽。 |
| 同上 | `test_production_rejects_grpc_reflection` | 生产环境禁止 gRPC Reflection。 |
| 同上 | `test_production_rejects_development_mysql_credentials` | 生产环境拒绝开发 MySQL 凭据。 |
| 同上 | `test_test_markers_are_registered` | pytest 的快速、真实模型、E2E、Mock/Docker 恢复和评测标记均已注册。 |
| 同上 | `test_runtime_adapter_dependencies_are_importable` | MySQL、ES、NATS、模型 HTTP adapter 的运行依赖可导入。 |
| `test_container_roles.py` | `test_role_factories_build_only_allowed_dependencies_and_services` | Server、Worker、Outbox 只装配各自允许的 adapter 与 application service。 |
| 同上 | `test_container_close_is_reverse_order_and_idempotent` | 容器资源按创建逆序关闭，重复关闭不重复执行。 |
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
| `test_delete_document_contract.py` | `test_delete_atomically_hides_document_cancels_ingest_and_creates_cleanup` | 删除原子隐藏文档、取消摄取并创建清理任务。 |
| 同上 | `test_new_delete_request_for_deleted_document_is_rejected` | 已删除文档的新删除请求返回稳定错误。 |
| `test_generated_code.py` | `test_generated_python_is_in_sync_with_proto` | Python protobuf 生成物与 `.proto` 保持同步。 |
| `test_grpc_application_contract.py` | `test_open_rpc_methods_convert_application_results` | 已开放 RPC 正确转换 application 结果，上传摘要使用注入的 parser/chunk/model 配置。 |
| 同上 | `test_rpc_maps_domain_failures_and_keeps_future_methods_closed` | 领域错误映射正确，未来方法保持关闭。 |
| 同上 | `test_submit_document_rejects_data_before_header` | 上传流首帧必须为 header。 |
| 同上 | `test_open_methods_work_through_generated_grpc_transport` | 已开放方法可经生成的 gRPC transport 调用。 |
| `test_metadata_repository_contract.py` | `test_submit_atomically_creates_task_and_waiting_outbox_and_deduplicates` | 提交原子创建 Task/WAITING Outbox，并分别验证同 key 与同 fingerprint 去重。 |
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
| 同上 | `test_every_response_has_result_and_business_error_outcome` | 每个响应都有 result 或 BusinessError 的 oneof。 |
| 同上 | `test_upload_request_is_a_header_or_data_frame` | 上传请求只允许 header 或 data 帧。 |
| 同上 | `test_idempotency_context_is_only_used_by_commands` | 幂等上下文仅用于命令型 RPC。 |
| 同上 | `test_evidence_contains_provenance_and_stage_scores_but_no_answer` | Evidence 包含 provenance/阶段分数，不生成答案。 |
| 同上 | `test_business_error_has_stable_machine_readable_fields` | BusinessError 具有稳定的机器可读字段。 |
| `test_retry_job_contract.py` | `test_retry_creates_new_job_task_and_ready_outbox_without_reviving_original` | Retry 创建新 Job/Task/READY Outbox，不复活原 Job。 |
| 同上 | `test_retry_rejects_failure_without_final_object` | 无正式对象的失败不可 Retry。 |
| 同上 | `test_retry_enforces_user_retry_limit` | Retry 强制执行用户重试上限。 |
| `test_search_engine_contract.py` | `test_search_upsert_is_idempotent_and_dense_sparse_are_separate` | Search upsert 幂等，Dense 与 Sparse 候选分离，并共同遵守 Dataset/metadata 过滤。 |
| `test_task_queue_contract.py` | `test_queue_preserves_at_least_once_delivery_and_explicit_ack_nak` | Queue 保持至少一次投递、重复 publish 和显式 ACK/NAK。 |
| 同上 | `test_unacked_delivery_can_be_redelivered` | 未 ACK delivery 可重新投递。 |

## Integration 测试函数

Integration 测试直连真实中间件，验证 SDK、DDL 和服务端行为；显式选择该测试类型时，基础设施不可用必须失败。

| 文件 | 测试函数 | 职责 |
| --- | --- | --- |
| `test_elasticsearch_adapter.py` | `test_real_es_upsert_dense_bm25_isolation_and_metadata_filters` | 真实 ES 验证 Bulk 幂等、KNN/BM25 召回、稳定排序、Dataset 隔离和 metadata 过滤。 |
| 同上 | `test_real_es_version_and_document_delete_are_idempotent` | 真实 ES 按版本和整文档删除均可重复执行并收敛到正确记录数。 |
| `test_nats_jetstream_adapter.py` | `test_real_jetstream_preserves_duplicate_publish_and_ack_removes_deliveries` | 真实 JetStream 保留重复 task_id 消息，PubAck 后可消费，显式 ACK 后移除。 |
| 同上 | `test_real_jetstream_redelivers_after_ack_wait_and_honors_delayed_nak` | 真实 durable consumer 在 ACK 超时后重投，并遵守 NAK delay。 |
| 同上 | `test_real_jetstream_provisioning_is_idempotent_and_rejects_incompatible_consumer` | stream/consumer 同配置装配幂等，不兼容 consumer 参数 fail fast。 |
| `test_mysql_concurrency.py` | `test_concurrent_retry_keys_reuse_one_active_child_job` | 八个并发 Retry key 只创建并复用一个活跃子 Job/Task，重试计数只增加一次。 |
| 同上 | `test_concurrent_rebuilds_allocate_distinct_index_versions` | 四个并发重建在 Document 行锁下分配互不重复的 index version，旧 active version 保持可见。 |
| 同上 | `test_delete_and_finalizer_race_never_leaves_ingest_outbox_ready` | 删除与 Finalizer 并发时，摄取 Outbox 最终必为 CANCELLED，且仅删除清理 Outbox 可发布。 |
| `test_mysql_lifecycle.py` | `test_pending_cancel_is_immediate_idempotent_and_withdraws_outbox` | PENDING 取消原子终止 Job/Task、撤销未发布 Outbox，并支持同 key 幂等回放。 |
| 同上 | `test_running_cancel_converges_at_completion_without_activating_version` | RUNNING 取消先记录请求，再由完成 checkpoint 收敛为 CANCELLED、放弃索引版本并创建清理任务。 |
| 同上 | `test_delete_hides_immediately_cancels_ingest_and_cleanup_honors_generation` | 删除立即隐藏文档、阻断旧摄取完成，并由 generation 匹配的清理 Task 收敛终态。 |
| 同上 | `test_new_delete_key_for_deleted_document_is_rejected` | 已删除 Document 只允许原幂等 key 回放，新 key 返回稳定冲突。 |
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

## Functional 测试函数

Functional 测试负责验证跨层调用链。它们使用真实 gRPC、application、Outbox、Worker、pipeline 与 retrieval，只在基础设施 Port 上替换为 `tests/fakes/` 的确定性实现。

| 文件 | 测试函数 | 职责 |
| --- | --- | --- |
| `test_mock_upload_ingest_retrieve.py` | `test_mock_grpc_upload_async_ingest_and_dense_retrieve` | 通过 gRPC 完成上传、异步摄取、Job 查询和 Dense evidence 检索。 |
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
| `test_spec_invariant_matrix.py` | `test_every_spec_invariant_has_mock_evidence_and_explicit_real_validation_status` | 每项 SPEC 不变量都有 Mock 证据及真实复验状态。 |

## Eval 测试函数

Eval 测试负责防止检索排序和 evidence 定位质量回退。不得以 LLM 自由文本逐字 snapshot 取代这些指标。

| 文件 | 测试函数 | 职责 |
| --- | --- | --- |
| `test_retrieval_quality.py` | `test_fixed_thirty_question_quality_baseline` | 在固定 30 问集上验证 Recall@6、MRR@6 和 locator accuracy 门槛。 |

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
| `fixtures/reliability_matrix.json` | SPEC T1～T25 与 Mock 测试证据、真实复验状态的映射。 |
| `eval/fixtures/retrieval_quality.json` | 固定问题、相关 chunk 与 locator 的检索质量基线。 |

## 维护规则

1. 新增、删除、移动或重命名测试文件时，同步更新“目录树”。
2. 新增、删除、重命名 `test_*` 函数或显著改变其断言职责时，同步更新对应类型的函数表。
3. 修改 RPC、Port、状态机、Outbox、Worker、重试、取消或删除语义时，同时检查 Contract、Functional、Resilience 三类表是否仍准确。
4. 新增 marker、fixture 或 Fake port 时，在本文件和 [`../docs/testing-guide.md`](../docs/testing-guide.md) 中补充运行边界；Fake 不得进入生产 bootstrap。
5. 测试文档与测试代码必须在同一提交中评审；缺少本文件同步的测试改动不视为完成。
