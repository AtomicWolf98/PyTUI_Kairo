# Runtime, events, concurrency, and locks

## Lifecycle state machine

```text
created ──start──> starting ──hook succeeds──> running
   │                    └──hook fails────────> degraded
running ──mark_degraded──────────────────────> degraded
running/degraded/created ──shutdown──> stopping ──hook succeeds──> stopped
                                      └──timeout/failure────────> degraded
```

`start()` is idempotent in `running`; other non-`created` starts return
`conflict`. `mark_degraded` does not change `stopped`. Shutdown closes admission,
optionally cancels active turns, waits the requested grace period, then closes
interactions, MCP, workspace leases, and the database. The event bus closes only
after the final `stopped` lifecycle event.

## Turn admission and snapshot

`SessionTurnSupervisor` admits one active turn per session and concurrent turns
for different sessions. Admission closes before shutdown draining. At submit,
`TurnEngine` captures one immutable `RunSnapshot`: session record, resolved
provider profile, tool descriptors, engine options, session/turn IDs, and
acceptance time. Later session/profile/tool mutations do not alter that run.

The current engine snapshot contains the build-time workspace fields from
`EngineOptions`; it does not acquire `WorkspaceService.turn_snapshot()` or
`ConfigurationService.turn_snapshot()`. See [limitations](limitations.md).

## Turn state machine and pipeline

```text
accepted → running → succeeded
              ├────→ stopping → cancelled
              └──────────────→ failed
```

There is no separate `accepted` event: `TurnAccepted` is the submit response and
the task asynchronously emits `running`. While running, `phase` can be:
`planning`, `connecting`, `thinking`, `streaming`, `waiting_approval`,
`running_tool`, or `compacting`.

The execution pipeline is:

1. optionally generate a plan and request fail-closed plan approval;
2. append the user message;
3. compact context when the configured threshold is reached;
4. stream one provider round, validating a required terminal event;
5. emit assistant deltas/reasoning/usage;
6. classify and execute tool calls, requesting approval when policy requires;
7. append correlated tool results and repeat up to `max_tool_rounds`;
8. commit the complete session record and emit exactly one terminal turn event.

One emergency compaction/retry is allowed after a provider context failure.
Cancellation races each provider stream read. If `stop_saves_partial` is true,
partial assistant content is saved. `wait` timeout is a client wait timeout and
does not cancel the run.

## Provider stream protocol

A provider stream may emit `content`, `reasoning`, `tool_call`, `usage`, followed
by exactly one `completed` or `failed`. Emitting after a terminal event, omitting
the payload required by a kind, raising, or ending without a terminal event is a
provider-client failure. Reasoning is retained in the message; it is only emitted
as deltas when `thinking_mode` is enabled.

## Event envelope

Every `KernelEvent` has global positive `sequence`, UTC timestamp, event ID,
kernel ID, `event_type`, typed payload, schema version 1, optional turn/session
correlation, per-turn sequence, and workspace revision.

| Event type | Payload / actual actions emitted today |
|---|---|
| `lifecycle` | `LifecycleEvent`: starting, running, degraded, stopping, stopped |
| `turn` | `TurnEvent`: status/phase/reason transitions |
| `message` | `MessageEvent`: `delta`, `plan_delta`, `completed` |
| `tool` | `ToolEvent`: `requested`, `started`, `output`, `completed` |
| `interaction` | `InteractionEvent`: `requested`, `resolved` |
| `usage` | `UsageEvent` with context/input/output estimates |

The enum also reserves `context`, `session_changed`, `config_changed`,
`workspace_changed`, `skills_changed`, and `notice`. The current façade/services
do not emit those reserved event types.

`EventBus` assigns sequence and appends to an in-memory bounded deque under one
lock. It offers live events with `put_nowait`; a slow subscriber never blocks the
producer. Replay and live subscription are bridged atomically under the same
lock. `EventReplay.gap` means the requested cursor predates the oldest retained
event. On a full subscriber queue the oldest queued item is dropped and the next
receive raises `SubscriberOverflow(last_delivered_sequence, dropped_events)`
once. Events are not persisted.

## Interaction semantics

Interaction IDs are single-use. Requests can represent tool approval, plan
approval, or text input. Safe defaults must be one of the offered actions and
cannot be an approval action. Cancellation, timeout, invalid correlation, and
shutdown fail closed. Late, duplicate, mismatched-turn, unoffered-action, and
empty-text responses return typed errors. Terminal interaction IDs are retained
in a bounded process-local map to distinguish expired and duplicate responses.

The engine offers `approve_once`, `reject`, `stop`, and a broader-mode choice for
tool approval. The broader-mode choice authorizes the current invocation, but
the current implementation does not mutate the kernel's future authorization
mode.

## Locks and acquisition order

The implementation uses non-reentrant asyncio locks. Integrations and service
participants must preserve these acquisition directions:

1. kernel shutdown lock → lifecycle lock → shutdown hook resources;
2. service mutation lock → service read/write lease or turn-supervisor condition
   → repository operation;
3. workspace mutation lock → writer-preferring workspace write lease → workspace
   repository → runtime participants; rollback calls participants in reverse;
4. configuration mutation lock → writer-preferring config write lease → config
   repository → runtime participants; rollback calls participants in reverse;
5. session/conversation mutation lock → supervisor condition → session
   repository;
6. turn admission: supervisor condition → session repository/provider/tool
   discovery → short engine run-map lock;
7. MCP client lock → transport request/reconnect/catalog refresh;
8. database open lock precedes the single database operation lock; writes use
   `BEGIN IMMEDIATE` inside the operation lock.

The engine run-map lock is released before awaiting provider streams, tools,
interactions, persistence, or event delivery. The event-bus lock never awaits a
subscriber. Repository calls release the database operation lock before service
participants are called.

Participant callbacks execute while their service mutation/write lease is held:
they must not re-enter that service, wait for a read lease on it, or acquire an
earlier lock in this list. Long participant work blocks new readers and writers.
Workspace and configuration leases prefer queued writers, preventing writer
starvation.
