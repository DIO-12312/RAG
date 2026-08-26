# 真实《计组复习》PDF 五十问评估 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于本地真实 `计组复习.pdf` 建立 50 条可追溯知识点 fixture，并通过真实 gRPC、MySQL、Elasticsearch、NATS 和 Embedding 模型评估 Recall@6、MRR@6、Top-1 页命中率与答案包含度。

**Architecture:** 测试数据以 UTF-8 JSON 数组保存，每条记录绑定 PdfParser 的 1-based 页码与从答案拆出的细粒度关键名词、术语和事实短语。真实评估测试只复用 `tests/e2e/conftest.py` 的 generated gRPC helpers：一次上传并完整摄取 PDF，随后逐题调用 `Retrieve(top_k=6)`，在测试文件内计算四项聚合指标，不修改生产检索评估代码；Earthfile 的公开 eval suite 同时运行既有 30 问和新增 50 问评测。

**Tech Stack:** Python 3.12、pytest、pytest-asyncio、generated gRPC client、MySQL 8、Elasticsearch 8、NATS JetStream、OpenAI-compatible Embedding、JSON、现有 `PdfParser`

**Spec:** `docs/superpowers/specs/2026-08-26-computer-architecture-pdf-eval-design.md`

## Global Constraints

- 源文档固定为 44 页真实用户文件；优先读取 `RAG_E2E_PDF_PATH`，否则读取 `tests/object/计组复习.pdf`。
- `tests/object/计组复习.pdf` 是个人资料，必须保持 untracked，不得加入 Git 暂存区或提交。
- JSON 固定为 UTF-8 数组、包含 50 条记录；ID 必须恰好为 `arch-001`～`arch-050` 且全局唯一。
- 每条记录只包含 `id`、`chapter`、`query`、`pages`、`answer`、`required_phrases` 六个字段。
- 每条 `required_phrases` 至少有 2 项、不设固定上限；按答案语义拆出重要名词、术语和事实动作，每项经空白规范化后同时是 `answer` 和对应 PDF 页原文的子串。
- 章节分布固定为第一章 7 条、第四章 9 条、第五章 13 条、第六章 8 条、第七章 13 条。
- 测试固定请求 top-6；页码相关性只以 `locator.page_number in case.pages` 判断，不依赖 `chunk_id`。
- 初始阈值固定为 `recall@6 >= 0.80`、`mrr@6 >= 0.65`、`top1_page_hit >= 0.60`、`answer_coverage >= 0.70`。
- 不修改 `src/rag_mvp/retrieval/evaluation.py`、`tests/e2e/conftest.py`、Makefile 或其他现有测试文件；允许按本计划修改 Earthfile 与对应构建入口契约测试。
- 新增测试文件、测试函数和 fixture 时，必须在同一测试提交中同步更新 `tests/TEST.md` 的目录树、测试职责表、运行边界和 fixture 职责表。
- 每个任务提交前检查 `git status`，只暂存本任务拥有的文件；保留用户对设计 SPEC 的现有未提交修改。
- 每个任务完成后更新本计划对应复选框，并使用 Conventional Commits 创建独立提交；不得 `git push`。

## Confirmed Execution Decisions

1. `required_phrases` 以设计 SPEC 的 JSON 实例为准：把答案拆成多个可独立核对的重要名词、术语和事实动作，至少 2 项、不设固定上限。
2. 修改 Earthfile 的 `run_eval()`，让 `make docker-test SUITE=eval` 同时运行既有 `test_real_retrieval_quality.py` 和新增 `test_real_computer_architecture_pdf_quality.py`。
3. 真实 PDF 已由用户放入 `tests/object/计组复习.pdf`；`.gitignore` 使用精确规则保护该个人资料，测试镜像继续通过既有 `./tests:/app/tests:ro` mount 读取它。

---

## File Structure

