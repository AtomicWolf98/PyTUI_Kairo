from __future__ import annotations

from pathlib import Path

import pytest

from kairo_kernel.testing import ConformanceHarness


@pytest.mark.asyncio
async def test_fifty_cross_session_operations_are_correlated_and_terminal_once(tmp_path: Path) -> None:
    harness = ConformanceHarness.create(tmp_path, session_count=5)
    async with harness.kernel:
        report = await harness.run(rounds=10)
        assert report.submitted == 50
        assert report.busy_mutations == 50
        assert report.invalid_responses == 50
        assert report.cancelled > 0
        assert len(report.terminal_counts) == 50
        assert all(count == 1 for _, count in report.terminal_counts)
        status = await harness.kernel.status()
        assert status.active_turn_id is None
        assert status.active_session_id is None
