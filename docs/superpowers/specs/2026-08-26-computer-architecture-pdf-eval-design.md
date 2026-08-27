# 真实《计组复习》PDF 检索与答案评估测试集设计

日期：2026-08-26
状态：已批准

## 目标

基于真实用户文档 `tests/object/计组复习.pdf`（44 页，覆盖第 1/4/5/6/7 章）构建 50 条知识点的 JSON 测试集，并新增一个真实评估测试：提交真实 PDF 完整摄取并向量化，对 50 个问题逐个向量化检索，按阈值评估检索质量与答案包含度。

## 系统边界

本系统是**纯检索型 RAG**：`ports/model.py` 的 `ModelGateway` 只有 `embed` 与 `rerank`，没有生成能力。因此"答案准确性"以**答案包含度**衡量：检索出的 top-k 证据原文必须包含参考答案拆出的必含短语。

## 数据文件

路径：`tests/eval/fixtures/computer_architecture_knowledge.json`（50 条记录的 JSON 数组，UTF-8）

每条记录 schema：

```json
{
  "id": "arch-001",
  "chapter": "第一章 计算机系统概论",
  "query": "冯·诺依曼模型计算机采用什么原理？",
  "pages": [6],
  "answer": "存储程序原理：程序和数据预先放在存储器中，机器工作时自动按程序的逻辑顺序从存储器中逐条取出指令并执行。",
  "required_phrases": ["存储", "程序原理", "程序和数据", "存储器", "逐条取出指令", "执行"]
}
```

字段说明：

- `id`：`arch-001` ~ `arch-050`，全局唯一。
- `chapter`：来源章节，用于可读性与覆盖面统计。
- `query`：自然语言问句。
- `pages`：期望命中的 PDF 页码列表（基于 PdfParser 从 1 开始的分页）。
- `answer`：完整参考答案文本（取自 PDF 原文，允许轻微规范化）。
- `required_phrases`：从参考答案中拆出的关键名词、术语或事实短语，至少 2 条、不设固定上限；全部命中 top-k 证据原文才算"答案命中"。优先拆分为可独立核对的细粒度短语，而不是用一条长句覆盖整个答案。

## 生成规则

由实现者依据已抽取的 PDF 全文撰写，每条锚定具体页码：

| 章节 | 页码范围 | 条数 |
|------|----------|------|
| 第一章 计算机系统概论 | 1, 5-7 | 7 |
| 第四章 指令系统 | 8-12 | 9 |
| 第五章 中央处理器 | 13-27 | 13 |
| 第六章 总线 | 27-32 | 8 |
| 第七章 输入/输出系统 | 33-44 | 13 |

- `pages` 取该知识点实际所在页；跨页知识点可给多个页码（如 DMA 三种传送方式 → [42, 43]）。
- `required_phrases` 必须拆出答案中的重要名词、术语和事实动作，并且都是 PDF 原文子串（去除多余空白后可被证据文本包含），避免拼写/翻译漂移。
- 质量检查：每条 `required_phrases` 必须能被 `answer` 与对应页原文覆盖；不允许含糊问句（如"什么是寻址方式"）。

## 评估测试

路径：`tests/eval/test_real_computer_architecture_pdf_quality.py`

标记：`@pytest.mark.eval` + `@pytest.mark.e2e` + `@pytest.mark.asyncio`

复用 `tests/e2e/conftest.py`：`create_dataset` / `submit_document` / `wait_for_job` / `retrieve` / `embedding_runtime` / `unique_id`。

流程：

1. `embedding_runtime` fixture 校验真实模型名与维度（缺失则 fail）。
2. 解析 PDF 路径：复用 e2e 逻辑（`RAG_E2E_PDF_PATH` 环境变量优先，否则 `tests/object/计组复习.pdf`；文件不存在则 skip）。
3. `create_dataset` 创建绑定真实 embedding 模型的 dataset（dense_top_k=20, sparse_top_k=20, rrf_k=60, max_context_tokens=4000）。
4. `submit_document` 上传 PDF，`wait_for_job` 等待摄取完成——此即"完整摄取 + 一次向量化"。
5. 读取 JSON 数组，对每条 case：
   - `retrieve(query, top_k=6)` —— 每次查询都做 query 向量化 + 混合检索。
   - 收集 top-6 证据；每条证据携带 `locator.page_number`（PDF 按页解析，分块后同页 chunk 的 `page_number` 相同），按页码匹配，不依赖 `chunk_id` 精确匹配：
     - `hits` = 证据中 `locator.page_number ∈ case.pages` 的 chunk。
     - `recall@6`：`hits` 非空。
     - `mrr@6`：第一个 `hits` 排名的倒数；无命中为 0。
     - `top1_page_hit`：top-1 证据的 `page_number ∈ case.pages`（比全局 recall 更严，反映最相关结果是否落在期望页）。
     - `answer_coverage`：拼接 top-6 证据原文，`case.required_phrases` 全部包含。
6. 聚合 50 条指标并断言阈值。

无论摄取、查询或指标断言是否失败，测试均在 `finally` 中调用 `DeleteDataset` 并等待该 Dataset 被物理 purge。日志在 teardown 前写入；删除失败也必须使测试失败，不能让一次性 eval 数据滞留在 MySQL、Elasticsearch 或对象存储。

每次真实评测在 `tests/eval/log/` 生成带时间戳的 JSON 日志，记录 50 条 query 的完整测试端 embedding、服务实际返回的 Top-K evidence（含 rank、chunk、页码、各阶段分数和原文）以及聚合指标。日志目录只读挂载之外单独以可写 bind mount 提供给 `rag-test`，生成物不进入 Git。

指标在测试内计算，**不改** `src/rag_mvp/retrieval/evaluation.py`。

## 阈值

初始阈值（保守，**首次真实跑后校准**）：

- `recall@6 >= 0.80`
- `mrr@6 >= 0.65`
- `top1_page_hit >= 0.60`
- `answer_coverage >= 0.70`

校准方式：在真实模型 + Docker 拓扑下跑 `make docker-test SUITE=eval`，根据实际指标调阈值，确保不 flaky 且不放水。校准后的最终值写回测试与本文档。

## 范围

新增文件：

- `tests/eval/fixtures/computer_architecture_knowledge.json`
- `tests/eval/test_real_computer_architecture_pdf_quality.py`

修改文件：

- `Earthfile`：`eval` suite 同时执行既有 30 问评测和新增 50 问 PDF 评测；本地 PDF 缺失时新增测试按既有约定 skip。
- `tests/contract/test_build_entrypoints.py`：约束公开 eval suite 必须包含两个真实评测文件。
- `tests/TEST.md`、`docs/testing-guide.md`：同步测试目录、职责和公开运行边界。
- `.gitignore`：精确忽略本地真实 PDF。

不改动：`evaluation.py`、Makefile。`tests/e2e/conftest.py` 仅新增 generated-gRPC-only 的 Dataset 删除与 purge 轮询 helper；PDF 文件 `tests/object/计组复习.pdf` 保持 untracked（个人资料，不进 CI）。

## 风险

- 需真实 embedding 模型 + Docker 才能运行；本地无模型时无法验证通过/校准阈值。
- `required_phrases` 依赖 PDF 原文子串，若解析器分块切断了短语，需用跨 chunk 拼接或调整短语。