| 文件 | 操作 | 单一职责 |
|---|---|---|
| `tests/eval/fixtures/computer_architecture_knowledge.json` | Create | 保存 50 条按章节、页码、答案和必含短语锚定的真实 PDF 知识点 |
| `tests/eval/test_real_computer_architecture_pdf_quality.py` | Create | 加载并校验 JSON，一次摄取 PDF，执行 50 次真实检索并聚合四项质量指标 |
| `tests/TEST.md` | Modify | 登记新增 eval 文件、测试函数、fixture、真实运行命令和本地 PDF 边界 |
| `Earthfile` | Modify | 让公开 `eval` suite 同时收集既有 30 问和新增 50 问真实评测 |
| `tests/contract/test_build_entrypoints.py` | Modify | 契约化 eval suite 的两个真实测试入口及 PDF 缺失时的 pytest skip 语义 |
| `docs/testing-guide.md` | Modify | 更新 Real Eval 职责、定位命令、调用成本和本地 PDF 边界 |
| `.gitignore` | Modify | 精确忽略 `tests/object/计组复习.pdf`，防止个人资料进入 Git |
| `docs/superpowers/specs/2026-08-26-computer-architecture-pdf-eval-design.md` | Conditional Modify | 仅当真实校准改变初始阈值时，同步记录最终阈值与校准结果；其余内容保持用户版本 |
| `docs/superpowers/plans/2026-08-26-computer-architecture-pdf-eval.md` | Modify | 执行时勾选步骤并记录实际验证结果和提交 hash |

---

### Task 1: 五十问真实 PDF 知识点 Fixture

**Files:**
- Create: `tests/eval/fixtures/computer_architecture_knowledge.json`
- Modify: `docs/superpowers/plans/2026-08-26-computer-architecture-pdf-eval.md`

**Interfaces:**
- Consumes: `PdfParser.parse(source_name: str, content: bytes) -> tuple[ParsedSegment, ...]`；每个 `ParsedSegment.locator.page_number` 是从 1 开始的 PDF 页码。
- Produces: 50 条 JSON 记录；Task 2 的 `_load_cases(FIXTURE_PATH) -> tuple[KnowledgeCase, ...]` 直接消费六个固定字段。

- [x] **Step 1: 确认真实 PDF 前置条件且不触碰 Git 跟踪状态**

运行：

```bash
test -f tests/object/计组复习.pdf
git check-ignore -v tests/object/计组复习.pdf
git status --short
```

Expected：第一条命令成功；第二条显示该 PDF 命中忽略规则；`git status --short` 只显示用户已修改的设计 SPEC 和本计划，不显示 PDF。若第一条失败，停止 Task 1，等待用户提供源文件；不得根据常识补写题库。

- [x] **Step 2: 使用生产 PdfParser 抽取逐页原文**

运行以下只读脚本，确保 fixture 使用的文本与真实摄取路径一致：

```bash
uv run python - <<'PY'
import asyncio
from pathlib import Path

from rag_mvp.adapters.parsers.pdf import PdfParser


async def main() -> None:
    source = Path("tests/object/计组复习.pdf")
    segments = await PdfParser().parse(source.name, source.read_bytes())
    pages = {segment.locator.page_number: segment.text for segment in segments}
    assert set(pages) == set(range(1, 45)), sorted(pages)
    for page_number in range(1, 45):
        print(f"\n===== PAGE {page_number} =====\n{pages[page_number]}")


asyncio.run(main())
PY
```

Expected：输出恰好覆盖 `PAGE 1`～`PAGE 44`，没有空页或 `INVALID_PDF`。若生产 Parser 跳过空页，先核对实际文件是否与设计 SPEC 所述 44 页文本型 PDF 相同，不修改 Parser 来迁就测试数据。

- [x] **Step 3: 按固定 ID 与章节配额撰写 50 条 JSON 记录**

使用 `apply_patch` 创建 `tests/eval/fixtures/computer_architecture_knowledge.json`。逐条从 Step 2 的页原文选取明确知识点，分配必须严格遵循：

