from __future__ import annotations

import asyncio
from tempfile import TemporaryDirectory

from kairo_kernel import KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.contracts.turns import TurnRequest

from ._support import EchoProvider, value


async def main() -> None:
    with TemporaryDirectory(prefix="kairo-basic-") as workspace:
        kernel = build_kernel(
            KernelConfig(workspace, database_path=":memory:", enable_builtin_tools=False),
            KernelDependencies(provider=EchoProvider()),
        )
        async with kernel:
            session = value(await kernel.sessions.create("Example"))
            accepted = value(await kernel.submit(TurnRequest("hello", session.session_id)))
            result = value(await kernel.wait(accepted.turn_id, timeout_seconds=2))
            assert result.status.value == "succeeded"
            assert result.messages


if __name__ == "__main__":
    asyncio.run(main())
