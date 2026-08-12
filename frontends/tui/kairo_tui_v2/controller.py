"""Controller: kernel-facing side of the app; never imports Textual widgets.

The controller owns every kernel interaction and returns immutable UI
actions. It holds the kernel but never touches the DOM; widgets never hold
the kernel.
"""

from __future__ import annotations

from kairo_kernel import KairoKernel
from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.identifiers import SessionId, TurnId
from kairo_kernel.contracts.providers import (
    ProviderConnectionReceipt,
    ProviderConnectionRequest,
    ProviderProfile,
)
from kairo_kernel.contracts.turns import TurnRequest
from kairo_kernel.errors import KernelError, KernelResult

from kairo_tui_v2.reducer import (
    DraftAccepted,
    NoticeSet,
    OpenConnectDialog,
    SessionActivated,
    SubmitFailed,
    TurnStarted,
    UiAction,
)
from kairo_tui_v2.state import SessionView, TurnView, WorkspaceView

TERMINAL_STATUSES = frozenset({"succeeded", "cancelled", "failed"})


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
        if not resolved.ok:
            error = resolved.error
            if error is not None and error.code is ErrorCode.NOT_FOUND:
                return [OpenConnectDialog(pending_draft=text)]
            return [SubmitFailed(text, error.message if error is not None else "Provider lookup failed.")]
        return await self._start_turn(text)

    async def _start_turn(self, text: str) -> list[UiAction]:
        assert self._kernel is not None
        session_id = await self._ensure_session()
        try:
            request = TurnRequest(text, session_id)
        except ValueError:
            return [SubmitFailed(text, "Turn text must not be empty.")]
        accepted = await self._kernel.submit(request)
        if not accepted.ok or accepted.value is None:
            error = accepted.error
            return [
                SubmitFailed(
                    text,
                    error.message if error is not None else "Turn was not accepted.",
                )
            ]
        turn = accepted.value
        return [
            DraftAccepted(turn.session_id),
            TurnStarted(turn.turn_id, turn.session_id),
            SessionActivated(turn.session_id),
        ]

    async def retry_draft(self, text: str) -> list[UiAction]:
        """Retry uses the exact last user message text; old records stay."""
        if not text.strip():
            return []
        if self._kernel is None:
            return [SubmitFailed(text, "Kernel is not available.")]
        return await self._start_turn(text)

    async def cancel_turn(self, turn_id: TurnId) -> list[UiAction]:
        if self._kernel is None:
            return [NoticeSet("Kernel is not available.")]
        result = await self._kernel.cancel(turn_id, "User pressed stop.")
        if not result.ok:
            return [NoticeSet(result.error.message if result.error is not None else "Stop failed.")]
        return [NoticeSet("")]

    async def _ensure_session(self) -> SessionId:
        """Reuse the newest session or create one; never duplicates implicitly."""
        assert self._kernel is not None
        loaded = await self._kernel.sessions.list()
        if loaded.ok and loaded.value:
            return loaded.value[-1].session_id
        created = await self._kernel.sessions.create("Chat")
        if created.ok and created.value is not None:
            return created.value.session_id
        raise RuntimeError("Could not create a session.")

    async def load_sessions(self) -> tuple[SessionView, ...]:
        if self._kernel is None:
            return ()
        loaded = await self._kernel.sessions.list()
        if not loaded.ok or loaded.value is None:
            return ()
        active_turns = await self._kernel.active_turns()
        running = {turn.session_id for turn in active_turns}
        return tuple(
            SessionView(
                item.session_id,
                item.name,
                item.message_count,
                item.updated_at,
                item.session_id in running,
            )
            for item in loaded.value
        )

    async def load_active_turns(self) -> tuple[TurnView, ...]:
        if self._kernel is None:
            return ()
        active = await self._kernel.active_turns()
        return tuple(
            TurnView(
                turn.turn_id,
                turn.session_id,
                turn.status,
                turn.phase,
                turn.status.value in TERMINAL_STATUSES,
            )
            for turn in active
        )

    async def load_history(self, session_id: SessionId) -> list[UiAction]:
        """Full transcript reload for recovery and session switches."""
        from kairo_tui_v2.reducer import TranscriptReplaced
        from kairo_tui_v2.state import TranscriptEntry

        if self._kernel is None:
            return []
        loaded = await self._kernel.conversations.history(session_id)
        if not loaded.ok or loaded.value is None:
            return [NoticeSet("History could not be loaded.")]
        entries = tuple(
            TranscriptEntry(
                message.message_id,
                message.role.value,
                message.kind.value,
                message.content,
                message.name,
            )
            for message in loaded.value
        )
        return [TranscriptReplaced(session_id, entries)]

    async def load_workspace(self) -> list[UiAction]:
        from kairo_tui_v2.reducer import WorkspaceUpdated

        if self._kernel is None:
            return []
        status = await self._kernel.status()
        return [
            WorkspaceUpdated(
                WorkspaceView(status.workspace_root, status.workspace_revision)
            )
        ]


    async def create_session(self, name: str) -> list[UiAction]:
        from kairo_tui_v2.reducer import NoticeSet, SessionActivated, SessionsLoaded

        if self._kernel is None:
            return [NoticeSet("Kernel is not available.")]
        created = await self._kernel.sessions.create(name)
        if not created.ok or created.value is None:
            return [NoticeSet(created.error.message if created.error else "Session create failed.")]
        loaded = await self.load_sessions()
        return [SessionsLoaded(loaded), SessionActivated(created.value.session_id)]

    async def rename_session(self, session_id: object, name: str) -> list[UiAction]:
        from kairo_tui_v2.reducer import NoticeSet, SessionsLoaded

        if self._kernel is None:
            return [NoticeSet("Kernel is not available.")]
        result = await self._kernel.sessions.rename(session_id, name)  # type: ignore[arg-type]
        if not result.ok:
            return [NoticeSet(result.error.message if result.error else "Rename failed.")]
        loaded = await self.load_sessions()
        return [SessionsLoaded(loaded)]

    async def delete_session(self, session_id: object) -> list[UiAction]:
        from kairo_tui_v2.reducer import NoticeSet, SessionsLoaded

        if self._kernel is None:
            return [NoticeSet("Kernel is not available.")]
        result = await self._kernel.sessions.delete(session_id)  # type: ignore[arg-type]
        if not result.ok:
            return [NoticeSet(result.error.message if result.error else "Delete failed.")]
        loaded = await self.load_sessions()
        return [SessionsLoaded(loaded)]

    async def execute_command(self, name: str) -> list[UiAction]:
        from kairo_tui_v2.reducer import NoticeSet

        if self._kernel is None:
            return [NoticeSet("Kernel is not available.")]
        command_text = f"/{name}" if not name.startswith("/") else name
        parsed = self._kernel.commands.parse(command_text)
        if not parsed.ok or parsed.value is None:
            return [NoticeSet(parsed.error.message if parsed.error else "Unknown command.")]
        result = await self._kernel.commands.execute(parsed.value, self._active_session_hint)
        if not result.ok:
            return [NoticeSet(result.error.message if result.error else "Command failed.")]
        return [NoticeSet("")]

    async def select_model(self, profile_id: object) -> list[UiAction]:
        from kairo_kernel.contracts.preferences import PreferencesPatch

        from kairo_tui_v2.reducer import NoticeSet, ProfileUpdated

        if self._kernel is None:
            return [NoticeSet("Kernel is not available.")]
        result = await self._kernel.preferences.patch(
            PreferencesPatch(expected_revision=0, profile_id=profile_id)  # type: ignore[arg-type]
        )
        if not result.ok:
            return [NoticeSet(result.error.message if result.error else "Model select failed.")]
        return [ProfileUpdated(str(profile_id))]

    async def model_profiles(self) -> tuple[ProviderProfile, ...]:
        if self._kernel is None:
            return ()
        snapshot = await self._kernel.providers.snapshot()
        return snapshot.profiles

    async def kernel_command_catalog(self) -> tuple[object, ...]:
        if self._kernel is None:
            return ()
        return self._kernel.commands.catalog()

    @property
    def _active_session_hint(self) -> None:
        return None

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