| ID 范围 | chapter 精确值 | 允许页码 | 行数 |
|---|---|---:|---:|
| `arch-001`～`arch-007` | `第一章 计算机系统概论` | `1, 5, 6, 7` | 7 |
| `arch-008`～`arch-016` | `第四章 指令系统` | `8`～`12` | 9 |
| `arch-017`～`arch-029` | `第五章 中央处理器` | `13`～`27` | 13 |
| `arch-030`～`arch-037` | `第六章 总线` | `27`～`32` | 8 |
| `arch-038`～`arch-050` | `第七章 输入/输出系统` | `33`～`44` | 13 |

每行写成单行 JSON object，并遵守以下可执行规则：

- `query` 必须点明待回答概念，不能使用“它是什么”“什么是寻址方式”之类缺少限定词的问题。
- `pages` 只列答案实际出现的页；跨页答案列出全部必要页，例如 DMA 三种传送方式使用 `[42, 43]`。
- `answer` 以对应页原文为基础，可统一空白和标点，但不得引入 PDF 未出现的事实。
- `required_phrases` 按答案语义拆出互补的重要名词、术语和事实动作，至少 2 项、不设固定上限；避免只选问题本身已经出现的术语，且短语不得跨越不存在于原文的改写边界。
- JSON 使用正常 ASCII 双引号，禁止弯引号和未闭合字符串；每行末尾不加逗号。

- [x] **Step 4: 运行独立 fixture 结构与来源校验**

运行：

```bash
uv run python - <<'PY'
import asyncio
import json
from collections import Counter
from pathlib import Path

from rag_mvp.adapters.parsers.pdf import PdfParser


FIXTURE = Path("tests/eval/fixtures/computer_architecture_knowledge.json")
PDF = Path("tests/object/计组复习.pdf")
EXPECTED_COUNTS = {
    "第一章 计算机系统概论": 7,
    "第四章 指令系统": 9,
    "第五章 中央处理器": 13,
    "第六章 总线": 8,
    "第七章 输入/输出系统": 13,
}
EXPECTED_PAGES = {
    "第一章 计算机系统概论": {1, 5, 6, 7},
    "第四章 指令系统": set(range(8, 13)),
    "第五章 中央处理器": set(range(13, 28)),
    "第六章 总线": set(range(27, 33)),
    "第七章 输入/输出系统": set(range(33, 45)),
}


def compact(value: str) -> str:
    return "".join(value.split())


async def main() -> None:
    rows = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 50
    assert [row["id"] for row in rows] == [f"arch-{index:03d}" for index in range(1, 51)]
    assert Counter(row["chapter"] for row in rows) == Counter(EXPECTED_COUNTS)
    parsed = await PdfParser().parse(PDF.name, PDF.read_bytes())
    page_text = {segment.locator.page_number: compact(segment.text) for segment in parsed}
    for row in rows:
        assert set(row) == {"id", "chapter", "query", "pages", "answer", "required_phrases"}
        assert row["query"].strip() and row["answer"].strip()
        assert row["pages"] == sorted(set(row["pages"]))
        assert set(row["pages"]) <= EXPECTED_PAGES[row["chapter"]]
        assert len(row["required_phrases"]) >= 2
        answer = compact(row["answer"])
        source = "".join(page_text[page] for page in row["pages"])
        for phrase in row["required_phrases"]:
            assert compact(phrase) in answer, (row["id"], phrase, "answer")
            assert compact(phrase) in source, (row["id"], phrase, "source")


asyncio.run(main())
PY
```

Expected：exit code 0，无 assertion；任何失败都只修正对应 JSON 记录，使短语与真实页原文一致，不放宽校验规则。

- [x] **Step 5: 检查 fixture diff 和敏感文件边界**

运行：

```bash
git diff --check
git status --short
git diff -- tests/eval/fixtures/computer_architecture_knowledge.json
```

Expected：JSON 数组恰好包含 50 条记录；`git status` 不包含 `tests/object/计组复习.pdf`；没有 `.env`、API Key、日志、缓存或对象数据。

- [x] **Step 6: 提交五十问 fixture 模块**

