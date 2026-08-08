"""Storage error construction kept private to implementation adapters."""

from typing import TypeVar

from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.errors import KernelError, KernelResult

T = TypeVar("T")


def failure(
    code: ErrorCode,
    message: str,
    operation: str,
    *,
    retryable: bool = False,
) -> KernelResult[T]:
    return KernelResult.failure(
        KernelError(
            code=code,
            message=message,
            retryable=retryable,
            operation=operation,
            details=JsonObject(),
        )
    )
