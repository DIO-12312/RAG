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


# 按稳定的字符启发式估算文本 Token 数，用于判断 Evidence 是否能放入上下文预算。
def estimate_tokens(text: str) -> int:
    """Use a stable character heuristic without importing a model tokenizer SDK."""

    return (len(text) + 3) // 4


# 按既有优先级在 Token 预算内选择完整 Evidence，并记录因预算不足而省略的 Chunk。
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
