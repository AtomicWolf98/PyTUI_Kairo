from __future__ import annotations

import asyncio
import socket
import urllib.error
from email.message import Message as HeaderMessage
from pathlib import Path
from unittest.mock import patch

import pytest

from kairo_kernel.contracts.content import TextBlock
from kairo_kernel.contracts.enums import AuthorizationMode, OperationScope, ToolExecutionStatus
from kairo_kernel.contracts.identifiers import SessionId, ToolCallId, TurnId
from kairo_kernel.contracts.json import JsonObject, freeze_json
from kairo_kernel.contracts.tools import ToolExecutionContext, ToolInvocation, ToolOutputChunk
from kairo_kernel.runtime import CancellationToken, WorkspaceLeaseManager
from kairo_kernel.tools import (
    AuthorizationGate,
    AuthorizationPolicy,
    BuiltinToolRegistry,
    CommandPolicy,
    ListDirTool,
    NetworkPolicy,
    PatchFileTool,
    PolicyViolation,
    PythonCodePolicy,
    ReadFileTool,
    RunCommandTool,
    RunPythonCodeTool,
    SearchFileTool,
    WebFetchTool,
    WorkspacePathPolicy,
    WriteFileTool,
)


class Sink:
    def __init__(self) -> None:
        self.chunks: list[ToolOutputChunk] = []

    async def write(self, chunk: ToolOutputChunk) -> None:
        self.chunks.append(chunk)


def arguments(**values: object) -> JsonObject:
    frozen = freeze_json(values)
    assert isinstance(frozen, JsonObject)
    return frozen


def invocation(name: str, **values: object) -> ToolInvocation:
    return ToolInvocation(
        ToolCallId("call-1"),
        TurnId("turn-1"),
        SessionId("session-1"),
        name,
        arguments(**values),
        OperationScope.INTERNAL,
    )


def context(root: Path) -> ToolExecutionContext:
    return ToolExecutionContext(str(root), AuthorizationMode.MANUAL.value)


async def execute(tool, call: ToolInvocation, root: Path, token: CancellationToken | None = None):
    sink = Sink()
    result = await tool.execute(call, context(root), token or CancellationToken(), sink)
    return result, sink


@pytest.mark.parametrize("mode", tuple(AuthorizationMode))
@pytest.mark.parametrize("scope", tuple(OperationScope))
def test_authorization_matrix(mode: AuthorizationMode, scope: OperationScope) -> None:
    async def exercise() -> None:
        actual = await AuthorizationPolicy().is_authorized(mode, scope)
        expected = mode is AuthorizationMode.YOLO or (
            mode is AuthorizationMode.AUTO and scope is OperationScope.INTERNAL
        )
        assert actual is expected

    asyncio.run(exercise())


def test_canonical_path_rejects_traversal_and_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    policy = WorkspacePathPolicy(root)
    with pytest.raises(PolicyViolation):
        policy.resolve("../outside/secret.txt", must_exist=True)

    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this Windows host.")
    with pytest.raises(PolicyViolation):
        policy.resolve("link/secret.txt", must_exist=True)


def test_file_tools_round_trip_search_list_patch_and_caps(tmp_path: Path) -> None:
    async def exercise() -> None:
        workspace = WorkspaceLeaseManager(str(tmp_path))
        write = WriteFileTool(workspace)
        result, sink = await execute(write, invocation("write_file", path="src/a.txt", content="hello\nworld\n"), tmp_path)
        assert result.status is ToolExecutionStatus.SUCCEEDED
        assert sink.chunks and (tmp_path / "src/a.txt").read_text(encoding="utf-8") == "hello\nworld\n"

        read = ReadFileTool(workspace, max_output_chars=5)
        result, _ = await execute(read, invocation("read_file", path="src/a.txt"), tmp_path)
        assert result.status is ToolExecutionStatus.SUCCEEDED
        assert isinstance(result.content[0], TextBlock) and result.content[0].text == "hello"
        assert isinstance(result.content[-1], TextBlock) and "truncated" in result.content[-1].text

        listing, _ = await execute(ListDirTool(workspace), invocation("list_dir", path=".", recursive=True), tmp_path)
        assert "src/a.txt" in listing.content[0].text
        search, _ = await execute(
            SearchFileTool(workspace), invocation("search_file", path=".", query="world"), tmp_path
        )
        assert "src/a.txt:2:world" in search.content[0].text
        patch_result, _ = await execute(
            PatchFileTool(workspace),
            invocation("patch_file", path="src/a.txt", search_block="world", replace_block="kernel"),
            tmp_path,
        )
        assert patch_result.status is ToolExecutionStatus.SUCCEEDED
        assert "kernel" in (tmp_path / "src/a.txt").read_text(encoding="utf-8")

    asyncio.run(exercise())


