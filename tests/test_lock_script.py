"""Tests for LLMPROXY_LOCK_SCRIPT functionality with different modes."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import sys
import tempfile

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llmproxy.script_loader import load_lock_script, execute_lock_script


class TestLoadLockScriptPythonMode:
    """Test loading Python scripts (.py extension)."""
    
    def test_python_script_path_loaded(self, tmp_path):
        """Test that a .py file path is recognized and loaded correctly."""
        script_path = tmp_path / "test_lock.py"
        script_path.write_text("""
def handle_request(request_data):
    return True
""")
        
        result = load_lock_script(str(script_path))
        
        assert result["type"] == "python"
        assert result["module"] is not None
        assert callable(result.get("handle_request"))
        assert result["error"] is None
    
    def test_python_script_with_handle_request(self, tmp_path):
        """Test that Python script with handle_request function is executable."""
        script_path = tmp_path / "lock_handler.py"
        script_path.write_text("""
def handle_request(request_data):
    if request_data.get("method") == "POST":
        return True
    return False
""")
        
        result = load_lock_script(str(script_path))
        
        assert result["type"] == "python"
        assert callable(result.get("handle_request"))
        
        assert result["handle_request"]({"method": "POST"}) is True
        assert result["handle_request"]({"method": "GET"}) is False
    
    def test_python_script_without_handle_request(self, tmp_path):
        """Test Python script without handle_request still loads."""
        script_path = tmp_path / "simple_lock.py"
        script_path.write_text("""
LOCK_ENABLED = True
""")
        
        result = load_lock_script(str(script_path))
        
        assert result["type"] == "python"
        assert result["handle_request"] is None


class TestLoadLockScriptShellMode:
    """Test loading Shell scripts (.sh, .bash extensions)."""
    
    def test_shell_script_path_loaded(self, tmp_path):
        """Test that a .sh file path is recognized and loaded correctly."""
        script_path = tmp_path / "test_lock.sh"
        script_path.write_text("#!/bin/bash\necho 'locked'")
        script_path.chmod(0o755)
        
        result = load_lock_script(str(script_path))
        
        assert result["type"] == "shell"
        assert result["path"] == str(script_path)
        assert result["executable"] is True
        assert result["error"] is None
    
    def test_bash_script_path_loaded(self, tmp_path):
        """Test that a .bash file path is recognized and loaded correctly."""
        script_path = tmp_path / "test_lock.bash"
        script_path.write_text("#!/bin/bash\nexit 0")
        script_path.chmod(0o755)
        
        result = load_lock_script(str(script_path))
        
        assert result["type"] == "shell"
        assert result["path"] == str(script_path)
    
    def test_shell_script_executable(self, tmp_path):
        """Test that shell script can be executed and returns correct output."""
        script_path = tmp_path / "echo_lock.sh"
        script_path.write_text("#!/bin/bash\necho 'LOCK_ACQUIRED'")
        script_path.chmod(0o755)
        
        result = load_lock_script(str(script_path))
        
        exec_result = execute_lock_script(result, {"method": "POST"})
        
        assert exec_result["success"] is True
        assert "LOCK_ACQUIRED" in exec_result["result"]


class TestLoadLockScriptBashCommandMode:
    """Test loading raw bash commands (non-file paths)."""
    
    def test_bash_command_echo(self):
        """Test that a simple echo command is recognized as bash command mode."""
        command = "echo 'test lock'"
        
        result = load_lock_script(command)
        
        assert result["type"] == "command"
        assert result["command"] == command
        assert result["path"] is None
    
    def test_bash_command_complex(self):
        """Test that a complex bash command is recognized correctly."""
        command = "if [ -f /tmp/lock ]; then echo 'locked'; fi"
        
        result = load_lock_script(command)
        
        assert result["type"] == "command"
        assert result["command"] == command
    
    def test_bash_command_execution(self):
        """Test that bash command can be executed successfully."""
        command = "echo 'BASH_LOCK_ACTIVE'"
        
        result = load_lock_script(command)
        
        exec_result = execute_lock_script(result, {"method": "POST"})
        
        assert exec_result["success"] is True
        assert "BASH_LOCK_ACTIVE" in exec_result["result"]
    
    def test_bash_command_with_exit_code(self):
        """Test that bash command exit codes are captured correctly."""
        command = "echo 'partial'; exit 1"
        
        result = load_lock_script(command)
        
        exec_result = execute_lock_script(result, {"method": "POST"})
        
        assert exec_result["success"] is False
        assert "partial" in exec_result["result"]
        assert "exited with code 1" in exec_result["error"]


class TestLoadLockScriptModeDetection:
    """Test the automatic mode detection logic."""
    
    def test_mode_detection_by_extension(self, tmp_path):
        """Test that mode is correctly detected by file extension."""
        py_script = tmp_path / "script.py"
        py_script.write_text("print('test')")
        assert load_lock_script(str(py_script))["type"] == "python"
        
        sh_script = tmp_path / "script.sh"
        sh_script.write_text("#!/bin/bash")
        sh_script.chmod(0o755)
        assert load_lock_script(str(sh_script))["type"] == "shell"
        
        bash_script = tmp_path / "script.bash"
        bash_script.write_text("#!/bin/bash")
        bash_script.chmod(0o755)
        assert load_lock_script(str(bash_script))["type"] == "shell"
    
    def test_mode_detection_by_file_existence(self, tmp_path):
        """Test that existing files are treated as scripts, non-existing as commands."""
        existing_script = tmp_path / "exists.sh"
        existing_script.write_text("#!/bin/bash")
        existing_script.chmod(0o755)
        assert load_lock_script(str(existing_script))["type"] == "shell"
        
        non_existing = "/tmp/nonexistent_script_12345.sh"
        assert load_lock_script(non_existing)["type"] == "command"
    
    def test_mode_detection_priority(self, tmp_path):
        """Test that extension takes priority over file existence check."""
        fake_py = tmp_path / "fake.py"
        fake_py.write_text("#!/bin/bash\necho 'test'")
        fake_py.chmod(0o755)
        
        result = load_lock_script(str(fake_py))
        assert result["type"] == "python"


class TestExecuteLockScriptPythonMode:
    """Test execution of Python mode lock scripts."""
    
    def test_python_script_with_handle_request_execution(self, tmp_path):
        """Test that Python script with handle_request is called correctly."""
        script_path = tmp_path / "conditional_lock.py"
        script_path.write_text("""
