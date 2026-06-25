"""Dynamic Python script loader for pre/post request hooks."""

import importlib.util
import os


def load_script_from_path(script_path: str) -> dict:
    """Load a Python script from file path.
    
    Returns a dict with:
        - module: the loaded module (if successful)
        - handle_request: callable if module exports it
        - error: error message if loading failed
    """
    if not script_path or not os.path.isfile(script_path):
        return {
            "module": None,
            "handle_request": None,
            "error": f"Script not found: {script_path}" if script_path else "No script path specified"
        }
    
    try:
        module_name = f"llmproxy_hook_{os.path.basename(script_path)}"
        spec = importlib.util.spec_from_file_location(module_name, script_path)
        if spec is None or spec.loader is None:
            return {
                "module": None,
                "handle_request": None,
                "error": f"Failed to create spec for: {script_path}"
            }
        
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        handle_request = getattr(module, "handle_request", None)
        
        return {
            "module": module,
            "handle_request": handle_request if callable(handle_request) else None,
            "error": None
        }
    except Exception as e:
        return {
            "module": None,
            "handle_request": None,
            "error": f"Error loading {script_path}: {str(e)}"
        }


def execute_hook(hook: dict, request_data: dict = None) -> dict:
    """Execute a loaded hook script.
    
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
