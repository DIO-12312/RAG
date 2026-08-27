"""稳定的领域失败表达；与 RPC 协议和基础设施异常类型解耦。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DomainFailure:
    code: str
    message: str
    retryable: bool = False

    # 在构造完成后校验并固化领域不变式。
    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("failure code must not be empty")


class DomainError(Exception):
    """Base exception carrying a stable machine-readable failure."""

    # 初始化该对象的依赖、配置或受控资源。
    def __init__(self, failure: DomainFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


class InvalidStateTransition(DomainError):
    """Raised when a state machine transition violates a terminal fence."""

    # 初始化该对象的依赖、配置或受控资源。
    def __init__(self, current: object, target: object) -> None:
        super().__init__(
            DomainFailure(
                code="INVALID_STATE_TRANSITION",
                message=f"cannot transition from {current} to {target}",
            )
        )