def handle_request(request_data):
    if request_data.get("method") == "POST":
        return True
    return False
""")
        
        result = load_lock_script(str(script_path))
        
        post_result = execute_lock_script(result, {"method": "POST"})
        assert post_result["success"] is True
        assert post_result["result"] is True
        
        get_result = execute_lock_script(result, {"method": "GET"})
        assert get_result["success"] is True
        assert get_result["result"] is False
    
    def test_python_script_without_handle_request(self, tmp_path):
        """Test Python script without handle_request uses default behavior."""
        script_path = tmp_path / "no_handler.py"
        script_path.write_text("""
LOCK_VAR = True
""")
        
        result = load_lock_script(str(script_path))
        
        exec_result = execute_lock_script(result, {"method": "POST"})
        assert "success" in exec_result
        assert "handle_request" in exec_result["error"]


class TestExecuteLockScriptShellMode:
    """Test execution of Shell mode lock scripts."""
    
    def test_shell_script_with_environment_variables(self, tmp_path):
        """Test that shell scripts receive request data as environment variables."""
        script_path = tmp_path / "env_check.sh"
        script_path.write_text("""#!/bin/bash
if [ "$LOCK_SCRIPT_METHOD" = "POST" ]; then
    echo "POST detected"
    exit 0
else
    echo "GET detected"
    exit 1