def test_authorization_gate_rejects_before_execution_and_supports_approved_once(tmp_path: Path) -> None:
    async def exercise() -> None:
        (tmp_path / "value.txt").write_text("value", encoding="utf-8")
        workspace = WorkspaceLeaseManager(str(tmp_path))
        tool = ReadFileTool(workspace)
        call = invocation("read_file", path="value.txt")
        gate = AuthorizationGate()
        rejected = await gate.execute(tool, call, context(tmp_path), CancellationToken(), Sink())
        assert rejected.status is ToolExecutionStatus.REJECTED
        approved = await gate.execute(
            tool, call, context(tmp_path), CancellationToken(), Sink(), approved_once=True
        )
        assert approved.status is ToolExecutionStatus.SUCCEEDED
        auto_context = ToolExecutionContext(str(tmp_path), AuthorizationMode.AUTO.value)
        automatic = await gate.execute(tool, call, auto_context, CancellationToken(), Sink())
        assert automatic.status is ToolExecutionStatus.SUCCEEDED

    asyncio.run(exercise())


def test_file_tools_reject_escape_oversize_binary_and_ambiguous_patch(tmp_path: Path) -> None:
    async def exercise() -> None:
        workspace = WorkspaceLeaseManager(str(tmp_path))
        outside = tmp_path.parent / "outside-kernel.txt"
        outside.write_text("outside", encoding="utf-8")
        escaped, _ = await execute(ReadFileTool(workspace), invocation("read_file", path=str(outside)), tmp_path)
        assert escaped.status is ToolExecutionStatus.FAILED

        (tmp_path / "large.txt").write_text("12345", encoding="utf-8")
        large, _ = await execute(
            ReadFileTool(workspace, max_read_bytes=4), invocation("read_file", path="large.txt"), tmp_path
        )
        assert large.status is ToolExecutionStatus.FAILED
        (tmp_path / "binary.bin").write_bytes(b"a\0b")
        binary, _ = await execute(ReadFileTool(workspace), invocation("read_file", path="binary.bin"), tmp_path)
        assert binary.status is ToolExecutionStatus.FAILED
        (tmp_path / "repeat.txt").write_text("x x", encoding="utf-8")
        patch_result, _ = await execute(
            PatchFileTool(workspace),
            invocation("patch_file", path="repeat.txt", search_block="x", replace_block="y"),
            tmp_path,
        )
        assert patch_result.status is ToolExecutionStatus.FAILED

    asyncio.run(exercise())


def test_workspace_lease_blocks_move_during_file_operation(tmp_path: Path) -> None:
    async def exercise() -> None:
        workspace = WorkspaceLeaseManager(str(tmp_path))
        lease = await workspace.read()
        writer = asyncio.create_task(workspace.write())
        await asyncio.sleep(0)
        assert not writer.done()
        await lease.release()
        write_lease = await asyncio.wait_for(writer, 0.2)
        await workspace.update(write_lease, str(tmp_path / "new"))
        await write_lease.release()

    asyncio.run(exercise())


def test_command_and_python_scope_classification(tmp_path: Path) -> None:
    workspace = WorkspacePathPolicy(tmp_path)
    command = CommandPolicy(workspace)
    assert command.classify("echo hello") is OperationScope.INTERNAL
    assert command.classify("git reset --hard") is OperationScope.DESTRUCTIVE
    assert command.classify("shutdown /s") is OperationScope.SYSTEM
    assert command.classify(f'type "{tmp_path.parent / "secret.txt"}"') is OperationScope.EXTERNAL
    python = PythonCodePolicy(workspace)
    assert python.classify("print('ok')") is OperationScope.INTERNAL
    assert python.classify("import subprocess") is OperationScope.SYSTEM
    assert python.classify(f"open({str(tmp_path.parent / 'secret.txt')!r})") is OperationScope.EXTERNAL


