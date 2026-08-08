from __future__ import annotations

import asyncio
from tempfile import TemporaryDirectory

from kairo_kernel import KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.contracts.turns import TurnRequest

from ._support import EchoProvider, value


async def main() -> None:
    with TemporaryDirectory(prefix="kairo-events-") as workspace:
        kernel = build_kernel(
            KernelConfig(
                workspace,
                database_path=":memory:",
                enable_builtin_tools=False,
                event_buffer_size=4,
            ),
            KernelDependencies(provider=EchoProvider()),
        )
        async with kernel:
            session = value(await kernel.sessions.create("Replay"))
            accepted = value(await kernel.submit(TurnRequest("events", session.session_id)))
            value(await kernel.wait(accepted.turn_id, 2))
            replay = await kernel.events.snapshot(after_sequence=0)
            assert replay.events
            assert replay.gap  # the bounded buffer evicted older lifecycle/turn events
            cursor = replay.newest_sequence
            subscription = await kernel.events.subscribe(after_sequence=cursor)
            next_turn = value(await kernel.submit(TurnRequest("live", session.session_id)))
            live = await asyncio.wait_for(subscription.receive(), timeout=2)
            assert live.sequence > cursor
            await subscription.close()
            value(await kernel.wait(next_turn.turn_id, 2))


if __name__ == "__main__":
    asyncio.run(main())