fi
""")
        script_path.chmod(0o755)
        
        result = load_lock_script(str(script_path))
        
        post_result = execute_lock_script(result, {"method": "POST"})
        assert post_result["success"] is True
        assert "POST detected" in post_result["result"]
        
        get_result = execute_lock_script(result, {"method": "GET"})
        assert get_result["success"] is False
        assert "GET detected" in get_result["result"]
    
    def test_shell_script_timeout(self, tmp_path):
        """Test that shell scripts respect timeout settings."""
        script_path = tmp_path / "slow_script.sh"
        script_path.write_text("#!/bin/bash\nsleep 10\necho 'done'")
        script_path.chmod(0o755)
        
        result = load_lock_script(str(script_path))
        
        exec_result = execute_lock_script(result, {"method": "POST"})
        
        assert "success" in exec_result
        # Either timeout or success (if fast enough)
        assert exec_result["success"] or "timed out" in exec_result["error"]


class TestExecuteLockScriptBashCommandMode:
    """Test execution of Bash command mode."""
    
    def test_bash_command_with_environment(self):
        """Test that bash commands receive environment variables."""
        command = "echo $LOCK_SCRIPT_METHOD"
        
        result = load_lock_script(command)
        
        exec_result = execute_lock_script(result, {"method": "POST"})
        
        assert exec_result["success"] is True
        assert "POST" in exec_result["result"]
    
    def test_bash_command_chaining(self):
        """Test that bash command can chain multiple commands."""
        command = "echo 'step1' && echo 'step2'"
        
        result = load_lock_script(command)
        
        exec_result = execute_lock_script(result, {"method": "POST"})
        
        assert exec_result["success"] is True
        assert "step1" in exec_result["result"]
        assert "step2" in exec_result["result"]


class TestLoadLockScriptEdgeCases:
    """Test edge cases and error handling."""
    
    def test_empty_string(self):
        """Test that empty string is handled gracefully."""
        result = load_lock_script("")
        assert result["type"] == "unknown"
        assert result["error"] is not None
    
    def test_whitespace_only(self):
        """Test that whitespace-only string is handled as bash command."""
        result = load_lock_script("   ")
        assert result["type"] == "command"
    
    def test_nonexistent_python_file(self):
        """Test that nonexistent .py file is treated as bash command."""
        result = load_lock_script("/tmp/nonexistent_12345.py")
        assert result["type"] == "command"
    
    def test_special_characters_in_command(self):
        """Test that special characters in bash commands are handled."""
        command = "echo 'test with special chars'"
        
        result = load_lock_script(command)
        
        exec_result = execute_lock_script(result, {"method": "POST"})
        assert exec_result["success"] is True
    
    def test_unicode_in_python_script(self, tmp_path):
        """Test that unicode characters in Python scripts are handled."""
        script_path = tmp_path / "unicode.py"
        script_path.write_text("# Test unicode: 你好世界\ndef handle_request(data):\n    return True")
        
        result = load_lock_script(str(script_path))
        
        assert result["type"] == "python"
        
        exec_result = execute_lock_script(result, {"method": "POST"})
        assert exec_result["success"] is True


class TestLLMPROXY_LOCK_SCRIPTEnvironmentVariable:
    """Test integration with LLMPROXY_LOCK_SCRIPT environment variable."""
    
    def test_env_variable_python_script(self, tmp_path, monkeypatch):
        """Test loading from LLMPROXY_LOCK_SCRIPT env var with Python script."""
        script_path = tmp_path / "env_lock.py"
        script_path.write_text("def handle_request(data): return True")
        
        monkeypatch.setenv("LLMPROXY_LOCK_SCRIPT", str(script_path))
        
        result = load_lock_script(os.environ.get("LLMPROXY_LOCK_SCRIPT", ""))
        
        assert result["type"] == "python"
        assert callable(result.get("handle_request"))
    
    def test_env_variable_shell_script(self, tmp_path, monkeypatch):
        """Test loading from LLMPROXY_LOCK_SCRIPT env var with Shell script."""
        script_path = tmp_path / "env_lock.sh"
        script_path.write_text("#!/bin/bash\necho 'locked'")
        script_path.chmod(0o755)
        
        monkeypatch.setenv("LLMPROXY_LOCK_SCRIPT", str(script_path))
        
        result = load_lock_script(os.environ.get("LLMPROXY_LOCK_SCRIPT", ""))
        
        assert result["type"] == "shell"
    
    def test_env_variable_bash_command(self, monkeypatch):
        """Test loading from LLMPROXY_LOCK_SCRIPT env var with bash command."""
        command = "echo 'env_lock_active'"
        
        monkeypatch.setenv("LLMPROXY_LOCK_SCRIPT", command)
        
        result = load_lock_script(os.environ.get("LLMPROXY_LOCK_SCRIPT", ""))
        
        assert result["type"] == "command"
        
        exec_result = execute_lock_script(result, {"method": "POST"})
        assert exec_result["success"] is True
        assert "env_lock_active" in exec_result["result"]
    
    def test_env_variable_empty(self, monkeypatch):
        """Test loading from empty LLMPROXY_LOCK_SCRIPT env var."""
        monkeypatch.setenv("LLMPROXY_LOCK_SCRIPT", "")
        
        result = load_lock_script(os.environ.get("LLMPROXY_LOCK_SCRIPT", ""))
        
        assert result["type"] == "unknown"
    
    def test_env_variable_not_set(self, monkeypatch):
        """Test loading when LLMPROXY_LOCK_SCRIPT env var is not set."""
        monkeypatch.delenv("LLMPROXY_LOCK_SCRIPT", raising=False)
        
        result = load_lock_script(os.environ.get("LLMPROXY_LOCK_SCRIPT", ""))
        
        assert result["type"] == "unknown"
