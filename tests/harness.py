import os
import sys
import shutil
import time

# Reconfigure stdout/stderr to UTF-8 to prevent GBK encoding issues on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')
from pathlib import Path
from agent.bootstrap import build_agent
from agent.config import Config
from agent.core import Agent

# Color printing helpers
def print_success(msg):
    print(f"\033[92m[SUCCESS] {msg}\033[0m")

def print_failure(msg):
    print(f"\033[91m[FAILURE] {msg}\033[0m")

def print_info(msg):
    print(f"\033[94m[INFO] {msg}\033[0m")

# Test Challenge Definitions
CHALLENGES = [
    {
        "id": "c1_math_factorial",
        "name": "Factorial Python Script",
        "prompt": (
            "Write a Python script named harness_factorial.py. It should contain a function "
            "to compute the factorial of a number, and run it to print the factorial of 6."
        ),
        "setup": lambda: None,
        "verify": lambda agent: verify_factorial(),
        "cleanup": lambda: safe_delete("harness_factorial.py")
    },
    {
        "id": "c2_directory_sandbox",
        "name": "Directory Sandboxing & File Creation",
        "prompt": (
            "Create a directory named C:/Users/Admin/Desktop/project/pyTUI/harness_sandbox. "
            "Inside it, create a file named welcome.txt with the content 'Welcome to Kairo Harness'. "
            "Verify the file is created by reading its content."
        ),
        "setup": lambda: None,
        "verify": lambda agent: verify_sandbox(),
        "cleanup": lambda: safe_delete_dir("harness_sandbox")
    },
    {
        "id": "c3_persistent_repl",
        "name": "Persistent Python State (REPL)",
        "prompt": (
            "First, execute a python code block to define a variable 'test_harness_var = 1234'. "
            "Second, in a separate block, print 'RESULT:' followed by the value of test_harness_var * 2."
        ),
        "setup": lambda: None,
        "verify": lambda agent: verify_repl_state(agent),
        "cleanup": lambda: None
    }
]

# Verification logic implementations
def safe_delete(filepath):
    path = Path(filepath)
    if path.exists():
        path.unlink()

def safe_delete_dir(dirpath):
    path = Path(dirpath)
    if path.exists():
        shutil.rmtree(path)

def verify_factorial():
    path = Path("harness_factorial.py")
    if not path.exists():
        return False, "File harness_factorial.py was not created."
    
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    
    if "factorial" not in content.lower():
        return False, "Function factorial not found in script."
        
    # Execute the script to check its output
    import subprocess
    res = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)
    if "720" not in res.stdout:
        return False, f"Expected output 720 not found. Output: {res.stdout}"
        
    return True, "Script correctly created and output matches 6! = 720."

def verify_sandbox():
    path = Path("harness_sandbox/welcome.txt")
    if not path.exists():
        return False, "welcome.txt was not created inside harness_sandbox."
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if content != "Welcome to Kairo Harness":
        return False, f"Incorrect file content: '{content}'"
    return True, "Directory and file created successfully with correct contents."

def verify_repl_state(agent: Agent):
    # Check if the python executor instance namespace has test_harness_var = 1234
    python_tool = agent.registry.tools.get("run_python_code")
    if not python_tool:
        return False, "run_python_code tool was not registered."
        
    namespace = python_tool.repl.locals
    if "test_harness_var" not in namespace:
        return False, f"test_harness_var was not found in REPL local variables. Namespace: {list(namespace.keys())}"
    if namespace["test_harness_var"] != 1234:
        return False, f"test_harness_var has incorrect value: {namespace['test_harness_var']}"
        
    # Also verify if the agent printed the multiplied output
    # Check if any messages in history contain "2468"
    history_text = str(agent.history)
    if "2468" not in history_text:
        return False, "Multplied result (2468) not found in conversation history."
        
    return True, "REPL variables successfully persisted and calculations returned correct results."


def run_harness():
    print_info("==========================================")
    print_info("Starting Kairo Evaluation Test Harness")
    print_info("==========================================")

    # 1. Load config
    config = Config("config.json")
    if config.api_key == "YOUR_API_KEY" or not config.api_key:
        print_failure("API key is not configured in config.json. Please configure API key before running harness.")
        return

    # Force configurations for headless automated testing
    config.auto_mode = True
    config.plan_mode = False
    config.thinking_mode = False # Keep logs clean

    results = []

    for chall in CHALLENGES:
        print_info(f"\n--- Running Challenge: {chall['name']} ---")
        
        # Setup environment
        chall["setup"]()
        
        agent = build_agent(config)
        
        start_time = time.time()
        
        try:
            print_info(f"Prompt: {chall['prompt']}")
            # Run agent interaction
            agent.run_interaction(chall["prompt"])
            
            # Verify results
            success, msg = chall["verify"](agent)
            elapsed = time.time() - start_time
            
            if success:
                print_success(f"{chall['name']} passed! ({elapsed:.2f}s)")
                results.append((chall["name"], "PASS", elapsed, msg))
            else:
                print_failure(f"{chall['name']} failed: {msg} ({elapsed:.2f}s)")
                results.append((chall["name"], "FAIL", elapsed, msg))
                
        except Exception as e:
            elapsed = time.time() - start_time
            print_failure(f"Exception raised in challenge {chall['name']}: {e}")
            results.append((chall["name"], "ERROR", elapsed, str(e)))
            
        finally:
            # Cleanup
            chall["cleanup"]()
            # Terminate persistent shell to prevent hanging process
            shell_tool = agent.registry.tools.get("run_command")
            if shell_tool:
                shell_tool.session.close()

    # Print summary evaluation matrix
    print("\n" + "="*50)
    print("EVALUATION HARNESS RESULTS SUMMARY")
    print("="*50)
    print(f"{'Challenge Name':<40} | {'Status':<6} | {'Time (s)':<8}")
    print("-"*59)
    for name, status, elapsed, msg in results:
        status_color = "\033[92mPASS\033[0m" if status == "PASS" else f"\033[91m{status}\033[0m"
        print(f"{name:<40} | {status_color:<14} | {elapsed:<8.2f}s")
    print("="*50)

if __name__ == "__main__":
    run_harness()
