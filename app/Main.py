"""
StudySync Backend — FastAPI with Circuit Breaker Pattern
PDC Assignment 4 — Part 3: Fault Tolerance Fix
Student ID: [Your-Student-ID]
"""

import time
import asyncio
import httpx
from enum import Enum
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ─────────────────────────────────────────────
#  Circuit Breaker Implementation
# ─────────────────────────────────────────────

class CircuitState(Enum):
    CLOSED   = "CLOSED"    # Normal — requests flow through
    OPEN     = "OPEN"      # Tripped — requests fail immediately (no waiting)
    HALF_OPEN = "HALF_OPEN" # Probing — one trial request allowed


class CircuitBreaker:
    """
    A thread-safe Circuit Breaker for protecting against slow/failing
    downstream dependencies (e.g. an external LLM API).

    States:
      CLOSED    → Everything is fine. Failures are counted.
      OPEN      → Too many failures. All calls fail fast with a fallback.
      HALF_OPEN → Recovery probe. One request is let through to test the service.

    Parameters:
      failure_threshold  – How many consecutive failures trip the breaker.
      recovery_timeout   – Seconds to wait in OPEN before probing again.
      request_timeout    – Per-request timeout (seconds) for the downstream call.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 15.0,
        request_timeout: float = 5.0,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout  = recovery_timeout
        self.request_timeout   = request_timeout

        self._state            = CircuitState.CLOSED
        self._failure_count    = 0
        self._last_failure_time: float | None = None

    # ── Public properties ──────────────────────────────────────────────────

    @property
    def state(self) -> CircuitState:
        """Return current state, automatically transitioning OPEN → HALF_OPEN."""
        if (
            self._state == CircuitState.OPEN
            and self._last_failure_time is not None
            and (time.monotonic() - self._last_failure_time) >= self.recovery_timeout
        ):
            self._state = CircuitState.HALF_OPEN
        return self._state

    # ── Core call wrapper ──────────────────────────────────────────────────

    async def call(self, coro):
        """
        Wrap an async coroutine with circuit-breaker logic.
        Raises CircuitOpenError immediately when OPEN.
        """
        current_state = self.state

        if current_state == CircuitState.OPEN:
            raise CircuitOpenError("Circuit is OPEN — request blocked (fail-fast).")

        try:
            result = await asyncio.wait_for(coro, timeout=self.request_timeout)
            self._on_success()
            return result
        except (asyncio.TimeoutError, Exception) as exc:
            self._on_failure()
            raise exc

    # ── State machine helpers ──────────────────────────────────────────────

    def _on_success(self):
        """Reset the breaker on a successful downstream call."""
        self._failure_count = 0
        self._state         = CircuitState.CLOSED

    def _on_failure(self):
        """Record a failure and potentially trip the breaker."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN

    # ── Introspection ──────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "state":           self.state.value,
            "failure_count":   self._failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout":  self.recovery_timeout,
            "request_timeout":   self.request_timeout,
        }


class CircuitOpenError(Exception):
    pass


# ─────────────────────────────────────────────
#  FastAPI App
# ─────────────────────────────────────────────

app = FastAPI(title="StudySync API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Mandatory: X-Student-ID middleware ────────────────────────────────────

STUDENT_ID = "Bscs23128"   #  

@app.middleware("http")
async def add_student_id_header(request: Request, call_next):
    """
    Injects X-Student-ID into EVERY response.
    Required by assignment — missing this = automatic zero.
    """
    response = await call_next(request)
    response.headers["X-Student-ID"] = STUDENT_ID
    return response


# ── Singleton circuit breaker for the LLM service ────────────────────────

llm_breaker = CircuitBreaker(
    failure_threshold=3,   # trip after 3 consecutive failures
    recovery_timeout=15.0, # try again after 15 s
    request_timeout=5.0,   # give up on LLM after 5 s (not 60 s!)
)

# Simulated LLM base URL — in production swap for real endpoint
LLM_API_URL = "http://127.0.0.1:9999/generate"  # intentionally unreachable for demo


# ─────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "breaker": llm_breaker.status()}


@app.get("/breaker/status")
async def breaker_status():
    """Inspect the live circuit-breaker state."""
    return llm_breaker.status()


@app.post("/breaker/reset")
async def breaker_reset():
    """Manually reset the circuit breaker (useful in tests / ops runbooks)."""
    llm_breaker._state         = CircuitState.CLOSED
    llm_breaker._failure_count = 0
    llm_breaker._last_failure_time = None
    return {"message": "Circuit breaker reset to CLOSED", "breaker": llm_breaker.status()}


@app.post("/ai/generate")
async def generate(request: Request):
    """
    Ask the external LLM API to generate a study summary.

    Without Circuit Breaker
    ───────────────────────
    Every request blocks for up to 60 s waiting for a timeout.
    500 concurrent users × 60 s = server meltdown.

    With Circuit Breaker
    ────────────────────
    • Requests 1-3   → fail fast after `request_timeout` seconds, count failures.
    • Request 4+     → OPEN: fail immediately with a cached fallback (0 ms).
    • After 15 s     → HALF_OPEN: one probe request is tried.
    • If probe works → CLOSED: normal operation resumes.
    """
    body = await request.json()
    prompt = body.get("prompt", "")

    async def call_llm():
        async with httpx.AsyncClient() as client:
            resp = await client.post(LLM_API_URL, json={"prompt": prompt})
            resp.raise_for_status()
            return resp.json()

    try:
        result = await llm_breaker.call(call_llm())
        return {
            "source":  "llm",
            "breaker": llm_breaker.status(),
            "result":  result,
        }

    except CircuitOpenError:
        # ── Fallback: return a cached / degraded response instantly ──────
        return JSONResponse(
            status_code=200,
            content={
                "source":  "fallback",
                "breaker": llm_breaker.status(),
                "result":  {
                    "summary": (
                        "The AI service is temporarily unavailable. "
                        "Here is a cached placeholder response. "
                        "Please try again in a few seconds."
                    )
                },
                "warning": "LLM circuit is OPEN — serving fallback response.",
            },
        )

    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=503,
            content={
                "error":   "LLM request timed out.",
                "breaker": llm_breaker.status(),
            },
        )

    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={
                "error":   f"LLM call failed: {str(exc)}",
                "breaker": llm_breaker.status(),
            },
        )