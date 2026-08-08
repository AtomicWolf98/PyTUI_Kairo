from __future__ import annotations

import asyncio

from . import approval, basic_turn, event_replay, mcp, memory, multi_session


async def main() -> None:
    await basic_turn.main()
    await multi_session.main()
    await event_replay.main()
    await approval.main()
    await mcp.main()
    await memory.main()


if __name__ == "__main__":
    asyncio.run(main())
