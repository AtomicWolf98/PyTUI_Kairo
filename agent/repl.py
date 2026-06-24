import builtins
import io
import queue
import sys
import time
import uuid
import subprocess
import threading
from typing import Dict, Any, Tuple, Optional

class PythonREPL:
    def __init__(self, policy: Optional[Dict[str, Any]] = None):
        if policy is None:
            policy = {
                "deny_builtins": ["exec", "eval", "compile", "__import__", "open"],
                "deny_modules": ["os", "subprocess", "sys", "socket", "urllib"],
            }
        self.deny_builtins: set = set(policy.get("deny_builtins", []))
        self.deny_modules: set = set(policy.get("deny_modules", []))

        # Single persistent namespace for the python session
        self.locals: Dict[str, Any] = {}

        # Build restricted builtins and install a policy-aware import hook
        self.locals["__builtins__"] = self._build_safe_builtins()

        # Pre-populate with standard imports that are not denied
        allowed_std = [m for m in ["os", "sys", "json", "math"] if m not in self.deny_modules]
        if allowed_std:
            exec("import " + ", ".join(allowed_std), self.locals, self.locals)

    def _build_safe_builtins(self) -> Dict[str, Any]:
        """Return a copy of builtins with denied names removed and a policy-aware __import__."""
        safe = dict(builtins.__dict__)
        for name in self.deny_builtins:
            safe.pop(name, None)

        real_import = builtins.__import__
        denied_modules = self.deny_modules

        def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level > 0 or (isinstance(name, str) and name.startswith(".")):
                raise ImportError("Relative imports are not allowed in the sandbox")
            if isinstance(name, str):
                top = name.split(".")[0]
                if top in denied_modules:
                    raise ImportError(f"Import of module '{name}' is denied by policy")
            return real_import(name, globals, locals, fromlist, level)

        safe["__import__"] = _safe_import
        return safe

    def execute(self, code: str) -> str:
        """
        Executes a block of python code and returns the console output (stdout + stderr).
        If the last statement is an expression, it tries to print its value.
        """
        # Save standard streams
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        
        # Redirect stdout and stderr
        redirected_stdout = io.StringIO()
        redirected_stderr = io.StringIO()
        sys.stdout = redirected_stdout
        sys.stderr = redirected_stderr
        
        try:
            # Try to compile code. If it's a single expression, we try to eval it
            # and print the result. Otherwise, we exec it.
            code_clean = code.strip()
            
            # Split code into lines to see if we can extract the last line
            lines = [l for l in code_clean.splitlines() if l.strip()]
            
            if len(lines) == 1:
                # Try to evaluate it first
                try:
                    expr_code = compile(code_clean, "<input>", "eval")
                    res = eval(expr_code, self.locals, self.locals)
                    if res is not None:
                        print(repr(res))
                except Exception:
                    # If eval fails (e.g. it's a statement, not an expression), exec it
                    stmt_code = compile(code_clean, "<input>", "exec")
                    exec(stmt_code, self.locals, self.locals)
            else:
                # For multi-line code blocks
                # We try to compile the entire block as 'exec'
                stmt_code = compile(code, "<input>", "exec")
                exec(stmt_code, self.locals, self.locals)
                
        except Exception:
            # Print traceback or error
            import traceback
            traceback.print_exc(file=sys.stderr)
        finally:
            # Restore standard streams
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            
        stdout_val = redirected_stdout.getvalue()
        stderr_val = redirected_stderr.getvalue()
        
        output = ""
        if stdout_val:
            output += stdout_val
        if stderr_val:
            output += stderr_val
            
        return output


class ShellSession:
    def __init__(self, shell_type: str = "cmd", command_timeout: float = 30.0, on_output=None, cwd: Optional[str] = None):
        self.shell_type = shell_type.lower()
        self.command_timeout = command_timeout
        self.cwd = cwd
        self.process = None
        self.lock = threading.Lock()
        self.on_output = on_output
        self.start()

    def start(self):
        """Starts the persistent shell background subprocess."""
        if self.shell_type == "powershell":
            cmd = ["powershell.exe", "-NoLogo", "-NoExit", "-Command", "-"]
        else: # default cmd
            cmd = ["cmd.exe", "/Q", "/K"] # /Q for echo off, /K to keep running

        kwargs = {}
        if self.cwd is not None:
            kwargs["cwd"] = self.cwd

        # Start process with pipes
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, # merge stderr into stdout
            text=True,
            bufsize=0,
            **kwargs,
        )
        
        # Consume startup messages by running a quick dummy command
        self.execute("echo off")

    def _read_stdout(
        self,
        process: subprocess.Popen,
        sentinel_token: str,
        output_queue: "queue.Queue[Tuple[str, Any]]",
    ):
        """Read stdout until this command's sentinel is found so later commands do not race for stdout."""
        try:
            while True:
                line = process.stdout.readline()
                output_queue.put(("line", line))
                if not line or sentinel_token in line.strip():
                    break
        except Exception as exc:
            output_queue.put(("error", exc))
        finally:
            output_queue.put(("done", None))

    def _kill_unresponsive_process(self):
        """Force-stop a hung shell process and clear the session state."""
        if not self.process:
            return

        process = self.process
        try:
            process.kill()
            process.wait(timeout=2)
        except Exception:
            try:
                process.terminate()
            except Exception:
                pass
        finally:
            self.process = None

    def execute(self, command: str) -> str:
        """
        Executes a command persistently and reads output until the completion sentinel is found.
        """
        if not self.process or self.process.poll() is not None:
            self.start()

        # Unique token to detect command completion
        sentinel_token = f"__SHELL_DONE_{uuid.uuid4().hex}__"
        
        with self.lock:
            # Write command followed by sentinel output command
            if self.shell_type == "powershell":
                full_cmd = f"{command}\nWrite-Output '{sentinel_token}'\n"
            else:
                full_cmd = f"{command}\necho {sentinel_token}\n"

            try:
                self.process.stdin.write(full_cmd)
                self.process.stdin.flush()
            except Exception as e:
                return f"Error writing to shell process: {e}"

            # Read stdout on a worker thread so we can time out if the shell stops responding.
            output_lines = []
            output_queue: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
            reader = threading.Thread(
                target=self._read_stdout,
                args=(self.process, sentinel_token, output_queue),
                daemon=True,
            )
            reader.start()

            last_activity = time.monotonic()
            while True:
                remaining = self.command_timeout - (time.monotonic() - last_activity)
                if remaining <= 0:
                    self._kill_unresponsive_process()
                    reader.join(timeout=1)
                    return (
                        f"Error: shell process became unresponsive after "
                        f"{self.command_timeout:.1f}s and was terminated."
                    )

                try:
                    event_type, payload = output_queue.get(timeout=min(0.1, remaining))
                except queue.Empty:
                    continue

                if event_type == "error":
                    return f"Error reading shell output: {payload}"
                if event_type == "done":
                    break

                line = payload
                last_activity = time.monotonic()
                if not line:
                    break
                
                # Check if this line is the sentinel.
                # Strip trailing whitespace and return chars
                stripped_line = line.strip()
                if sentinel_token in stripped_line:
                    break
                
                # Also filter out the echoed command itself if CMD behaves verbosely
                # (though /Q and echo off should hide it)
                if stripped_line == command.strip():
                    continue
                if stripped_line == f"echo {sentinel_token}":
                    continue
                if stripped_line == f"Write-Output '{sentinel_token}'":
                    continue
                
                if self.on_output:
                    self.on_output(line)
                else:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                
                output_lines.append(line)
                
            return "".join(output_lines)

    def close(self):
        """Terminates the shell process."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
