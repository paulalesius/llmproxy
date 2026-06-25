"""Shared fixtures for llmproxy integration tests."""

import os
import pytest
import subprocess
import time
import httpx
from pathlib import Path


# Test configuration
TEST_PORT = 4002
TEST_PID_FILE = Path("/tmp/llmproxy_test.pid")


def pytest_configure():
    """Get test config path."""
    project_root = Path(__file__).parent.parent
    config_path = project_root / "config.test.yaml"
    os.environ["LLMPROXY_TEST_CONFIG"] = str(config_path)


@pytest.fixture(scope="session")
def llmproxy_server():
    """
    Start llmproxy server for integration tests.
    Yields the base URL, handles cleanup on session end.
    """
    project_root = Path(__file__).parent.parent
    config_path = project_root / "config.test.yaml"
    
    # Start server with config file
    proc = subprocess.Popen(
        ["python3", "-m", "src.llmproxy.main", "-c", str(config_path)],
        cwd=str(project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    TEST_PID_FILE.write_text(str(proc.pid))
    
    # Wait for server to be ready
    base_url = f"http://127.0.0.1:{TEST_PORT}"
    max_wait = 10
    started = False
    
    for _ in range(max_wait):
        try:
            with httpx.Client() as client:
                resp = client.get(f"{base_url}/health", timeout=2)
                if resp.status_code == 200 and "healthy" in resp.text:
                    started = True
                    break
        except (httpx.RequestError, httpx.TimeoutException):
            pass
        time.sleep(1)
    
    if not started:
        proc.terminate()
        pytest.fail(f"llmproxy server failed to start within {max_wait}s")
    
    yield base_url
    
    # Cleanup
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    if TEST_PID_FILE.exists():
        TEST_PID_FILE.unlink()


@pytest.fixture
async def client(llmproxy_server):
    """Async HTTP client for testing."""
    async with httpx.AsyncClient(base_url=llmproxy_server) as client:
        yield client


@pytest.fixture
def sync_client(llmproxy_server):
    """Sync HTTP client for testing."""
    with httpx.Client(base_url=llmproxy_server) as client:
        yield client
