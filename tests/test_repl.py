import unittest
import os
import shutil
import threading
from pathlib import Path
from unittest.mock import patch
from agent.repl import PythonREPL, ShellSession

class TestREPL(unittest.TestCase):
    def test_python_repl_persistence(self):
        repl = PythonREPL()
        
        # 1. Set variable
        out1 = repl.execute("x = 10")
        # 2. Check if variable persists and can be evaluated
        out2 = repl.execute("x * 5")
        self.assertIn("50", out2)

        # 3. Test multi-line block and persistence of imports
        code_block = (
            "import math\n"
            "def my_func(val):\n"
            "    return math.sqrt(val)\n"
        )
        repl.execute(code_block)
        
        out3 = repl.execute("my_func(25)")
        self.assertIn("5.0", out3)

    def test_python_repl_error_handling(self):
        repl = PythonREPL()
        # Ensure it does not crash on syntax error, but returns error trace
        output = repl.execute("print(undefined_variable)")
        self.assertIn("NameError", output)

    def test_shell_session_persistence_cmd(self):
        # We test on CMD since it's the default on Windows
        session = ShellSession(shell_type="cmd")
        
        try:
            # 1. Create a directory and move into it
            test_dir = "test_repl_sandbox"
            # Cleanup if it exists
            if os.path.exists(test_dir):
                shutil.rmtree(test_dir)
                
            session.execute(f"mkdir {test_dir}")
            session.execute(f"cd {test_dir}")
            
            # Check current directory
            cwd_out = session.execute("cd").strip()
            self.assertTrue(cwd_out.endswith(test_dir), f"Directory was: {cwd_out}")
            
            # 2. Set environment variable and check persistence
            session.execute("set PERSISTENT_VAR=TUI_OK")
            var_out = session.execute("echo %PERSISTENT_VAR%").strip()
            self.assertEqual(var_out, "TUI_OK")
            
        finally:
            # Cleanup
            session.close()
            if os.path.exists(test_dir):
                shutil.rmtree(test_dir)

    def test_shell_session_times_out_and_kills_hung_process(self):
        class FakeStdin:
            def write(self, _data):
                return None

            def flush(self):
                return None

        class BlockingStdout:
            def __init__(self, stopped_event):
                self.stopped_event = stopped_event

            def readline(self):
                self.stopped_event.wait(timeout=5)
                return ""

        class FakeProcess:
            def __init__(self):
                self.stopped_event = threading.Event()
                self.stdin = FakeStdin()
                self.stdout = BlockingStdout(self.stopped_event)
                self.killed = False

            def poll(self):
                return 1 if self.killed else None

            def kill(self):
                self.killed = True
                self.stopped_event.set()

            def terminate(self):
                self.kill()

            def wait(self, timeout=None):
                self.stopped_event.wait(timeout=timeout)
                return 0

        with patch.object(ShellSession, "start", lambda self: None):
            session = ShellSession(shell_type="cmd", command_timeout=0.1)

        fake_process = FakeProcess()
        session.process = fake_process

        output = session.execute("dir")

        self.assertIn("became unresponsive", output)
        self.assertTrue(fake_process.killed)
        self.assertIsNone(session.process)

if __name__ == "__main__":
    unittest.main()
