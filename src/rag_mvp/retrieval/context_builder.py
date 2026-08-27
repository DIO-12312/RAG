"""纯 evidence 预算选择：生成与传输协议无关的上下文计划，不截断单个证据。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rag_mvp.domain.models import Evidence


@dataclass(frozen=True, slots=True)
class ContextPlan:
    evidence: tuple[Evidence, ...]
    estimated_tokens: int
    omitted_chunk_ids: tuple[str, ...]


# 实现 estimate_tokens 对应的局部职责。
def estimate_tokens(text: str) -> int:
    """Use a stable character heuristic without importing a model tokenizer SDK."""

    return (len(text) + 3) // 4


# 构建该方法负责的领域数据或基础设施状态。
def build_context_plan(evidence: Sequence[Evidence], *, max_context_tokens: int) -> ContextPlan:
    if max_context_tokens < 1:
        raise ValueError("max_context_tokens must be at least 1")

    selected: list[Evidence] = []
    omitted: list[str] = []
    used = 0
    for item in evidence:
        cost = estimate_tokens(item.content_with_weight)
        if used + cost <= max_context_tokens:
            selected.append(item)
            used += cost
        else:
            omitted.append(item.chunk_id)
    return ContextPlan(tuple(selected), used, tuple(omitted))
