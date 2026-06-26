"""Dynamic script loader for lock hooks (Python and shell scripts)."""

import importlib.util
import os
import subprocess
import stat


def load_script_from_path(script_path: str) -> dict:
    """Load a Python script from file path.
    
    Returns a dict with:
        - type: "python"
        - module: the loaded module (if successful)
        - handle_request: callable if module exports it
        - error: error message if loading failed
    """
    if not script_path or not os.path.isfile(script_path):
        return {
            "type": "python",
            "module": None,
            "handle_request": None,
            "error": f"Script not found: {script_path}" if script_path else "No script path specified"
        }
    
    try:
        module_name = f"llmproxy_hook_{os.path.basename(script_path)}"
        spec = importlib.util.spec_from_file_location(module_name, script_path)
        if spec is None or spec.loader is None:
            return {
                "type": "python",
                "module": None,
                "handle_request": None,
                "error": f"Failed to create spec for: {script_path}"
            }
        
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        handle_request = getattr(module, "handle_request", None)
        
        return {
            "type": "python",
            "module": module,
            "handle_request": handle_request if callable(handle_request) else None,
            "error": None
        }
    except Exception as e:
        return {
            "type": "python",
            "module": None,
            "handle_request": None,
            "error": f"Error loading {script_path}: {str(e)}"
        }


def load_shell_script(script_path: str) -> dict:
    """Load a shell script from file path.
    
    Returns a dict with:
        - type: "shell"
        - path: absolute path to script
        - executable: bool
        - error: error message if loading failed
    """
    if not script_path or not os.path.isfile(script_path):
        return {
            "type": "shell",
            "path": script_path,
            "executable": False,
            "error": f"Script not found: {script_path}" if script_path else "No script path specified"
        }
    
    try:
        abs_path = os.path.abspath(script_path)
        st = os.stat(abs_path)
        executable = bool(st.st_mode & stat.S_IXUSR)
        
        return {
            "type": "shell",
            "path": abs_path,
            "executable": executable,
            "error": None
        }
    except Exception as e:
        return {
            "type": "shell",
            "path": script_path,
            "executable": False,
            "error": f"Error loading {script_path}: {str(e)}"
        }


def execute_hook(hook: dict, request_data: dict = None) -> dict:
    """Execute a loaded hook script (legacy function).
    
    Returns execution result:
        - success: bool
        - result: return value from handle_request (if any)
        - error: error message if execution failed
    """
    if hook is None or hook.get("handle_request") is None:
        return {
            "success": True,
            "result": None,
            "error": "No hook to execute"
        }
    
    try:
        if request_data is not None:
            result = hook["handle_request"](request_data)
        else:
            result = hook["handle_request"]()
        
        return {
            "success": True,
            "result": result,
            "error": None
        }
    except Exception as e:
        return {
            "success": False,
            "result": None,
            "error": f"Hook execution failed: {str(e)}"
        }