```bash
git add tests/eval/fixtures/computer_architecture_knowledge.json docs/superpowers/plans/2026-08-26-computer-architecture-pdf-eval.md
git diff --cached --check
git diff --cached --name-only
git commit -m "test(eval): 新增计组 PDF 五十问基准数据"
```

Expected：暂存区只包含 fixture 与本计划；提交后记录 commit hash 和 Step 4 的实际验证结果。用户正在编辑的设计 SPEC 不得被暂存。

---

### Task 2: 真实 PDF 检索与答案包含度质量门禁

**Files:**
- Create: `tests/eval/test_real_computer_architecture_pdf_quality.py`
- Modify: `tests/TEST.md`
- Conditional Modify: `docs/superpowers/specs/2026-08-26-computer-architecture-pdf-eval-design.md`
- Modify: `docs/superpowers/plans/2026-08-26-computer-architecture-pdf-eval.md`

**Interfaces:**
- Consumes: Task 1 的 JSON；`tests.e2e.conftest.EmbeddingRuntime`、`create_dataset(stub, runtime, case_name) -> str`、`submit_document(stub, dataset_id, source) -> tuple[str, str]`、`wait_for_job(stub, job_id, deadline_seconds=240) -> JobResult`、`retrieve(stub, dataset_id, query) -> RetrieveResult`。
- Produces: `_load_cases(path: Path) -> tuple[KnowledgeCase, ...]`、`QualityMetrics(recall_at_6, mrr_at_6, top1_page_hit, answer_coverage)` 和 `test_real_computer_architecture_pdf_quality()`；不产生生产 API。

- [x] **Step 1: 先确认新测试尚不存在**

运行：

```bash
uv run pytest tests/eval/test_real_computer_architecture_pdf_quality.py -q
```

Expected：FAIL，pytest 明确报告测试文件不存在。这确认后续新增文件确实提供新的门禁，而不是覆盖既有测试。

- [x] **Step 2: 写入完整 JSON loader、指标计算和真实评估测试**

使用 `apply_patch` 创建 `tests/eval/test_real_computer_architecture_pdf_quality.py`，内容如下：

