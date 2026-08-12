"""D1 acceptance: fail-closed interaction dialogs."""

from __future__ import annotations

from datetime import datetime, timezone

from kairo_kernel.contracts.enums import (
    AuthorizationMode,
    InteractionAction,
    InteractionKind,
    LifecycleState,
)
from kairo_kernel.contracts.identifiers import InteractionId, KernelId, SessionId, TurnId
from kairo_kernel.contracts.interactions import (
    InteractionChoice,
    InteractionReceipt,
    InteractionRequest,
    InteractionResponse,
)
from kairo_kernel.contracts.lifecycle import ContextStats, KernelStatus
from kairo_kernel.errors import KernelError, KernelResult

from kairo_tui_v2.app import KairoTuiApp
from kairo_tui_v2.dialogs.approval import ApprovalDialog
from kairo_tui_v2.dialogs.plan import PlanDialog
from kairo_tui_v2.reducer import InteractionsUpdated
from tests_v2.support.fakes import FakeEvents

SESSION = SessionId("session-1")
TURN = TurnId("turn-1")


class InteractionKernel:
    """Fake kernel recording interaction responses."""

    def __init__(self) -> None:
        self.responded: list[InteractionResponse] = []
        self.fail_respond: KernelError | None = None
        self.events = FakeEvents()
        self.interactions = InteractionKernel._Interactions(self)

    class _Interactions:
        def __init__(self, owner: InteractionKernel) -> None:
            self._owner = owner

        async def respond(self, response: InteractionResponse) -> KernelResult[InteractionReceipt]:
            if self._owner.fail_respond is not None:
                return KernelResult.failure(self._owner.fail_respond)
            self._owner.responded.append(response)
            await self._owner._emit_resolved(response.interaction_id)
            return KernelResult.success(
                InteractionReceipt(response.interaction_id, response.turn_id, True)
            )

    class _Sessions:
        async def list(self) -> KernelResult[tuple]:
            return KernelResult.success(())

        async def create(self, name: str):
            from kairo_kernel.contracts.support import SessionSummary

            return KernelResult.success(SessionSummary(SESSION, name, 0, datetime.now(timezone.utc), datetime.now(timezone.utc)))

    class _Conversations:
        async def history(self, session_id: object) -> KernelResult[tuple]:
            return KernelResult.success(())

    def __init__(self) -> None:
        self.responded = []
        self.fail_respond = None
        self.events = FakeEvents()
        self.interactions = InteractionKernel._Interactions(self)
        self.sessions = InteractionKernel._Sessions()
        self.conversations = InteractionKernel._Conversations()

    async def status(self) -> KernelStatus:
        return KernelStatus(
            KernelId("k"),
            LifecycleState.RUNNING,
            "0.4.0a2",
            datetime.now(timezone.utc),
            "/",
            0,
            None,
            None,
            None,
            AuthorizationMode.MANUAL,
            False,
            False,
            ContextStats(0, 0, 0.0),
        )

    async def active_turns(self) -> tuple:
        return ()

    async def _emit_resolved(self, interaction_id: object) -> None:
        from datetime import datetime, timezone

        from kairo_kernel.contracts.enums import EventType
        from kairo_kernel.contracts.events import InteractionEvent, KernelEvent
        from kairo_kernel.contracts.identifiers import EventId

        await self.events.emit(
            KernelEvent(
                EventId(f"e{id(interaction_id)}"),
                KernelId("k"),
                1,
                datetime.now(timezone.utc),
                EventType.INTERACTION,
                InteractionEvent("resolved", interaction_id=interaction_id),
                turn_id=TURN,
                session_id=SESSION,
            )
        )


def _tool_request(*, include_stop: bool = True) -> InteractionRequest:
    choices = [InteractionChoice(InteractionAction.APPROVE_ONCE, "Run once")]
    if include_stop:
        choices.append(InteractionChoice(InteractionAction.STOP, "Stop"))
    choices.append(InteractionChoice(InteractionAction.REJECT, "Reject"))
    return InteractionRequest(
        InteractionId("interaction-1"),
        TURN,
        SESSION,
        InteractionKind.TOOL_APPROVAL,
        "Run list_directory on /tmp?",
        tuple(choices),
        None,
        InteractionAction.REJECT,
    )


def _plan_request() -> InteractionRequest:
    return InteractionRequest(
        InteractionId("interaction-2"),
        TURN,
        SESSION,
        InteractionKind.PLAN_APPROVAL,
        "Approve plan?",
        (
            InteractionChoice(InteractionAction.APPROVE_ONCE, "Approve and run"),
            InteractionChoice(InteractionAction.SUBMIT_TEXT, "Edit plan instructions"),
            InteractionChoice(InteractionAction.REJECT, "Cancel"),
        ),
        None,
        InteractionAction.REJECT,
    )


