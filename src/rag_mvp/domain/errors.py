"""稳定的领域失败表达；与 RPC 协议和基础设施异常类型解耦。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DomainFailure:
    code: str
    message: str
    retryable: bool = False

    # 拒绝没有机器可读错误码的领域失败。
    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("failure code must not be empty")


class DomainError(Exception):
    """Base exception carrying a stable machine-readable failure."""

    # 用领域失败的消息初始化异常，并保留失败对象供调用方处理。
    def __init__(self, failure: DomainFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


class InvalidStateTransition(DomainError):
    """Raised when a state machine transition violates a terminal fence."""

    # 将非法状态迁移转换为带固定错误码的领域异常。
    def __init__(self, current: object, target: object) -> None:
        super().__init__(
            DomainFailure(
                code="INVALID_STATE_TRANSITION",
                message=f"cannot transition from {current} to {target}",
            )
        )