```python
"""Real 50-question quality gate for the local computer-architecture PDF."""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.conftest import (
    EmbeddingRuntime,
    create_dataset,
    retrieve,
    submit_document,
    wait_for_job,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "computer_architecture_knowledge.json"
LOCAL_PDF_ENV = "RAG_E2E_PDF_PATH"
DEFAULT_LOCAL_PDF = Path(__file__).resolve().parents[1] / "object" / "计组复习.pdf"
EXPECTED_CHAPTER_COUNTS = {
    "第一章 计算机系统概论": 7,
    "第四章 指令系统": 9,
    "第五章 中央处理器": 13,
    "第六章 总线": 8,
    "第七章 输入/输出系统": 13,
}
EXPECTED_CHAPTER_PAGES = {
    "第一章 计算机系统概论": frozenset({1, 5, 6, 7}),
    "第四章 指令系统": frozenset(range(8, 13)),
    "第五章 中央处理器": frozenset(range(13, 28)),
    "第六章 总线": frozenset(range(27, 33)),
    "第七章 输入/输出系统": frozenset(range(33, 45)),
}
RECALL_AT_6_THRESHOLD = 0.80
MRR_AT_6_THRESHOLD = 0.65
TOP1_PAGE_HIT_THRESHOLD = 0.60
ANSWER_COVERAGE_THRESHOLD = 0.70


@dataclass(frozen=True, slots=True)
class KnowledgeCase:
    id: str
    chapter: str
    query: str
    pages: tuple[int, ...]
    answer: str
    required_phrases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    case_id: str
    hit: bool
    reciprocal_rank: float
    top1_page_hit: bool
    answer_covered: bool
    retrieved_pages: tuple[int | None, ...]
    missing_phrases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QualityMetrics:
    recall_at_6: float
    mrr_at_6: float
    top1_page_hit: float
    answer_coverage: float


def _compact_text(value: str) -> str:
    return "".join(value.split())


def _load_cases(path: Path) -> tuple[KnowledgeCase, ...]:
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 50
    cases: list[KnowledgeCase] = []
    for line_number, raw_line in enumerate(raw_lines, start=1):
        payload = json.loads(raw_line)
        assert set(payload) == {
            "id",
            "chapter",
            "query",
            "pages",
            "answer",
            "required_phrases",
        }, line_number
        case = KnowledgeCase(
            id=str(payload["id"]),
            chapter=str(payload["chapter"]),
            query=str(payload["query"]),
            pages=tuple(int(page) for page in payload["pages"]),
            answer=str(payload["answer"]),
            required_phrases=tuple(str(phrase) for phrase in payload["required_phrases"]),
        )
        assert case.query.strip() and case.answer.strip(), case.id
        assert case.pages == tuple(sorted(set(case.pages))), case.id
        assert case.chapter in EXPECTED_CHAPTER_PAGES, case.id
        assert set(case.pages) <= EXPECTED_CHAPTER_PAGES[case.chapter], case.id
        assert len(case.required_phrases) >= 2, case.id
        compact_answer = _compact_text(case.answer)
        assert all(_compact_text(phrase) in compact_answer for phrase in case.required_phrases), (
            case.id
        )
        cases.append(case)

    assert [case.id for case in cases] == [f"arch-{index:03d}" for index in range(1, 51)]
    assert Counter(case.chapter for case in cases) == Counter(EXPECTED_CHAPTER_COUNTS)
    return tuple(cases)


@pytest.fixture
def computer_architecture_pdf() -> Path:
    configured_path = os.getenv(LOCAL_PDF_ENV, "").strip()
    source = Path(configured_path).expanduser() if configured_path else DEFAULT_LOCAL_PDF
    if not source.is_file():
        pytest.skip(
            "local real-user PDF is unavailable; place it at "
            f"{DEFAULT_LOCAL_PDF} or set {LOCAL_PDF_ENV} to a container-visible path"
        )
    return source


def _evaluate_case(case: KnowledgeCase, result: Any, document_id: str) -> CaseOutcome:
    evidence = tuple(
        item for item in result.evidence[:6] if str(item.document_id) == document_id
    )
    retrieved_pages = tuple(item.locator.page_number for item in evidence)
    relevant_pages = set(case.pages)
    first_relevant_rank = next(
        (
            rank
            for rank, page_number in enumerate(retrieved_pages, start=1)
            if page_number in relevant_pages
        ),
        None,
    )
    context = _compact_text("".join(item.content_with_weight for item in evidence))
    missing_phrases = tuple(
        phrase for phrase in case.required_phrases if _compact_text(phrase) not in context
    )
    return CaseOutcome(
        case_id=case.id,
        hit=first_relevant_rank is not None,
        reciprocal_rank=0.0 if first_relevant_rank is None else 1.0 / first_relevant_rank,
        top1_page_hit=bool(retrieved_pages and retrieved_pages[0] in relevant_pages),
        answer_covered=not missing_phrases,
        retrieved_pages=retrieved_pages,
        missing_phrases=missing_phrases,
    )


def _aggregate(outcomes: tuple[CaseOutcome, ...]) -> QualityMetrics:
    count = len(outcomes)
    assert count == 50
    return QualityMetrics(
        recall_at_6=sum(outcome.hit for outcome in outcomes) / count,
        mrr_at_6=sum(outcome.reciprocal_rank for outcome in outcomes) / count,
        top1_page_hit=sum(outcome.top1_page_hit for outcome in outcomes) / count,
        answer_coverage=sum(outcome.answer_covered for outcome in outcomes) / count,
    )


@pytest.mark.eval
@pytest.mark.e2e
@pytest.mark.asyncio
async def test_real_computer_architecture_pdf_quality(
    computer_architecture_pdf: Path,
    rag_stub: object,
    embedding_runtime: EmbeddingRuntime,
) -> None:
    cases = _load_cases(FIXTURE_PATH)
    dataset_id = await create_dataset(
        rag_stub,
        embedding_runtime,
        "real-computer-architecture-pdf-50",
    )
    document_id, job_id = await submit_document(
        rag_stub,
        dataset_id,
        computer_architecture_pdf,
    )
    await wait_for_job(rag_stub, job_id, deadline_seconds=600)

    outcomes: list[CaseOutcome] = []
    for case in cases:
        result = await retrieve(rag_stub, dataset_id, case.query)
        outcomes.append(_evaluate_case(case, result, document_id))

    frozen_outcomes = tuple(outcomes)
    metrics = _aggregate(frozen_outcomes)
    diagnostics = [
        (
            f"{outcome.case_id}: pages={outcome.retrieved_pages!r} "
            f"hit={outcome.hit} rr={outcome.reciprocal_rank:.3f} "
            f"top1={outcome.top1_page_hit} missing={outcome.missing_phrases!r}"
        )
        for outcome in frozen_outcomes
        if not (
            outcome.hit
            and outcome.top1_page_hit
            and outcome.answer_covered
            and outcome.reciprocal_rank == 1.0
        )
    ]
    summary = (
        f"recall@6={metrics.recall_at_6:.3f} mrr@6={metrics.mrr_at_6:.3f} "
        f"top1_page_hit={metrics.top1_page_hit:.3f} "
        f"answer_coverage={metrics.answer_coverage:.3f}"
    )
    print(summary)
    failures = []
    if metrics.recall_at_6 < RECALL_AT_6_THRESHOLD:
        failures.append(f"recall@6 < {RECALL_AT_6_THRESHOLD:.2f}")
    if metrics.mrr_at_6 < MRR_AT_6_THRESHOLD:
        failures.append(f"mrr@6 < {MRR_AT_6_THRESHOLD:.2f}")
    if metrics.top1_page_hit < TOP1_PAGE_HIT_THRESHOLD:
        failures.append(f"top1_page_hit < {TOP1_PAGE_HIT_THRESHOLD:.2f}")
    if metrics.answer_coverage < ANSWER_COVERAGE_THRESHOLD:
        failures.append(f"answer_coverage < {ANSWER_COVERAGE_THRESHOLD:.2f}")
    assert not failures, "\n".join([summary, *failures, *diagnostics])
```