def execute_lock_script(hook: dict, request_data: dict = None) -> dict:
    """Execute a lock script (Python or shell).
    
    For Python scripts:
        - If handle_request() exists, call it with request_data
        - Supports phase detection via request_data.get("phase")
        - "post" phase includes response_status
    
    For shell scripts:
        - Exports request data as environment variables
        - Runs script via subprocess
        - Environment variables:
          - LOCK_SCRIPT_METHOD, LOCK_SCRIPT_PATH, LOCK_SCRIPT_URL
          - LOCK_SCRIPT_HEADERS (JSON-encoded)
          - LOCK_SCRIPT_RESPONSE_STATUS (post phase only)
          - LOCK_SCRIPT_PHASE (pre/post)
    
    For bash commands:
        - Exports request data as environment variables
        - Runs command via subprocess with shell=True
        - Same environment variables as shell scripts
    
    Returns execution result:
        - success: bool
        - result: return value or stdout (if any)
        - error: error message if execution failed
    """
    if hook is None:
        return {
            "success": True,
            "result": None,
            "error": "No hook to execute"
        }
    
    try:
        if hook.get("type") == "python":
            # Python script execution
            handle_request = hook.get("handle_request")
            if handle_request is None:
                return {
                    "success": True,
                    "result": None,
                    "error": "Python script has no handle_request() function"
                }
            
            if request_data is not None:
                result = handle_request(request_data)
            else:
                result = handle_request()
            
            return {
                "success": True,
                "result": result,
                "error": None
            }
        
        elif hook.get("type") in ("shell", "command"):
            # Shell script or bash command execution
            script_path = hook.get("path")
            command = hook.get("command")
            
            if not script_path and not command:
                return {
                    "success": False,
                    "result": None,
                    "error": "Shell script path or command not found"
                }
            
            # Build environment
            env = os.environ.copy()
            if request_data:
                env["LOCK_SCRIPT_METHOD"] = request_data.get("method", "")
                env["LOCK_SCRIPT_PATH"] = request_data.get("path", "")
                env["LOCK_SCRIPT_URL"] = request_data.get("url", "")
                env["LOCK_SCRIPT_HEADERS"] = str(request_data.get("headers", {}))
                
                # Phase indicator
                phase = request_data.get("phase", "pre")
                env["LOCK_SCRIPT_PHASE"] = phase
                
                # Global lock status
                env["LOCK_SCRIPT_GLOBAL_LOCK_ENABLED"] = str(request_data.get("global_lock_enabled", False)).lower()
                
                # Post-phase specific
                if phase == "post":
                    env["LOCK_SCRIPT_RESPONSE_STATUS"] = str(request_data.get("response_status", ""))
            
            # Run script or command
            if command:
                # Bash command (shell=True)
                result = subprocess.run(
                    command,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    shell=True
                )
            else:
                # Shell script (shell=False)
                result = subprocess.run(
                    [script_path],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
            
            stdout = result.stdout.strip() if result.stdout else None
            stderr = result.stderr.strip() if result.stderr else None
            
            if result.returncode == 0:
                return {
                    "success": True,
                    "result": stdout,
                    "error": None
                }
            else:
                return {
                    "success": False,
                    "result": stdout,
                    "error": f"Shell script/command exited with code {result.returncode}: {stderr}"
                }
        
        else:
            return {
                "success": False,
                "result": None,
                "error": f"Unknown hook type: {hook.get('type')}"
            }
    
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "result": None,
            "error": "Script execution timed out (30s)"
        }
    except Exception as e:
        return {
            "success": False,
            "result": None,
            "error": f"Script execution failed: {str(e)}"
        }


def load_lock_script(script_path: str | None) -> dict:
    """Load lock script from path or bash command.
    
    Supports three modes:
    1. Python script (.py) - loads as module with handle_request()
    2. Shell script (.sh, .bash) - loads as executable script
    3. Bash command - raw command string (if not a file)
    
    Returns a dict with:
        - type: "python", "shell", or "command"
        - module: loaded Python module (if type="python")
        - handle_request: callable (if type="python" and module exports it)
        - path: absolute path to script (if type="shell" or "python")
        - command: command string (if type="command")
        - executable: bool (if type="shell")
        - error: error message if loading failed
    """
    if not script_path:
        return {
            "type": "unknown",
            "module": None,
            "handle_request": None,
            "path": None,
            "command": None,
            "executable": False,
            "error": "No script path specified"
        }
    
    # Check if it's a file path (non-empty string that exists)
    if os.path.isfile(script_path):
        # Determine script type by extension
        _, ext = os.path.splitext(script_path)
        ext = ext.lower()
        
        if ext in ('.py',):
            # Python script
            return load_script_from_path(script_path)
        
        elif ext in ('.sh', '.bash',):
            # Shell script
            return load_shell_script(script_path)
        
        else:
            # Unknown extension, treat as shell script
            return load_shell_script(script_path)
    
    # Not a file - treat as bash command
    return {
        "type": "command",
        "module": None,
        "handle_request": None,
        "path": None,
        "command": script_path,
        "executable": True,
        "error": None
    }
