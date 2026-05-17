"""
Test Suite — Circuit Breaker Pattern
PDC Assignment 4 — Part 3

This script proves two things:
  1. WITHOUT the circuit breaker → requests block until timeout (simulated slow hanging)
  2. WITH  the circuit breaker → requests fail fast after threshold, then serve a fallback

Run:
    pip install pytest pytest-asyncio httpx fastapi
    pytest tests/test_circuit_breaker.py -v
"""

import asyncio
import time
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

# Import the app and the breaker
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.main import app, llm_breaker, CircuitState, CircuitBreaker, CircuitOpenError


# ─────────────────────────────────────────────
#  Fixtures
# ─────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_breaker():
    """Fresh circuit breaker state before every test."""
    llm_breaker._state             = CircuitState.CLOSED
    llm_breaker._failure_count     = 0
    llm_breaker._last_failure_time = None
    yield
    # Tear-down (also reset, for safety)
    llm_breaker._state             = CircuitState.CLOSED
    llm_breaker._failure_count     = 0
    llm_breaker._last_failure_time = None


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ─────────────────────────────────────────────
#  1. Mandatory: X-Student-ID header
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_student_id_header_present(client):
    """Every response MUST include X-Student-ID header."""
    resp = await client.get("/health")
    assert "x-student-id" in resp.headers, (
        "FAIL: X-Student-ID header is missing — automatic zero per assignment rules!"
    )
    print(f"\n  ✓ X-Student-ID: {resp.headers['x-student-id']}")


# ─────────────────────────────────────────────
#  2. Without Circuit Breaker — blocking behavior
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_without_breaker_requests_block():
    """
    BEFORE FIX: Simulate a naive call to a slow LLM (no circuit breaker).
    The caller blocks for the entire timeout period on every single request.
    With 60 s timeouts in production this starves the server.
    """
    FAKE_TIMEOUT = 0.5  # accelerated for tests; real scenario = 60 s

    async def slow_llm_call():
        await asyncio.sleep(FAKE_TIMEOUT)  # simulates LLM hanging
        raise asyncio.TimeoutError("LLM did not respond")

    start = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await slow_llm_call()
    elapsed = time.monotonic() - start

    assert elapsed >= FAKE_TIMEOUT, "Should have waited for the full timeout"
    print(f"\n  ✗ Without breaker: blocked for {elapsed:.3f}s per request")
    print("    In production (60s timeout) × many users = server meltdown")


# ─────────────────────────────────────────────
#  3. Circuit Breaker unit tests
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_breaker_trips_after_threshold():
    """After `failure_threshold` failures the breaker opens."""
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=60, request_timeout=0.1)

    async def always_timeout():
        await asyncio.sleep(1)  # longer than request_timeout

    for i in range(3):
        with pytest.raises(asyncio.TimeoutError):
            await breaker.call(always_timeout())

    assert breaker.state == CircuitState.OPEN, (
        f"Expected OPEN after {breaker.failure_threshold} failures, got {breaker.state}"
    )
    print(f"\n  ✓ Breaker OPENED after {breaker.failure_threshold} failures")


@pytest.mark.asyncio
async def test_open_breaker_fails_fast():
    """OPEN breaker raises CircuitOpenError instantly — no waiting."""
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=60, request_timeout=0.1)

    async def fail_once():
        await asyncio.sleep(1)

    # Trip it
    with pytest.raises(asyncio.TimeoutError):
        await breaker.call(fail_once())

    assert breaker.state == CircuitState.OPEN

    # Now verify it fails FAST (not after a timeout)
    start = time.monotonic()
    with pytest.raises(CircuitOpenError):
        await breaker.call(fail_once())
    elapsed = time.monotonic() - start

    assert elapsed < 0.05, f"OPEN breaker should fail in <50ms, took {elapsed:.3f}s"
    print(f"\n  ✓ OPEN breaker failed fast in {elapsed*1000:.1f}ms (vs up to 60,000ms without)")


