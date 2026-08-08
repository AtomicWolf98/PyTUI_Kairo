"""Typed errors and results exposed by the Kernel Contract v1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.identifiers import InteractionId, TurnId
from kairo_kernel.contracts.json import Contract, JsonObject

T = TypeVar("T")


@dataclass(frozen=True)
class KernelError(Contract):
    code: ErrorCode
    message: str
    retryable: bool = False
    operation: str = ""
    details: JsonObject = JsonObject()
    turn_id: TurnId | None = None
    interaction_id: InteractionId | None = None


@dataclass(frozen=True)
class KernelResult(Contract, Generic[T]):
    value: T | None = None
    error: KernelError | None = None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.error is None):
            raise ValueError("KernelResult must contain exactly one of value or error.")

    @property
    def ok(self) -> bool:
        return self.error is None

    @classmethod
    def success(cls, value: T) -> KernelResult[T]:
        return cls(value=value)

    @classmethod
    def failure(cls, error: KernelError) -> KernelResult[T]:
        return cls(error=error)