- [x] **Step 3: 运行静态检查和离线边界检查**

运行：

```bash
uv run ruff check tests/eval/test_real_computer_architecture_pdf_quality.py
uv run ruff format --check tests/eval/test_real_computer_architecture_pdf_quality.py
uv run pytest -m "eval and not e2e" tests/eval -q
```

Expected：Ruff 两项通过；离线 eval 继续通过，新增真实测试因同时带 `e2e` marker 被 deselect，不读取 PDF、不调用模型。

- [x] **Step 4: 在真实 Docker 拓扑运行新增测试并记录初始指标**

先确保 `.env` 中配置了真实 embedding endpoint、模型名、API Key 和维度；然后运行：

```bash
make docker-up
docker compose --profile test run --rm rag-test uv run pytest \
  -m "eval and e2e" \
  tests/eval/test_real_computer_architecture_pdf_quality.py -q -s
```

Expected：完成一次 PDF 摄取、50 次 query embedding 与混合检索；终端打印四项指标。测试达到初始阈值时直接保留 SPEC 阈值，不为追求更高数字收紧到贴近单次观测值。Task 3 完成后，最终公开验收改用 `make docker-test SUITE=eval`。

若任一指标低于初始阈值，按以下顺序处理，不得直接降低门槛：

1. 根据 assertion diagnostics 核对失败 case 的期望页码、答案和短语是否与 PdfParser 原文一致；fixture 错误只修正该行并重跑 Task 1 Step 4。
2. 检查 top-6 是否来自目标 `document_id`、locator 是否保留页码、短语是否仅因 chunk 边界或空白规范化未命中；修复测试聚合错误，但不得改生产检索算法来迎合评测。
3. fixture 与计算均正确后，连续运行同一命令 3 次，记录每次四项指标。只有稳定低于初始阈值的指标才允许校准；最终阈值取三次最小观测值向下保留两位小数，并额外预留 `0.02` 波动空间，同时在设计 SPEC 的“阈值”段写入最终数值、三次观测值、模型名和日期。未低于初始阈值的指标保持原值。

