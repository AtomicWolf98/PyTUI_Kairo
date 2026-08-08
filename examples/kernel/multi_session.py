from __future__ import annotations

import asyncio
from tempfile import TemporaryDirectory

from kairo_kernel import KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.turns import TurnRequest

from ._support import EchoProvider, value


async def main() -> None:
    with TemporaryDirectory(prefix="kairo-sessions-") as workspace:
        kernel = build_kernel(
            KernelConfig(workspace, database_path=":memory:", enable_builtin_tools=False),
            KernelDependencies(provider=EchoProvider(delay=0.02)),
        )
        async with kernel:
            first = value(await kernel.sessions.create("First"))
            second = value(await kernel.sessions.create("Second"))
            turn_one = value(await kernel.submit(TurnRequest("one", first.session_id)))
            turn_two = value(await kernel.submit(TurnRequest("two", second.session_id)))
            duplicate = await kernel.submit(TurnRequest("blocked", first.session_id))
            assert duplicate.error is not None and duplicate.error.code is ErrorCode.KERNEL_BUSY
            completed = await asyncio.gather(kernel.wait(turn_one.turn_id, 2), kernel.wait(turn_two.turn_id, 2))
            assert all(item.ok and item.value is not None for item in completed)


if __name__ == "__main__":
    asyncio.run(main())
