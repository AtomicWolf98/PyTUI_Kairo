import difflib
import json
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


DEFAULT_IGNORES = {
    ".git", ".venv", "node_modules", "__pycache__", "build", "dist",
}
WRITE_TOOLS = {"write_file", "patch_file"}


@dataclass(frozen=True)
class ChangedFile:
    path: str
    status: str
    session_touched: bool = False
    staged: bool = False
    untracked: bool = False


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: str
    files: Tuple[str, ...] = ()
    changes: Tuple[ChangedFile, ...] = ()
    session_touched: Tuple[str, ...] = ()
    active_file: str = ""
    selected_file: str = ""
    diff: str = ""
    diff_truncated: bool = False
    tree_truncated: bool = False
    error: str = ""


class WorkspaceMonitor:
    """Read-only workspace scanner and diff provider for the Textual UI."""

    def __init__(self, root: Path, *, max_files: int = 2000, max_diff_bytes: int = 204800):
        self.root = Path(root).resolve()
        self.max_files = max(1, int(max_files))
        self.max_diff_bytes = max(1024, int(max_diff_bytes))
        self.is_git = self._detect_git()
        self.git_prefix = self._git_prefix() if self.is_git else ""
        self._state_lock = threading.RLock()
        self.session_touched: Set[str] = set()
        self.active_file = ""
        self._before_contents: Dict[str, Optional[str]] = {}
        self._scan_durations: List[float] = []
        self.recommended_refresh_seconds: float = 2.0
        files, self.tree_truncated, error = self._collect_files()
        self._files = files
        self._baseline_signatures = self._signatures(files)
        self._previous_signatures = dict(self._baseline_signatures)
        self._scan_error = error

    def _run_git(self, args: Sequence[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=False,
            timeout=5,
            check=False,
        )

    def _detect_git(self) -> bool:
        try:
            result = self._run_git(["rev-parse", "--is-inside-work-tree"])
            return result.returncode == 0 and result.stdout.strip() == b"true"
        except (OSError, subprocess.SubprocessError):
            return False

    def _git_prefix(self) -> str:
        try:
            result = self._run_git(["rev-parse", "--show-prefix"])
            return result.stdout.decode("utf-8", errors="replace").strip().replace("\\", "/")
        except (OSError, subprocess.SubprocessError):
            return ""

    def _collect_files(self) -> Tuple[Tuple[str, ...], bool, str]:
        try:
            if self.is_git:
                result = self._run_git(["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
                if result.returncode == 0:
                    values = [
                        value.decode("utf-8", errors="replace").replace("\\", "/")
                        for value in result.stdout.split(b"\0") if value
                    ]
                    values = sorted(dict.fromkeys(values))
                    return tuple(values[:self.max_files]), len(values) > self.max_files, ""

            values: List[str] = []
            truncated = False
            for current, directories, filenames in os.walk(self.root):
                directories[:] = sorted(d for d in directories if d not in DEFAULT_IGNORES)
                for filename in sorted(filenames):
                    path = Path(current) / filename
                    values.append(path.relative_to(self.root).as_posix())
                    if len(values) >= self.max_files:
                        truncated = True
                        return tuple(values), truncated, ""
            return tuple(values), truncated, ""
        except Exception as exc:
            return (), False, f"Workspace scan failed: {exc}"

    def _signatures(self, files: Iterable[str]) -> Dict[str, Tuple[int, int]]:
        signatures: Dict[str, Tuple[int, int]] = {}
        for relative in files:
            try:
                stat = (self.root / relative).stat()
                signatures[relative] = (stat.st_size, stat.st_mtime_ns)
            except OSError:
                continue
        return signatures

    def _relative_inside(self, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            return ""
        try:
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = self.root / candidate
            resolved = candidate.resolve()
            relative = resolved.relative_to(self.root)
            return relative.as_posix()
        except (OSError, ValueError):
            return ""

    @staticmethod
    def _arguments(value: object) -> Dict[str, object]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    def begin_tool(self, name: str, arguments: object) -> str:
        if name not in WRITE_TOOLS:
            with self._state_lock:
                self.active_file = ""
            return ""
        relative = self._relative_inside(self._arguments(arguments).get("path"))
        with self._state_lock:
            self.active_file = relative
            needs_baseline = relative and not self.is_git and relative not in self._before_contents
        if needs_baseline:
            path = self.root / relative
            if not path.exists():
                value = ""
            else:
                try:
                    if path.stat().st_size <= self.max_diff_bytes and b"\0" not in path.read_bytes()[:8192]:
                        value = path.read_text(encoding="utf-8", errors="replace")
                    else:
                        value = None
                except OSError:
                    value = None
            with self._state_lock:
                self._before_contents.setdefault(relative, value)
        return relative

    def finish_tool(self, name: str, arguments: object, success: bool) -> str:
        relative = self._relative_inside(self._arguments(arguments).get("path")) if name in WRITE_TOOLS else ""
        with self._state_lock:
            if success and relative:
                self.session_touched.add(relative)
            self.active_file = ""
        return relative

    def _refresh_files(self):
        files, truncated, error = self._collect_files()
        current = self._signatures(files)
        with self._state_lock:
            for path in set(current) | set(self._previous_signatures):
                if current.get(path) != self._previous_signatures.get(path):
                    self.session_touched.add(path)
            self._files = files
            self.tree_truncated = truncated
            self._scan_error = error
            self._previous_signatures = current

    def _git_changes(self) -> Tuple[List[ChangedFile], str]:
        try:
            result = self._run_git(["status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "."])
        except (OSError, subprocess.SubprocessError) as exc:
            with self._state_lock:
                self.is_git = False
            return [], f"Git status failed: {exc}"
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", errors="replace").strip()
            with self._state_lock:
                self.is_git = False
            return [], message or "Git status failed"

        records = result.stdout.split(b"\0")
        with self._state_lock:
            touched = set(self.session_touched)
        changes: List[ChangedFile] = []
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record or len(record) < 4:
                continue
            code = record[:2].decode("ascii", errors="replace")
            path = record[3:].decode("utf-8", errors="replace").replace("\\", "/")
            if self.git_prefix and path.startswith(self.git_prefix):
                path = path[len(self.git_prefix):]
            if "R" in code or "C" in code:
                if index < len(records) and records[index]:
                    index += 1
            status = "?" if code == "??" else code.strip() or "M"
            changes.append(ChangedFile(
                path=path,
                status=status,
                session_touched=path in touched,
                staged=code[0] not in (" ", "?"),
                untracked=code == "??",
            ))
        changes.sort(key=lambda item: (not item.session_touched, item.path.lower()))
        return changes, ""

    def _plain_changes(self) -> List[ChangedFile]:
        values = []
        with self._state_lock:
            touched = sorted(self.session_touched)
        for path in touched:
            exists_now = path in self._previous_signatures
            existed_before = path in self._baseline_signatures
            status = "A" if exists_now and not existed_before else "D" if existed_before and not exists_now else "M"
            values.append(ChangedFile(path, status, True, False, not existed_before))
        return values

    def _limit_diff(self, value: str) -> Tuple[str, bool]:
        encoded = value.encode("utf-8", errors="replace")
        if len(encoded) <= self.max_diff_bytes:
            return value, False
        clipped = encoded[:self.max_diff_bytes].decode("utf-8", errors="ignore")
        return clipped + f"\n... diff truncated at {self.max_diff_bytes:,} bytes ...", True

    def _git_diff(self, relative: str, change: Optional[ChangedFile]) -> Tuple[str, bool, str]:
        path = self.root / relative
        if change and change.untracked:
            try:
                raw = path.read_bytes()
                if b"\0" in raw[:8192]:
                    return "Binary file; preview unavailable.", False, ""
                text = raw.decode("utf-8", errors="replace")
                lines = text.splitlines()
                diff = ["--- /dev/null", f"+++ b/{relative}", f"@@ -0,0 +1,{len(lines)} @@"]
                diff.extend("+" + line for line in lines)
                value, truncated = self._limit_diff("\n".join(diff))
                return value, truncated, ""
            except OSError as exc:
                return "", False, f"Unable to read {relative}: {exc}"

        try:
            result = self._run_git(["diff", "--no-color", "--no-ext-diff", "--unified=3", "HEAD", "--", relative])
            if result.returncode != 0:
                staged = self._run_git(["diff", "--cached", "--no-color", "--unified=3", "--", relative])
                unstaged = self._run_git(["diff", "--no-color", "--unified=3", "--", relative])
                raw = staged.stdout + unstaged.stdout
            else:
                raw = result.stdout
            if b"Binary files" in raw or b"GIT binary patch" in raw:
                return "Binary file changed; preview unavailable.", False, ""
            value, truncated = self._limit_diff(raw.decode("utf-8", errors="replace"))
            return value or "No textual diff for this file.", truncated, ""
        except (OSError, subprocess.SubprocessError) as exc:
            return "", False, f"Git diff failed: {exc}"

    def _plain_diff(self, relative: str) -> Tuple[str, bool, str]:
        path = self.root / relative
        before = self._before_contents.get(relative)
        if before is None and relative in self._before_contents:
            return "Binary or oversized file; preview unavailable.", False, ""
        if before is None:
            if not path.exists():
                return "File was deleted; no baseline content is available.", False, ""
            try:
                raw = path.read_bytes()
                if b"\0" in raw[:8192]:
                    return "Binary file changed; preview unavailable.", False, ""
                current = raw.decode("utf-8", errors="replace")
                value, truncated = self._limit_diff(
                    "No baseline is available; showing current content.\n\n" + current
                )
                return value, truncated, ""
            except OSError as exc:
                return "", False, f"Unable to read {relative}: {exc}"

        try:
            after = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        except OSError as exc:
            return "", False, f"Unable to read {relative}: {exc}"
        diff = difflib.unified_diff(
            before.splitlines(), after.splitlines(),
            fromfile=f"a/{relative}", tofile=f"b/{relative}", lineterm="",
        )
        value, truncated = self._limit_diff("\n".join(diff))
        return value or "No textual diff for this file.", truncated, ""

    def refresh(self, selected_file: str = "") -> WorkspaceSnapshot:
        import time
        start = time.monotonic()
        self._refresh_files()
        if self.is_git:
            changes, change_error = self._git_changes()
        else:
            changes, change_error = self._plain_changes(), ""

        changed_by_path = {item.path: item for item in changes}
        selected = selected_file if selected_file in changed_by_path else ""
        if not selected and self.active_file in changed_by_path:
            selected = self.active_file
        if not selected and changes:
            selected = changes[0].path

        diff = "Select a changed file to review."
        diff_truncated = False
        diff_error = ""
        if selected:
            if self.is_git:
                diff, diff_truncated, diff_error = self._git_diff(selected, changed_by_path.get(selected))
            else:
                diff, diff_truncated, diff_error = self._plain_diff(selected)

        elapsed = time.monotonic() - start
        self._scan_durations.append(elapsed)
        # Keep a rolling window of the last 5 scans.
        self._scan_durations = self._scan_durations[-5:]
        avg = sum(self._scan_durations) / len(self._scan_durations)
        # Back off linearly with average scan time, capped between 0.5s and 30s.
        self.recommended_refresh_seconds = max(0.5, min(30.0, avg * 2.0))

        errors = [value for value in (self._scan_error, change_error, diff_error) if value]
        with self._state_lock:
            files = self._files
            session_touched = tuple(sorted(self.session_touched))
            active_file = self.active_file
            tree_truncated = self.tree_truncated
        return WorkspaceSnapshot(
            root=str(self.root),
            files=files,
            changes=tuple(changes),
            session_touched=session_touched,
            active_file=active_file,
            selected_file=selected,
            diff=diff,
            diff_truncated=diff_truncated,
            tree_truncated=tree_truncated,
            error=" | ".join(errors),
        )