async def test_approval_shows_only_offered_actions() -> None:
    kernel = InteractionKernel()
    app = KairoTuiApp(kernel=kernel)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        app.dispatch_action(InteractionsUpdated((_tool_request(),)))
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, ApprovalDialog)
        labels = [button.label for button in app.screen.query("Button")]
        assert "Run once" in labels
        assert "Stop" in labels
        assert "Reject" in labels


async def test_approval_run_once_responds_exact_correlation() -> None:
    kernel = InteractionKernel()
    app = KairoTuiApp(kernel=kernel)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        app.dispatch_action(InteractionsUpdated((_tool_request(),)))
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#approval-approve_once").press()
        await pilot.pause()
        await pilot.pause()
        assert len(kernel.responded) == 1
        response = kernel.responded[0]
        assert response.interaction_id == InteractionId("interaction-1")
        assert response.turn_id == TURN
        assert response.action is InteractionAction.APPROVE_ONCE
        assert not isinstance(app.screen, ApprovalDialog)


async def test_approval_reject() -> None:
    kernel = InteractionKernel()
    app = KairoTuiApp(kernel=kernel)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        app.dispatch_action(InteractionsUpdated((_tool_request(),)))
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#approval-reject").press()
        await pilot.pause()
        await pilot.pause()
        assert kernel.responded[-1].action is InteractionAction.REJECT


async def test_escape_fails_closed_preferring_stop() -> None:
    kernel = InteractionKernel()
    app = KairoTuiApp(kernel=kernel)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        app.dispatch_action(InteractionsUpdated((_tool_request(),)))
        await pilot.pause()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
        assert kernel.responded[-1].action is InteractionAction.STOP


async def test_escape_without_stop_falls_back_to_reject() -> None:
    kernel = InteractionKernel()
    app = KairoTuiApp(kernel=kernel)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        request = _tool_request(include_stop=False)
        app.dispatch_action(InteractionsUpdated((request,)))
        await pilot.pause()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
        assert kernel.responded[-1].action is InteractionAction.REJECT


async def test_respond_failure_keeps_modal_with_error() -> None:
    kernel = InteractionKernel()
    kernel.fail_respond = KernelError(
        __import__("kairo_kernel.contracts.enums", fromlist=["ErrorCode"]).ErrorCode.CONFLICT,
        "Interaction expired.",
        operation="interaction.respond",
    )
    app = KairoTuiApp(kernel=kernel)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        app.dispatch_action(InteractionsUpdated((_tool_request(),)))
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#approval-approve_once").press()
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, ApprovalDialog)  # modal stays open
        assert "expired" in str(app.screen.query_one("#approval-error").content)


async def test_multiple_interactions_queue_one_modal() -> None:
    kernel = InteractionKernel()
    app = KairoTuiApp(kernel=kernel)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        second = _tool_request()
        app.dispatch_action(
            InteractionsUpdated(
                (
                    _tool_request(),
                    InteractionRequest(
                        InteractionId("interaction-9"),
                        TURN,
                        SESSION,
                        InteractionKind.TOOL_APPROVAL,
                        "Second request?",
                        (InteractionChoice(InteractionAction.REJECT, "Reject"),),
                        None,
                        InteractionAction.REJECT,
                    ),
                )
            )
        )
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, ApprovalDialog)
        assert len(app.screen.query("Button")) == 3  # one modal, not stacked
        # Resolve the first; the second opens next.
        app.screen.query_one("#approval-approve_once").press()
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, ApprovalDialog)
        assert "Second request" in str(app.screen.query_one("#approval-prompt").content)


async def test_plan_approve_edit_cancel() -> None:
    kernel = InteractionKernel()
    app = KairoTuiApp(kernel=kernel)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        app.dispatch_action(InteractionsUpdated((_plan_request(),)))
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, PlanDialog)
        # Approve
        app.screen.query_one("#plan-approve").press()
        await pilot.pause()
        await pilot.pause()
        assert kernel.responded[-1].action is InteractionAction.APPROVE_ONCE
        # Edit with real text input
        app.dispatch_action(InteractionsUpdated((_plan_request(),)))
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#plan-instructions").value = "focus on tests"
        app.screen.query_one("#plan-edit").press()
        await pilot.pause()
        await pilot.pause()
        response = kernel.responded[-1]
        assert response.action is InteractionAction.SUBMIT_TEXT
        assert response.text == "focus on tests"
        # Cancel
        app.dispatch_action(InteractionsUpdated((_plan_request(),)))
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#plan-cancel").press()
        await pilot.pause()
        await pilot.pause()
        assert kernel.responded[-1].action is InteractionAction.REJECT


async def test_plan_edit_requires_text() -> None:
    kernel = InteractionKernel()
    app = KairoTuiApp(kernel=kernel)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        app.dispatch_action(InteractionsUpdated((_plan_request(),)))
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#plan-edit").press()
        await pilot.pause()
        assert kernel.responded == []
        assert isinstance(app.screen, PlanDialog)
