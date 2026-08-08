from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

from kairo_kernel.contracts.enums import EventType, LifecycleState
from kairo_kernel.contracts.events import KernelEvent, LifecycleEvent
from kairo_kernel.contracts.identifiers import EventId, KernelId, SessionId
from kairo_kernel.contracts.turns import TurnRequest


def load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def main() -> None:
    schema_root = Path(__file__).resolve().parents[2] / "docs" / "kernel" / "schema"
    contracts = load(schema_root / "contracts-v1.json")
    events = load(schema_root / "events-v1.json")
    config = load(schema_root / "config-v1.json")
    for schema in (contracts, events, config):
        Draft202012Validator.check_schema(schema)

    request = json.loads(TurnRequest("hello", SessionId("session-1")).to_json())
    Draft202012Validator(contracts).validate(request)
    event = KernelEvent(
        EventId("event-1"),
        KernelId("kernel-1"),
        1,
        datetime.now(timezone.utc),
        EventType.LIFECYCLE,
        LifecycleEvent(LifecycleState.RUNNING),
    )
    Draft202012Validator(events).validate(json.loads(event.to_json()))
    Draft202012Validator(config).validate(
        {
            "workspace_root": ".",
            "database_path": ":memory:",
            "profiles": [],
            "engine_options": {"authorization_mode": "manual"},
        }
    )


if __name__ == "__main__":
    main()