- [x] **Step 5: 同步登记 `tests/TEST.md`**

使用 `apply_patch` 完成四处精确更新：

1. 在 eval 目录树的 `fixtures/` 下加入 `computer_architecture_knowledge.json`，并加入 `test_real_computer_architecture_pdf_quality.py`。
2. 将 Eval 层职责扩展为“固定 30 问算法基线 + 本地真实 PDF 五十问页码/答案包含度门禁”，并注明新增测试的直接 Docker 命令；不要声称现有 `make docker-test SUITE=eval` 已包含它。
3. 在“Eval 测试函数”表加入 `test_real_computer_architecture_pdf_quality.py` / `test_real_computer_architecture_pdf_quality`，职责写明一次完整 PDF 摄取、50 次 query embedding、Recall@6、MRR@6、Top-1 页命中率和答案包含度。
4. 在“Fake 与 Fixture 的职责”表加入 `eval/fixtures/computer_architecture_knowledge.json`，说明 50 条记录按 PdfParser 页码锚定，源 PDF 保持本地且不进 Git。

- [x] **Step 6: 运行最终离线与真实验证**

运行：

```bash
make ci
docker compose --profile test run --rm rag-test uv run pytest \
  -m "eval and e2e" \
  tests/eval/test_real_computer_architecture_pdf_quality.py -q -s
git diff --check
```

Expected：`make ci` 通过且不访问真实模型；新增真实测试通过并打印最终指标；`git diff --check` 无错误。若 Earthly 不可用，必须明确记录 `make ci` 未运行，并至少执行 Step 3 的底层离线检查；不得据此声称完整离线门禁通过。

完成诊断后安全停止服务：

```bash
make docker-down
```

Expected：服务停止但命名卷保留；禁止执行 `docker compose down -v`。

- [x] **Step 7: 检查最终改动范围和个人资料隔离**

运行：

```bash
git status --short
git diff -- tests/eval/test_real_computer_architecture_pdf_quality.py tests/TEST.md
git diff -- docs/superpowers/specs/2026-08-26-computer-architecture-pdf-eval-design.md
git check-ignore -v tests/object/计组复习.pdf
```

Expected：本任务拥有的改动只有新测试、`tests/TEST.md`、本计划，以及仅在阈值确实校准时产生的设计 SPEC 阈值更新；PDF、`.env`、API Key、日志、缓存和 `data/` 均不出现在待提交文件中。

- [x] **Step 8: 提交真实评估模块**

若阈值未改变：

```bash
git add tests/eval/test_real_computer_architecture_pdf_quality.py tests/TEST.md docs/superpowers/plans/2026-08-26-computer-architecture-pdf-eval.md
```

若阈值经过 Step 4 校准，再额外暂存设计 SPEC：

```bash
git add docs/superpowers/specs/2026-08-26-computer-architecture-pdf-eval-design.md
```

检查并提交：

```bash
git diff --cached --check
git diff --cached --name-only
git commit -m "test(eval): 增加计组 PDF 五十问真实评测"
```

Expected：提交只表达真实 PDF 质量门禁及其必要测试文档/阈值记录；交接中报告 commit hash、实际四项指标、实际运行命令、未运行项，以及 `make docker-test SUITE=eval` 尚未包含新增测试的已知限制。

---

### Task 3: Earthfile 公开 Eval Suite 集成

**Files:**
- Modify: `tests/contract/test_build_entrypoints.py`
- Modify: `Earthfile`
- Modify: `tests/TEST.md`
- Modify: `docs/testing-guide.md`
- Modify: `docs/superpowers/plans/2026-08-26-computer-architecture-pdf-eval.md`

**Interfaces:**
- Consumes: Task 2 新增的 `tests/eval/test_real_computer_architecture_pdf_quality.py` 与既有 `tests/eval/test_real_retrieval_quality.py`。
- Produces: `make docker-test SUITE=eval` 在同一真实 Compose 拓扑依次收集两个 eval 文件；新增 PDF 缺失时 pytest 将该用例标为 skip，旧 30 问仍必须运行。