@pytest.mark.asyncio
async def test_breaker_recovers_to_half_open():
    """After recovery_timeout elapses, breaker transitions OPEN → HALF_OPEN."""
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.2, request_timeout=0.05)

    async def always_fail():
        await asyncio.sleep(1)

    with pytest.raises(asyncio.TimeoutError):
        await breaker.call(always_fail())

    assert breaker.state == CircuitState.OPEN

    await asyncio.sleep(0.3)  # wait past recovery_timeout

    assert breaker.state == CircuitState.HALF_OPEN
    print("\n  ✓ Breaker transitioned OPEN → HALF_OPEN after recovery timeout")


@pytest.mark.asyncio
async def test_successful_probe_closes_breaker():
    """A successful HALF_OPEN probe resets breaker back to CLOSED."""
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1, request_timeout=1.0)

    async def fail():
        raise Exception("downstream error")

    # Trip it
    with pytest.raises(Exception):
        await breaker.call(fail())

    await asyncio.sleep(0.15)  # wait for HALF_OPEN
    assert breaker.state == CircuitState.HALF_OPEN

    # Successful probe
    async def succeed():
        return {"ok": True}

    result = await breaker.call(succeed())
    assert result == {"ok": True}
    assert breaker.state == CircuitState.CLOSED
    assert breaker._failure_count == 0
    print("\n  ✓ Successful probe closed the breaker — normal operation resumed")


# ─────────────────────────────────────────────
#  4. Integration: /ai/generate endpoint
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_api_returns_fallback_when_open(client):
    """
    When the circuit is OPEN, /ai/generate returns a 200 fallback
    immediately — not a 503 after 60 s.
    """
    # Manually open the circuit
    llm_breaker._state         = CircuitState.OPEN
    llm_breaker._failure_count = 3
    llm_breaker._last_failure_time = time.monotonic()

    start = time.monotonic()
    resp  = await client.post("/ai/generate", json={"prompt": "Summarize Newton's laws"})
    elapsed = time.monotonic() - start

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "fallback"
    assert elapsed < 0.5, f"Fallback should be instant, took {elapsed:.3f}s"
    print(f"\n  ✓ /ai/generate returned fallback in {elapsed*1000:.1f}ms with OPEN circuit")
    print(f"    Response: {body['result']['summary'][:60]}...")


@pytest.mark.asyncio
async def test_api_trips_breaker_on_repeated_failure(client):
    """
    Firing /ai/generate at the dead LLM URL eventually trips the breaker.
    Subsequent calls get the instant fallback.
    """
    # Set a very short timeout so the test runs quickly
    llm_breaker.request_timeout   = 0.3
    llm_breaker.failure_threshold = 2

    statuses = []
    for _ in range(5):
        resp = await client.post("/ai/generate", json={"prompt": "test"})
        statuses.append((resp.status_code, resp.json().get("source", "error")))

    sources = [s for _, s in statuses]
    print(f"\n  Request sources: {sources}")

    # After threshold failures, breaker opens and returns fallback
    assert "fallback" in sources, (
        "Expected at least one 'fallback' response after breaker opened"
    )
    print("  ✓ Breaker tripped — subsequent calls received instant fallback")


@pytest.mark.asyncio
async def test_breaker_status_endpoint(client):
    """GET /breaker/status exposes breaker internals."""
    resp = await client.get("/breaker/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "state" in body
    assert "failure_count" in body
    print(f"\n  ✓ /breaker/status → {body}")


@pytest.mark.asyncio
async def test_breaker_reset_endpoint(client):
    """POST /breaker/reset clears the breaker."""
    llm_breaker._state         = CircuitState.OPEN
    llm_breaker._failure_count = 5

    resp = await client.post("/breaker/reset")
    assert resp.status_code == 200
    assert resp.json()["breaker"]["state"] == "CLOSED"
    print("\n  ✓ /breaker/reset cleared the open circuit")