"""Controller: kernel-facing side of the app; never imports Textual widgets.

The controller owns every kernel interaction and returns immutable UI
actions. It holds the kernel but never touches the DOM; widgets never hold
the kernel.
"""

from __future__ import annotations

from kairo_kernel import KairoKernel
from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.providers import (
    ProviderConnectionReceipt,
    ProviderConnectionRequest,
)
from kairo_kernel.errors import KernelError, KernelResult

from kairo_tui_v2.reducer import (
    DraftReady,
    OpenConnectDialog,
    SubmitFailed,
    UiAction,
)


class TuiController:
    """One kernel-facing command queue; actions are pure and replayable."""

    def __init__(self, kernel: KairoKernel | None) -> None:
        self._kernel = kernel

    async def submit_draft(self, text: str) -> list[UiAction]:
        """Route a submitted draft; never clears it (P1 keeps it in state)."""
        if not text.strip():
            return []
        if self._kernel is None:
            return [OpenConnectDialog(pending_draft=text)]
        resolved = await self._kernel.providers.resolve(role="chat")
        if resolved.ok:
            return [DraftReady(text)]
        error = resolved.error
        if error is not None and error.code is ErrorCode.NOT_FOUND:
            return [OpenConnectDialog(pending_draft=text)]
        return [SubmitFailed(text, error.message if error is not None else "Provider lookup failed.")]

    async def catalog_revision(self) -> int:
        if self._kernel is None:
            return 0
        return (await self._kernel.providers.snapshot()).revision

    async def connect(self, request: ProviderConnectionRequest) -> KernelResult[ProviderConnectionReceipt]:
        """Exactly one atomic configure call; never a chained mutation sequence."""
        if self._kernel is None:
            return KernelResult.failure(
                KernelError(
                    ErrorCode.KERNEL_NOT_RUNNING,
                    "Kernel is not available.",
                    operation="provider.configure",
                )
            )
        return await self._kernel.providers.configure(request)
