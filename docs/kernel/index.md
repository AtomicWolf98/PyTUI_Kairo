# Kairo Kernel v1

This directory is the implementation-grounded reference for the asynchronous,
frontend-neutral kernel in `kairo_kernel`. The normative Python surface is
`build_kernel(KernelConfig, KernelDependencies)` and `KairoKernel`; the JSON
Schemas under `schema/` describe the version-1 contract and event wire shapes.

## Architecture

```text
plain / TUI / Web UI / embedding
              │
        KairoKernel façade
              │
 sessions · conversations · memory · configuration · workspace
 providers · skills · MCP · diagnostics · tools · interactions · events
              │
 TurnEngine + asyncio runtime primitives
              │
 frozen contracts and Protocol ports
              │
 SQLite · provider adapters · built-in tools · MCP transports
```

The kernel has no Rich, Textual, FastAPI, or frontend imports. Frontends should
only submit immutable DTOs, consume `KernelResult`, answer interaction requests,
and render the replayable event stream. `build_kernel` performs no I/O; `start`
or `async with` opens the database and optionally connects configured MCP
servers.

## Minimal embedding

```python
from kairo_kernel import KernelConfig, build_kernel
from kairo_kernel.contracts import TurnRequest

kernel = build_kernel(KernelConfig(workspace_root="."))
async with kernel:
    session_result = await kernel.sessions.create("Chat")
    session = session_result.value
    assert session is not None
    accepted_result = await kernel.submit(TurnRequest("Hello", session.session_id))
    accepted = accepted_result.value
    assert accepted is not None
    result = await kernel.wait(accepted.turn_id)
```

Production callers must handle `error` instead of relying on the assertions in
this compact example. See `examples/kernel/` for complete offline programs.

## Reference map

- [Public façade](public-api.md)
- [Contracts, ports, wire JSON, and errors](contracts.md)
- [Lifecycle, turn state machine, events, concurrency, and locks](runtime.md)
- [Storage and migration](storage-and-migration.md)
- [Providers, tools, MCP, and security](integrations-and-security.md)
- [Known limitations](limitations.md)
- [Machine schemas](schema/README.md)

The implementation currently advertises `KERNEL_API_VERSION = "1.1"` and
`EVENT_SCHEMA_VERSION = 1`. The Alpha package metadata requires Python 3.11
or newer.