def test_shell_and_python_tools_execute_timeout_cancel_and_fail_structurally(tmp_path: Path) -> None:
    async def exercise() -> None:
        workspace = WorkspaceLeaseManager(str(tmp_path))
        command = RunCommandTool(workspace, max_output_chars=20)
        success, sink = await execute(command, invocation("run_command", command="echo hello"), tmp_path)
        assert success.status is ToolExecutionStatus.SUCCEEDED and sink.chunks
        assert "hello" in success.content[0].text.lower()

        python = RunPythonCodeTool(workspace)
        py_success, _ = await execute(
            python, invocation("run_python_code", code="print('python-ok')"), tmp_path
        )
        assert py_success.status is ToolExecutionStatus.SUCCEEDED
        gate = AuthorizationGate()
        denied = await gate.execute(
            python,
            invocation("run_python_code", code="import subprocess"),
            context(tmp_path),
            CancellationToken(),
            Sink(),
        )
        assert denied.status is ToolExecutionStatus.REJECTED
        yolo = await gate.execute(
            python,
            invocation("run_python_code", code="import subprocess; print('allowed')"),
            ToolExecutionContext(str(tmp_path), AuthorizationMode.YOLO.value),
            CancellationToken(),
            Sink(),
        )
        assert yolo.status is ToolExecutionStatus.SUCCEEDED

        sleep_code = "import time; time.sleep(5)"
        timeout_tool = RunPythonCodeTool(workspace, timeout_seconds=0.05, deny_modules=())
        timed_out, _ = await execute(
            timeout_tool, invocation("run_python_code", code=sleep_code), tmp_path
        )
        assert timed_out.status is ToolExecutionStatus.FAILED and "timed out" in timed_out.error_message

        token = CancellationToken()
        cancel_tool = RunPythonCodeTool(workspace, timeout_seconds=5, deny_modules=())
        task = asyncio.create_task(
            execute(cancel_tool, invocation("run_python_code", code=sleep_code), tmp_path, token)
        )
        await asyncio.sleep(0.05)
        token.cancel("test")
        cancelled, _ = await asyncio.wait_for(task, 1)
        assert cancelled.status is ToolExecutionStatus.CANCELLED

    asyncio.run(exercise())


def public_address(*_args: object, **_kwargs: object):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def private_address(*_args: object, **_kwargs: object):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]


def test_network_policy_rejects_private_credentials_scheme_and_denied_hosts() -> None:
    with patch("socket.getaddrinfo", side_effect=private_address), pytest.raises(PolicyViolation):
        NetworkPolicy().validate("http://localhost/data")
    with pytest.raises(PolicyViolation):
        NetworkPolicy().validate("file:///etc/passwd")
    with pytest.raises(PolicyViolation):
        NetworkPolicy().validate("https://user:pass@example.com/")
    with patch("socket.getaddrinfo", side_effect=public_address):
        with pytest.raises(PolicyViolation):
            NetworkPolicy(deny_hosts=("example.com",)).validate("https://sub.example.com/")
        assert NetworkPolicy(allow_hosts=("example.com",)).validate("https://example.com/").host == "example.com"


class RedirectOpener:
    def open(self, request, timeout: float):
        del timeout
        headers = HeaderMessage()
        headers["Location"] = "http://127.0.0.1/private"
        raise urllib.error.HTTPError(request.full_url, 302, "Found", headers, None)


def test_web_fetch_validates_redirect_target_before_following(tmp_path: Path) -> None:
    tool = WebFetchTool(WorkspaceLeaseManager(str(tmp_path)), network_policy=NetworkPolicy())

    def addresses(host: str, *_args: object, **_kwargs: object):
        return public_address() if host == "example.com" else private_address()

    with (
        patch("socket.getaddrinfo", side_effect=addresses),
        patch("urllib.request.build_opener", return_value=RedirectOpener()),
        pytest.raises(PolicyViolation),
    ):
        tool._fetch("https://example.com/start")


def test_registry_exposes_exact_builtin_names(tmp_path: Path) -> None:
    async def exercise() -> None:
        workspace = WorkspaceLeaseManager(str(tmp_path))
        tools = (
            ReadFileTool(workspace),
            WriteFileTool(workspace),
            ListDirTool(workspace),
            SearchFileTool(workspace),
            PatchFileTool(workspace),
            RunCommandTool(workspace),
            RunPythonCodeTool(workspace),
            WebFetchTool(workspace),
        )
        registry = BuiltinToolRegistry(tools)
        assert {item.name for item in await registry.list()} == {
            "read_file",
            "write_file",
            "list_dir",
            "search_file",
            "patch_file",
            "run_command",
            "run_python_code",
            "web_fetch",
        }
        assert (await registry.get("read_file")).ok
        assert not (await registry.get("missing")).ok

    asyncio.run(exercise())


def test_tools_do_not_import_legacy_agent_or_tools() -> None:
    root = Path(__file__).parents[3] / "kairo_kernel" / "tools"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    assert "from agent" not in source
    assert "from tools" not in source
