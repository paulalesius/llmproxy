"""Test LockManager global locking."""

import pytest
from src.exrouter.backend import Backend
from src.exrouter.proxy import LockManager


@pytest.fixture
def test_backends():
    """Create test backends."""
    return {
        "llm": Backend(name="llm", url="http://localhost:8080", paths=[], locks=["vision"]),
        "vision": Backend(name="vision", url="http://localhost:8081", paths=[], locks=[]),
        "embed": Backend(name="embed", url="http://localhost:8082", paths=[], locks=["llm"]),
    }


@pytest.mark.asyncio
async def test_acquire_lock(test_backends):
    """Test acquiring a lock."""
    manager = LockManager(test_backends, timeout=1)
    
    # LLM wants to lock vision
    acquired = await manager.acquire("llm", ["vision"])
    assert acquired is True
    
    # Vision should be locked
    assert manager.is_locked("vision") is True


@pytest.mark.asyncio
async def test_release_lock(test_backends):
    """Test releasing a lock."""
    manager = LockManager(test_backends, timeout=1)
    
    # Acquire lock
    await manager.acquire("llm", ["vision"])
    assert manager.is_locked("vision") is True
    
    # Release lock
    await manager.release("llm", ["vision"])
    assert manager.is_locked("vision") is False


@pytest.mark.asyncio
async def test_lock_contention(test_backends):
    """Test lock contention - second acquire should timeout and return False."""
    manager = LockManager(test_backends, timeout=1)  # 1 second timeout
    
    # First backend acquires lock
    acquired1 = await manager.acquire("llm", ["vision"])
    assert acquired1 is True
    
    # Second backend tries to acquire same lock - should timeout
    acquired2 = await manager.acquire("embed", ["vision"])
    assert acquired2 is False  # Vision still locked by llm


@pytest.mark.asyncio
async def test_multiple_locks(test_backends):
    """Test acquiring multiple locks at once."""
    manager = LockManager(test_backends, timeout=1)
    
    # LLM wants to lock both vision and embed
    acquired = await manager.acquire("llm", ["vision", "embed"])
    assert acquired is True
    
    assert manager.is_locked("vision") is True
    assert manager.is_locked("embed") is True
    
    # Release both
    await manager.release("llm", ["vision", "embed"])
    
    assert manager.is_locked("vision") is False
    assert manager.is_locked("embed") is False


@pytest.mark.asyncio
async def test_lock_reacquire_after_release(test_backends):
    """Test that locks can be acquired again after release."""
    manager = LockManager(test_backends, timeout=1)
    
    # First acquisition
    await manager.acquire("llm", ["vision"])
    await manager.release("llm", ["vision"])
    
    # Second acquisition should succeed
    acquired = await manager.acquire("embed", ["vision"])
    assert acquired is True


@pytest.mark.asyncio
async def test_lock_timeout(test_backends):
    """Test that lock acquisition times out correctly."""
    manager = LockManager(test_backends, timeout=1)  # 1 second timeout
    
    # Lock vision
    await manager.acquire("llm", ["vision"])
    
    # Try to acquire with short timeout - should return False after 1s
    import time
    start = time.time()
    acquired = await manager.acquire("embed", ["vision"])
    elapsed = time.time() - start
    
    assert acquired is False
    assert elapsed >= 1  # Should have waited at least 1 second
    assert elapsed < 3   # Should not have waited too long
