"""The single UI-neutral asynchronous turn state machine."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

from kairo_kernel.contracts.content import (
    ContentBlock,
    Message,
    ReasoningBlock,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from kairo_kernel.contracts.enums import (
    AuthorizationMode,
    ErrorCode,
    EventType,
    InteractionAction,
    InteractionKind,
    MessageKind,
    MessageRole,
    OperationScope,
    ProviderFailureKind,
    ProviderStreamKind,
    ToolExecutionStatus,
    TurnPhase,
    TurnStatus,
)
from kairo_kernel.contracts.events import EventPayload, InteractionEvent, MessageEvent, ToolEvent, TurnEvent, UsageEvent
from kairo_kernel.contracts.identifiers import InteractionId, MessageId, TurnId
from kairo_kernel.contracts.interactions import InteractionChoice, InteractionRequest, InteractionResponse
from kairo_kernel.contracts.lifecycle import ContextStats
from kairo_kernel.contracts.providers import ProviderFailure, ProviderRequest, ProviderStreamEvent, ProviderUsage
from kairo_kernel.contracts.support import SessionRecord
from kairo_kernel.contracts.tools import (
    ToolExecutionContext,
    ToolInvocation,
    ToolOutputChunk,
    ToolResult,
)
from kairo_kernel.contracts.turns import CancelReceipt, TurnAccepted, TurnRequest, TurnResult, TurnSnapshot
from kairo_kernel.engine.context import ContextPacker, estimate_context_tokens
from kairo_kernel.engine.models import EngineOptions, RunSnapshot
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.ports.interactions import InteractionPort
from kairo_kernel.ports.providers import ProviderPort
from kairo_kernel.ports.repositories import SessionRepositoryPort
from kairo_kernel.ports.tools import AuthorizationPolicyPort, ToolRegistryPort
from kairo_kernel.runtime.cancellation import CancellationSource
from kairo_kernel.runtime.events import EventBus
from kairo_kernel.runtime.turns import SessionTurnSupervisor, TurnLease


class _EngineFailure(RuntimeError):
    def __init__(self, error: KernelError):
        self.error = error
        super().__init__(error.message)


class _TurnCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class _StreamResult:
    message_id: MessageId
    blocks: tuple[ContentBlock, ...]
    tool_calls: tuple[ToolCallBlock, ...]
    usage: ProviderUsage | None
    failure: ProviderFailure | None = None
    cancelled: bool = False


@dataclass
class _Run:
    snapshot: RunSnapshot
    lease: TurnLease
    cancellation: CancellationSource
    future: asyncio.Future[TurnResult]
    status: TurnStatus = TurnStatus.ACCEPTED
    phase: TurnPhase | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    task: asyncio.Task[None] | None = None
    turn_sequence: int = 0
    terminal_emitted: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    compression_count: int = 0
    output_messages: list[Message] = field(default_factory=list)


class TurnEngine:
    """Coordinate provider, tools, interactions, context and persistence."""

    def __init__(
        self,
        *,
        provider: ProviderPort,
        tools: ToolRegistryPort,
        sessions: SessionRepositoryPort,
        events: EventBus,
        interactions: InteractionPort,
        authorization: AuthorizationPolicyPort,
        options: EngineOptions = EngineOptions(),
        supervisor: SessionTurnSupervisor | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.sessions = sessions
        self.events = events
        self.interactions = interactions
        self.authorization = authorization
        self.options = options
        self.supervisor = supervisor or SessionTurnSupervisor()
        self._runs: dict[TurnId, _Run] = {}
        self._lock = asyncio.Lock()

    async def submit(self, request: TurnRequest) -> KernelResult[TurnAccepted]:
        session_id = request.session_id or self.options.default_session_id
        if session_id is None:
            return KernelResult.failure(
                KernelError(ErrorCode.INVALID_ARGUMENT, "A session_id is required.", operation="turn.submit")
            )
        turn_id = TurnId(uuid.uuid4().hex)
        lease_result = await self.supervisor.start(session_id, turn_id)
        if not lease_result.ok or lease_result.value is None:
            return KernelResult.failure(
                lease_result.error
                or KernelError(ErrorCode.INTERNAL, "Turn admission failed.", operation="turn.submit")
            )
        lease = lease_result.value
        loaded = await self.sessions.load(session_id)
        if not loaded.ok or loaded.value is None:
            await lease.release()
            return KernelResult.failure(
                loaded.error
                or KernelError(ErrorCode.SESSION_NOT_FOUND, "Session not found.", operation="turn.submit")
            )
        resolved = await self.provider.resolve_profile(self.options.profile_id, "chat")
        if not resolved.ok or resolved.value is None:
            await lease.release()
            return KernelResult.failure(
                resolved.error
                or KernelError(ErrorCode.PROVIDER_CLIENT, "Profile resolution failed.", operation="turn.submit")
            )
        try:
            descriptors = await self.tools.list()
        except Exception as exc:
            await lease.release()
            return KernelResult.failure(
                KernelError(ErrorCode.INTERNAL, f"Tool discovery failed: {exc}", operation="turn.submit")
            )
        accepted_at = _now()
        snapshot = RunSnapshot(turn_id, session_id, loaded.value, resolved.value, descriptors, self.options, accepted_at)
        future: asyncio.Future[TurnResult] = asyncio.get_running_loop().create_future()
        run = _Run(snapshot, lease, CancellationSource(), future, compression_count=loaded.value.compression_count)
        async with self._lock:
            self._runs[turn_id] = run
            run.task = asyncio.create_task(self._execute(run, request), name=f"kairo-turn-{turn_id[:8]}")
        return KernelResult.success(TurnAccepted(turn_id, session_id, accepted_at))

    async def get(self, turn_id: TurnId) -> KernelResult[TurnSnapshot]:
        async with self._lock:
            run = self._runs.get(turn_id)
            if run is None:
                return KernelResult.failure(KernelError(ErrorCode.TURN_NOT_FOUND, "Turn not found."))
            return KernelResult.success(self._snapshot(run))

    async def wait(
        self,
        turn_id: TurnId,
        timeout_seconds: float | None = None,
    ) -> KernelResult[TurnResult]:
        async with self._lock:
            run = self._runs.get(turn_id)
            if run is None:
                return KernelResult.failure(KernelError(ErrorCode.TURN_NOT_FOUND, "Turn not found."))
            future = run.future
        try:
            if timeout_seconds is None:
                result = await asyncio.shield(future)
            else:
                result = await asyncio.wait_for(asyncio.shield(future), max(0.0, timeout_seconds))
            return KernelResult.success(result)
        except TimeoutError:
            return KernelResult.failure(
                KernelError(
                    ErrorCode.RESOURCE_EXHAUSTED,
                    "Timed out waiting for turn completion.",
                    retryable=True,
                    operation="turn.wait",
                    turn_id=turn_id,
                )
            )

    async def cancel(self, turn_id: TurnId, reason: str = "") -> KernelResult[CancelReceipt]:
        async with self._lock:
            run = self._runs.get(turn_id)
            if run is None:
                return KernelResult.failure(KernelError(ErrorCode.TURN_NOT_FOUND, "Turn not found."))
            if run.status in _TERMINAL:
                return KernelResult.success(CancelReceipt(turn_id, False, True))
            requested = run.cancellation.cancel(reason or "Turn cancellation requested.")
            if requested:
                run.status = TurnStatus.STOPPING
        if requested:
            await self._emit_turn(run, TurnStatus.STOPPING, run.phase, reason or "Cancellation requested.")
        return KernelResult.success(CancelReceipt(turn_id, requested, False))

    async def _execute(self, run: _Run, request: TurnRequest) -> None:
        history = list(run.snapshot.session.messages)
        dirty = False
        error: KernelError | None = None
        status = TurnStatus.SUCCEEDED
        try:
            run.started_at = _now()
            run.status = TurnStatus.RUNNING
            await self._emit_turn(run, TurnStatus.RUNNING, None)
            user_text = request.text
            if run.snapshot.options.plan_mode:
                user_text = await self._plan(run, request.text)
            self._raise_if_cancelled(run)
            user = Message(
                MessageId(uuid.uuid4().hex),
                MessageRole.USER,
                MessageKind.CHAT,
                (TextBlock(user_text),),
            )
            history.append(user)
            dirty = True
            emergency_retry_used = False
            tool_rounds = 0
            while True:
                self._raise_if_cancelled(run)
                history = await self._compact(run, tuple(history), emergency=False)
                stream = await self._provider_round(run, tuple(history), role="chat", emit_deltas=True)
                if stream.cancelled:
                    partial = self._partial_message(stream)
                    if run.snapshot.options.stop_saves_partial:
                        history.append(partial)
                        run.output_messages.append(partial)
                        await self._emit_message(run, partial.message_id, "completed", partial.content)
                    raise _TurnCancelled("Generation stopped.")
                if stream.failure is not None:
                    if stream.failure.kind is ProviderFailureKind.CONTEXT and not emergency_retry_used:
                        emergency_retry_used = True
                        history = await self._compact(run, tuple(history), emergency=True)
                        continue
                    raise _EngineFailure(_provider_error(stream.failure, run.snapshot.turn_id))
                assistant = Message(
                    stream.message_id,
                    MessageRole.ASSISTANT,
                    MessageKind.CHAT,
                    stream.blocks + stream.tool_calls,
                )
                history.append(assistant)
                run.output_messages.append(assistant)
                await self._emit_message(run, assistant.message_id, "completed", assistant.content)
                if not stream.tool_calls:
                    break
                tool_rounds += 1
                if tool_rounds > run.snapshot.options.max_tool_rounds:
                    raise _EngineFailure(
                        KernelError(
                            ErrorCode.RESOURCE_EXHAUSTED,
                            "Maximum tool rounds exceeded.",
                            operation="turn.tools",
                            turn_id=run.snapshot.turn_id,
                        )
                    )
                for call in stream.tool_calls:
                    result = await self._execute_tool(run, call)
                    tool_message = Message(
                        MessageId(uuid.uuid4().hex),
                        MessageRole.TOOL,
                        MessageKind.CHAT,
                        (ToolResultBlock(result.tool_call_id, result.name, result.status, result.content),),
                        result.name,
                    )
                    history.append(tool_message)
                    run.output_messages.append(tool_message)
                    if run.cancellation.token.cancelled:
                        partial = self._stopped_message()
                        if run.snapshot.options.stop_saves_partial:
                            history.append(partial)
                            run.output_messages.append(partial)
                            await self._emit_message(run, partial.message_id, "completed", partial.content)
                        raise _TurnCancelled("Task stopped after tool completion.")
            await self._commit(run, tuple(history))
        except _TurnCancelled as exc:
            status = TurnStatus.CANCELLED
            if dirty:
                try:
                    await self._commit(run, tuple(history))
                except _EngineFailure as persistence:
                    status = TurnStatus.FAILED
                    error = persistence.error
            if error is None and status is TurnStatus.CANCELLED:
                error = KernelError(
                    ErrorCode.PROVIDER_CLIENT,
                    str(exc),
                    operation="turn.cancel",
                    turn_id=run.snapshot.turn_id,
                )
        except _EngineFailure as exc:
            status = TurnStatus.FAILED
            error = exc.error
            if dirty:
                try:
                    await self._commit(run, tuple(history))
                except _EngineFailure as persistence:
                    error = persistence.error
        except Exception as exc:
            status = TurnStatus.FAILED
            error = KernelError(
                ErrorCode.INTERNAL,
                f"Turn failed: {exc}",
                operation="turn.execute",
                turn_id=run.snapshot.turn_id,
            )
            if dirty:
                with suppress(_EngineFailure):
                    await self._commit(run, tuple(history))
        finally:
            await run.lease.release()
            await self._finish(run, status, error)

    async def _plan(self, run: _Run, user_text: str) -> str:
        await self._transition(run, TurnPhase.PLANNING)
        system = Message(
            MessageId(uuid.uuid4().hex),
            MessageRole.SYSTEM,
            MessageKind.PLAN,
            (TextBlock("Create a detailed implementation plan. Do not execute tools or write code."),),
        )
        user = Message(
            MessageId(uuid.uuid4().hex),
            MessageRole.USER,
            MessageKind.PLAN,
            (TextBlock(user_text),),
        )
        plan = await self._provider_round(run, (system, user), role="plan", emit_deltas=True, action="plan_delta")
        if plan.cancelled:
            raise _TurnCancelled("Plan generation stopped.")
        if plan.failure is not None:
            raise _EngineFailure(_provider_error(plan.failure, run.snapshot.turn_id))
        if plan.tool_calls:
            raise _EngineFailure(
                KernelError(
                    ErrorCode.PROVIDER_CLIENT,
                    "Plan provider returned a tool call.",
                    operation="turn.plan",
                    turn_id=run.snapshot.turn_id,
                )
            )
        plan_message = Message(plan.message_id, MessageRole.ASSISTANT, MessageKind.PLAN, plan.blocks)
        run.output_messages.append(plan_message)
        await self._emit_message(run, plan.message_id, "completed", plan.blocks)
        response = await self._request_interaction(
            run,
            InteractionKind.PLAN_APPROVAL,
            "Approve plan?",
            (
                InteractionChoice(InteractionAction.APPROVE_ONCE, "Approve and run"),
                InteractionChoice(InteractionAction.SUBMIT_TEXT, "Edit plan instructions"),
                InteractionChoice(InteractionAction.STOP, "Cancel task"),
            ),
            InteractionAction.STOP,
        )
        if response.action is InteractionAction.APPROVE_ONCE:
            return user_text
        if response.action is InteractionAction.SUBMIT_TEXT and response.text.strip():
            return f"{user_text}\n\n[User Plan Modification]: {response.text.strip()}"
        raise _TurnCancelled("Plan was cancelled.")

    async def _provider_round(
        self,
        run: _Run,
        messages: tuple[Message, ...],
        *,
        role: str,
        emit_deltas: bool,
        action: str = "delta",
    ) -> _StreamResult:
        await self._transition(run, TurnPhase.CONNECTING)
        profile_result = await self.provider.resolve_profile(run.snapshot.options.profile_id, role)
        if not profile_result.ok or profile_result.value is None:
            raise _EngineFailure(
                profile_result.error
                or KernelError(ErrorCode.PROVIDER_CLIENT, "Profile resolution failed.", operation=f"turn.{role}")
            )
        request = ProviderRequest(profile_result.value, messages, () if role != "chat" else run.snapshot.tools, role=role)
        message_id = MessageId(uuid.uuid4().hex)
        blocks: list[ContentBlock] = []
        calls: list[ToolCallBlock] = []
        usage: ProviderUsage | None = None
        terminal = False
        iterator = self.provider.stream(request, run.cancellation.token)
        try:
            while True:
                next_task = asyncio.create_task(_next_stream_event(iterator))
                cancel_task = asyncio.create_task(run.cancellation.token.wait())
                done, _ = await asyncio.wait((next_task, cancel_task), return_when=asyncio.FIRST_COMPLETED)
                if cancel_task in done:
                    next_task.cancel()
                    with suppress(asyncio.CancelledError, StopAsyncIteration):
                        await next_task
                    return _StreamResult(message_id, tuple(blocks), tuple(calls), usage, cancelled=True)
                cancel_task.cancel()
                with suppress(asyncio.CancelledError):
                    await cancel_task
                try:
                    event = next_task.result()
                except StopAsyncIteration:
                    break
                if terminal:
                    return self._invalid_stream(message_id, blocks, calls, usage, "Provider emitted after terminal event.")
                if event.kind is ProviderStreamKind.CONTENT:
                    blocks.extend(event.content)
                    if emit_deltas and event.content:
                        await self._emit_message(run, message_id, action, event.content)
                    await self._transition(run, TurnPhase.STREAMING)
                elif event.kind is ProviderStreamKind.REASONING:
                    normalized = tuple(
                        ReasoningBlock(block.text) if isinstance(block, TextBlock) else block
                        for block in event.content
                    )
                    blocks.extend(normalized)
                    if emit_deltas and normalized and run.snapshot.options.thinking_mode:
                        await self._emit_message(run, message_id, action, normalized)
                    await self._transition(run, TurnPhase.THINKING)
                elif event.kind is ProviderStreamKind.TOOL_CALL:
                    if event.tool_call is None:
                        return self._invalid_stream(message_id, blocks, calls, usage, "Tool event omitted tool_call.")
                    calls.append(event.tool_call)
                elif event.kind is ProviderStreamKind.USAGE:
                    if event.usage is None:
                        return self._invalid_stream(message_id, blocks, calls, usage, "Usage event omitted usage.")
                    usage = event.usage
                    run.input_tokens += max(0, event.usage.input_tokens)
                    run.output_tokens += max(0, event.usage.output_tokens)
                    await self._emit_usage(run, event.usage.input_tokens + event.usage.output_tokens)
                elif event.kind is ProviderStreamKind.COMPLETED:
                    terminal = True
                elif event.kind is ProviderStreamKind.FAILED:
                    terminal = True
                    return _StreamResult(message_id, tuple(blocks), tuple(calls), usage, failure=event.failure)
        except Exception as exc:
            return self._invalid_stream(message_id, blocks, calls, usage, f"Provider stream raised: {exc}")
        if not terminal:
            return self._invalid_stream(message_id, blocks, calls, usage, "Provider stream ended without terminal event.")
        return _StreamResult(message_id, tuple(blocks), tuple(calls), usage)

    async def _compact(self, run: _Run, history: tuple[Message, ...], *, emergency: bool) -> list[Message]:
        packer = ContextPacker(
            run.snapshot.options.context_trigger_percent,
            run.snapshot.options.context_target_percent,
            run.snapshot.options.preserve_recent_turns,
        )
        if not emergency and not packer.needs_compaction(history, run.snapshot.tools, run.snapshot.profile.context_window):
            return list(history)
        await self._transition(run, TurnPhase.COMPACTING)
        source, retained = packer.source_and_retained(history, emergency=emergency)
        compacted = retained
        if source:
            system = Message(
                MessageId(uuid.uuid4().hex),
                MessageRole.SYSTEM,
                MessageKind.SUMMARY,
                (TextBlock("Summarize the conversation faithfully for the assistant that continues it."),),
            )
            user = Message(
                MessageId(uuid.uuid4().hex),
                MessageRole.USER,
                MessageKind.SUMMARY,
                (TextBlock("\n".join(message.to_json() for message in source)),),
            )
            summary = await self._provider_round(run, (system, user), role="summary", emit_deltas=False)
            if summary.cancelled:
                raise _TurnCancelled("Compaction stopped.")
            if summary.failure is not None:
                raise _EngineFailure(_provider_error(summary.failure, run.snapshot.turn_id))
            text = "".join(block.text for block in summary.blocks if isinstance(block, (TextBlock, ReasoningBlock))).strip()
            if not text:
                raise _EngineFailure(
                    KernelError(
                        ErrorCode.PROVIDER_CONTEXT,
                        "Context compaction returned an empty summary.",
                        operation="turn.compact",
                        turn_id=run.snapshot.turn_id,
                    )
                )
            compacted = packer.insert_summary(retained, text, MessageId(uuid.uuid4().hex))
            run.compression_count += 1
        compacted = packer.trim_to_target(
            compacted,
            run.snapshot.tools,
            run.snapshot.profile.context_window,
            minimum_turns=1 if emergency else run.snapshot.options.preserve_recent_turns,
        )
        if emergency:
            budget = int(run.snapshot.profile.context_window * run.snapshot.options.context_target_percent / 100.0)
            if estimate_context_tokens(compacted, run.snapshot.tools) > budget:
                raise _EngineFailure(
                    KernelError(
                        ErrorCode.PROVIDER_CONTEXT,
                        "Emergency context compaction could not fit the request.",
                        operation="turn.compact",
                        turn_id=run.snapshot.turn_id,
                    )
                )
        await self._emit_usage(run, estimate_context_tokens(compacted, run.snapshot.tools))
        return list(compacted)

    async def _execute_tool(self, run: _Run, call: ToolCallBlock) -> ToolResult:
        started = _now()
        provisional = ToolInvocation(
            call.tool_call_id,
            run.snapshot.turn_id,
            run.snapshot.session_id,
            call.name,
            call.arguments,
            OperationScope.INTERNAL,
        )
        fetched = await self.tools.get(call.name)
        if not fetched.ok or fetched.value is None:
            result = ToolResult(
                call.tool_call_id,
                call.name,
                ToolExecutionStatus.FAILED,
                (TextBlock("Tool not found."),),
                started,
                _now(),
                "Tool not found.",
            )
            await self._emit_tool(run, "completed", provisional, result=result)
            return result
        tool = fetched.value
        classified = await tool.classify(provisional)
        if not classified.ok or classified.value is None:
            message = "Tool scope classification failed." if classified.error is None else classified.error.message
            result = ToolResult(
                call.tool_call_id,
                call.name,
                ToolExecutionStatus.FAILED,
                (TextBlock(message),),
                started,
                _now(),
                message,
            )
            await self._emit_tool(run, "completed", provisional, result=result)
            return result
        invocation = replace(provisional, scope=classified.value)
        await self._emit_tool(run, "requested", invocation)
        mode = run.snapshot.options.authorization_mode
        authorized = await self.authorization.is_authorized(mode, invocation.scope)
        if not authorized:
            response = await self._request_interaction(
                run,
                InteractionKind.TOOL_APPROVAL,
                f"Execute tool '{call.name}'?",
                (
                    InteractionChoice(InteractionAction.APPROVE_ONCE, "Run once"),
                    InteractionChoice(InteractionAction.REJECT, "Reject"),
                    InteractionChoice(InteractionAction.STOP, "Stop task"),
                    InteractionChoice(
                        InteractionAction.ENABLE_YOLO if mode is AuthorizationMode.AUTO else InteractionAction.ENABLE_AUTO,
                        "Enable broader authorization",
                    ),
                ),
                InteractionAction.REJECT,
            )
            if response.action is InteractionAction.STOP:
                run.cancellation.cancel("Tool approval stopped the task.")
                raise _TurnCancelled("Tool approval stopped the task.")
            if response.action is InteractionAction.REJECT or response.action not in (
                InteractionAction.APPROVE_ONCE,
                InteractionAction.ENABLE_AUTO,
                InteractionAction.ENABLE_YOLO,
            ):
                result = ToolResult(
                    call.tool_call_id,
                    call.name,
                    ToolExecutionStatus.REJECTED,
                    (TextBlock("Tool execution was rejected."),),
                    started,
                    _now(),
                    "Tool execution was rejected.",
                )
                await self._emit_tool(run, "completed", invocation, result=result)
                return result
        await self._transition(run, TurnPhase.RUNNING_TOOL)
        await self._emit_tool(run, "started", invocation)
        sink = _EngineToolSink(self, run, invocation)
        context = ToolExecutionContext(
            run.snapshot.options.workspace_root,
            run.snapshot.options.authorization_mode.value,
        )
        try:
            result = await tool.execute(invocation, context, run.cancellation.token, sink)
            if result.tool_call_id != call.tool_call_id or result.name != call.name:
                raise ValueError("Tool result correlation does not match invocation.")
        except Exception as exc:
            result = ToolResult(
                call.tool_call_id,
                call.name,
                ToolExecutionStatus.FAILED,
                (TextBlock(str(exc)),),
                started,
                _now(),
                str(exc),
            )
        await self._emit_tool(run, "completed", invocation, result=result)
        return result

    async def _request_interaction(
        self,
        run: _Run,
        kind: InteractionKind,
        prompt: str,
        choices: tuple[InteractionChoice, ...],
        safe_default: InteractionAction,
    ) -> InteractionResponse:
        request = InteractionRequest(
            InteractionId(uuid.uuid4().hex),
            run.snapshot.turn_id,
            run.snapshot.session_id,
            kind,
            prompt,
            choices,
            _now() + timedelta(seconds=max(0.0, run.snapshot.options.interaction_timeout_seconds)),
            safe_default,
        )
        await self._transition(
            run,
            TurnPhase.WAITING_APPROVAL if kind is InteractionKind.TOOL_APPROVAL else TurnPhase.PLANNING,
        )
        await self._emit(run, EventType.INTERACTION, InteractionEvent("requested", request=request))
        response = await self.interactions.request(request, run.cancellation.token)
        offered = {choice.action for choice in choices}
        if response.turn_id != request.turn_id or response.interaction_id != request.interaction_id or response.action not in offered:
            response = InteractionResponse(request.interaction_id, request.turn_id, safe_default)
        if response.action is InteractionAction.SUBMIT_TEXT and not response.text.strip():
            response = InteractionResponse(request.interaction_id, request.turn_id, safe_default)
        await self._emit(run, EventType.INTERACTION, InteractionEvent("resolved", response=response))
        return response

    async def _commit(self, run: _Run, history: tuple[Message, ...]) -> None:
        record = SessionRecord(
            run.snapshot.session_id,
            run.snapshot.session.name,
            history,
            run.snapshot.session.created_at,
            _now(),
            run.compression_count,
        )
        active = run.snapshot.options.default_session_id == run.snapshot.session_id
        result = await self.sessions.save(record, active=active)
        if not result.ok:
            raise _EngineFailure(
                result.error
                or KernelError(
                    ErrorCode.SESSION_PERSISTENCE_FAILED,
                    "Session persistence failed.",
                    operation="turn.commit",
                    turn_id=run.snapshot.turn_id,
                )
            )

    async def _finish(self, run: _Run, status: TurnStatus, error: KernelError | None) -> None:
        if run.terminal_emitted:
            return
        run.terminal_emitted = True
        run.status = status
        run.phase = None
        run.finished_at = _now()
        reason = "" if error is None else error.message
        with suppress(RuntimeError):
            await self._emit_turn(run, status, None, reason)
        started = run.started_at or run.snapshot.accepted_at
        result = TurnResult(
            run.snapshot.turn_id,
            run.snapshot.session_id,
            status,
            tuple(run.output_messages),
            started,
            run.finished_at,
            reason,
        )
        if not run.future.done():
            run.future.set_result(result)

    def _snapshot(self, run: _Run) -> TurnSnapshot:
        return TurnSnapshot(
            run.snapshot.turn_id,
            run.snapshot.session_id,
            run.status,
            run.phase,
            run.started_at,
            run.finished_at,
            run.cancellation.token.cancelled,
        )

    async def _transition(self, run: _Run, phase: TurnPhase) -> None:
        run.phase = phase
        await self._emit_turn(run, run.status, phase)

    async def _emit_turn(
        self,
        run: _Run,
        status: TurnStatus,
        phase: TurnPhase | None,
        reason: str = "",
    ) -> None:
        await self._emit(run, EventType.TURN, TurnEvent(status, phase, reason))

    async def _emit_message(
        self,
        run: _Run,
        message_id: MessageId,
        action: str,
        content: tuple[ContentBlock, ...],
    ) -> None:
        await self._emit(run, EventType.MESSAGE, MessageEvent(message_id, action, content))

    async def _emit_tool(
        self,
        run: _Run,
        action: str,
        invocation: ToolInvocation,
        *,
        output: ToolOutputChunk | None = None,
        result: ToolResult | None = None,
    ) -> None:
        await self._emit(run, EventType.TOOL, ToolEvent(action, invocation, output, result))

    async def _emit_usage(self, run: _Run, used_tokens: int) -> None:
        limit = max(1, run.snapshot.profile.context_window)
        stats = ContextStats(
            max(0, used_tokens),
            limit,
            min(100.0, max(0.0, used_tokens * 100.0 / limit)),
            run.input_tokens,
            run.output_tokens,
        )
        await self._emit(run, EventType.USAGE, UsageEvent(stats))

    async def _emit(self, run: _Run, event_type: EventType, payload: EventPayload) -> None:
        run.turn_sequence += 1
        await self.events.emit(
            event_type,
            payload,
            turn_sequence=run.turn_sequence,
            turn_id=run.snapshot.turn_id,
            session_id=run.snapshot.session_id,
            workspace_revision=run.snapshot.options.workspace_revision,
        )

    @staticmethod
    def _invalid_stream(
        message_id: MessageId,
        blocks: list[ContentBlock],
        calls: list[ToolCallBlock],
        usage: ProviderUsage | None,
        message: str,
    ) -> _StreamResult:
        return _StreamResult(
            message_id,
            tuple(blocks),
            tuple(calls),
            usage,
            ProviderFailure(ProviderFailureKind.CLIENT, message, False),
        )

    @staticmethod
    def _partial_message(stream: _StreamResult) -> Message:
        marker = TextBlock("[stopped]" if not stream.blocks else "\n\n[stopped]")
        return Message(stream.message_id, MessageRole.ASSISTANT, MessageKind.CHAT, stream.blocks + (marker,))

    @staticmethod
    def _stopped_message() -> Message:
        return Message(
            MessageId(uuid.uuid4().hex),
            MessageRole.ASSISTANT,
            MessageKind.CHAT,
            (TextBlock("[stopped]"),),
        )

    @staticmethod
    def _raise_if_cancelled(run: _Run) -> None:
        if run.cancellation.token.cancelled:
            raise _TurnCancelled("Turn was cancelled.")


class _EngineToolSink:
    def __init__(self, engine: TurnEngine, run: _Run, invocation: ToolInvocation):
        self.engine = engine
        self.run = run
        self.invocation = invocation

    async def write(self, chunk: ToolOutputChunk) -> None:
        if chunk.tool_call_id != self.invocation.tool_call_id:
            raise ValueError("Tool output correlation does not match invocation.")
        await self.engine._emit_tool(self.run, "output", self.invocation, output=chunk)


def _provider_error(failure: ProviderFailure, turn_id: TurnId) -> KernelError:
    codes = {
        ProviderFailureKind.AUTH: ErrorCode.PROVIDER_AUTH,
        ProviderFailureKind.RATE_LIMIT: ErrorCode.PROVIDER_RATE_LIMIT,
        ProviderFailureKind.SERVER: ErrorCode.PROVIDER_SERVER,
        ProviderFailureKind.CONNECTION: ErrorCode.PROVIDER_CONNECTION,
        ProviderFailureKind.CONTEXT: ErrorCode.PROVIDER_CONTEXT,
        ProviderFailureKind.CLIENT: ErrorCode.PROVIDER_CLIENT,
        ProviderFailureKind.CANCELLED: ErrorCode.PROVIDER_CLIENT,
    }
    return KernelError(codes[failure.kind], failure.message, failure.retryable, "turn.provider", turn_id=turn_id)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _next_stream_event(iterator: AsyncIterator[ProviderStreamEvent]) -> ProviderStreamEvent:
    return await anext(iterator)


_TERMINAL = (TurnStatus.SUCCEEDED, TurnStatus.CANCELLED, TurnStatus.FAILED)