- [x] **Step 1: RED——扩展构建入口契约**

在 `test_docker_entrypoints_validate_suites_scan_logs_and_preserve_volumes()` 中增加行为断言：Earthfile 必须包含两个真实 eval 测试文件，且 eval pytest 参数不能再只指向旧文件。运行：

```bash
uv run pytest tests/contract/test_build_entrypoints.py::test_docker_entrypoints_validate_suites_scan_logs_and_preserve_volumes -q
```

Expected：FAIL，指出 `test_real_computer_architecture_pdf_quality.py` 尚未出现在 Earthfile。

- [x] **Step 2: GREEN——扩展 Earthfile 的 `run_eval()`**

将 `run_eval()` 的 pytest 路径改为同时列出两个测试文件：

```Earthfile
run_eval() { docker compose --profile test run --rm rag-test uv run pytest -m eval tests/eval/test_real_retrieval_quality.py tests/eval/test_real_computer_architecture_pdf_quality.py -q; };
```

不改变 suite 枚举、Compose 启动、Secret 传递、失败现场保留或 `all` 的执行顺序。

- [x] **Step 3: 验证构建入口契约转绿**

```bash
uv run pytest tests/contract/test_build_entrypoints.py -q
```

Expected：全部通过，且新增断言证明公开 eval suite 收集两个真实评测文件。

- [x] **Step 4: 同步测试运行文档**

在 `tests/TEST.md` 的 Eval 运行边界和 `docs/testing-guide.md` 的 Real Eval、单项定位命令、真实测试注意事项中明确：公开 eval suite 包含固定 30 问与本地 PDF 50 问；PDF 缺失时只 skip 50 问；存在时会额外产生一次完整文档 embedding 和 50 次 query embedding。

- [x] **Step 5: 运行构建入口必跑检查与公开真实 suite**

```bash
uv run pytest tests/contract/test_build_entrypoints.py -q
make ci
make docker-test SUITE=eval
make docker-down
```

Expected：契约测试、离线门禁和两个真实 eval 文件全部通过；最后安全停止容器并保留持久卷。任何命令未运行或失败必须如实记录。

- [x] **Step 6: 提交公开 Eval Suite 模块**

```bash
git add Earthfile tests/contract/test_build_entrypoints.py tests/TEST.md docs/testing-guide.md docs/superpowers/plans/2026-08-26-computer-architecture-pdf-eval.md
git diff --cached --check
git diff --cached --name-only
git commit -m "build(eval): 纳入计组 PDF 五十问评测"
```

Expected：提交不包含 PDF、`.env`、Secret、日志或数据卷。

---

## Completion Criteria

- JSON 数组恰好 50 条记录，ID、章节配额、页码范围、字段集合和细粒度关键短语约束全部通过校验。
- 每个必含短语经空白压缩后同时存在于参考答案和指定 PDF 页原文。
- 新测试只通过 generated gRPC helper 驱动真实服务；PDF 只摄取一次，50 个 query 各检索一次 top-6。
- Recall@6、MRR@6、Top-1 页命中率和答案包含度使用 50 条 case 聚合，失败信息能定位 case、返回页码和缺失短语。
- 初始阈值全部达到，或按三次真实观测完成保守校准并同步设计 SPEC。
- `tests/TEST.md` 的目录树、Eval 职责、测试函数表和 fixture 表全部同步。
- 未修改生产代码、`evaluation.py`、E2E conftest、Makefile 或其他既有测试；Earthfile 只扩展 eval suite 的测试文件列表。
- `make docker-test SUITE=eval` 同时收集既有 30 问评测与新增 50 问 PDF 评测。
- `tests/object/计组复习.pdf`、`.env`、API Key、日志、缓存和数据卷没有进入 Git。
- 已如实记录离线门禁、真实 Docker 测试和未运行项；未因 PDF 缺失或测试 skip 声称真实评估通过。
