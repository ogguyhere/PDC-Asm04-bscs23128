# PDC-Sp24-[Bscs23128]-[Khadijah]

**Course:** Parallel and Distributed Computing (PDC) — Assignment 4
**Topic:** Building Resilient Distributed Systems
**Part 3 Choice:** Fault Tolerance — Circuit Breaker Pattern

---

## What This Implements

The app is a minimal FastAPI backend that reproduces the **StudySync** scenario from the assignment. Part 3 implements a full **Circuit Breaker** with three states (CLOSED → OPEN → HALF-OPEN) to protect against a slow/failing external LLM API.

Every API response includes the mandatory `X-Student-ID` header via FastAPI middleware.

---

## Project Structure

```
PDC-Asm04-bscs23128
├── app/
│   └── main.py          ← FastAPI app + Circuit Breaker + middleware
├── tests/
│   └── test_circuit_breaker.py  ← 10 tests proving before/after behavior
├── REPORT.md            ← Parts 1 & 2 (convert to PDF before submitting)
├── requirements.txt
└── README.md            ← This file
```

---

## How to Run

### 1. Clone and install

```bash
git clone https://github.com/[Your-Username]/PDC-Sp24-[Your-ID]-[Your-LastName].git](https://github.com/ogguyhere/PDC-Asm04-bscs23128.git
cd PDC-Asm04-bscs23128

pip install -r requirements.txt
```

### 2. Update your Student ID

Open `app/main.py` and replace the placeholder on line:

```python
STUDENT_ID = "Bscs23128"   # ← replace this
```

### 3. Start the server

```bash
uvicorn app.main:app --reload --port 8000
```

### 4. Test the API manually

```bash
# Health check — confirms X-Student-ID header is present
curl -i http://localhost:8000/health

# Inspect circuit breaker state
curl http://localhost:8000/breaker/status

# Hit the LLM endpoint (LLM URL is unreachable → trips breaker after 3 calls)
curl -X POST http://localhost:8000/ai/generate \
     -H "Content-Type: application/json" \
     -d '{"prompt": "Summarize Newton'\''s laws"}'

# After 3 failures the breaker opens — this call returns fallback instantly:
curl -X POST http://localhost:8000/ai/generate \
     -H "Content-Type: application/json" \
     -d '{"prompt": "anything"}'

# Reset the breaker
curl -X POST http://localhost:8000/breaker/reset
```

---

## How to Run Tests

```bash
pytest tests/test_circuit_breaker.py -v
```

**Output — all 10 tests pass:**

```
collected 10 items                                                                                                                                                                                       

tests/test_circuit_breaker.py::test_student_id_header_present PASSED                                                                                                                               [ 10%]
tests/test_circuit_breaker.py::test_without_breaker_requests_block PASSED                                                                                                                          [ 20%]
tests/test_circuit_breaker.py::test_breaker_trips_after_threshold PASSED                                                                                                                           [ 30%]
tests/test_circuit_breaker.py::test_open_breaker_fails_fast PASSED                                                                                                                                 [ 40%]
tests/test_circuit_breaker.py::test_breaker_recovers_to_half_open PASSED                                                                                                                           [ 50%]
tests/test_circuit_breaker.py::test_successful_probe_closes_breaker PASSED                                                                                                                         [ 60%]
tests/test_circuit_breaker.py::test_api_returns_fallback_when_open PASSED                                                                                                                          [ 70%]
tests/test_circuit_breaker.py::test_api_trips_breaker_on_repeated_failure PASSED                                                                                                                   [ 80%]
tests/test_circuit_breaker.py::test_breaker_status_endpoint PASSED                                                                                                                                 [ 90%]
tests/test_circuit_breaker.py::test_breaker_reset_endpoint PASSED                                                                                                                                  [100%]

============================================================================================ warnings summary ============================================================================================
tests/test_circuit_breaker.py::test_open_breaker_fails_fast
  /home/kaysaurus/Documents/Workspace S-26/PDC/Asm 04/PDC-Asm04-bscs23128/tests/test_circuit_breaker.py:131: RuntimeWarning: coroutine 'test_open_breaker_fails_fast.<locals>.fail_once' was never awaited
    with pytest.raises(CircuitOpenError):
  Enable tracemalloc to get traceback where the object was allocated.
  See https://docs.pytest.org/en/stable/how-to/capture-warnings.html#resource-warnings for more info.

tests/test_circuit_breaker.py::test_api_returns_fallback_when_open
tests/test_circuit_breaker.py::test_api_trips_breaker_on_repeated_failure
  /home/kaysaurus/Documents/Workspace S-26/PDC/Asm 04/PDC-Asm04-bscs23128/tests/../app/main.py:218: RuntimeWarning: coroutine 'generate.<locals>.call_llm' was never awaited
    return JSONResponse(
  Enable tracemalloc to get traceback where the object was allocated.
  See https://docs.pytest.org/en/stable/how-to/capture-warnings.html#resource-warnings for more info.

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
===================================================================================== 10 passed, 3 warnings in 1.96s =====================================================================================
[kaysaurus@archlinux PDC-Asm04-bscs23128]$ 
```

---

## What the Tests Prove

| Test | What it demonstrates |
|------|----------------------|
| `test_student_id_header_present` | Mandatory `X-Student-ID` header is on every response |
| `test_without_breaker_requests_block` | **BEFORE fix:** caller blocks for full timeout duration |
| `test_breaker_trips_after_threshold` | Breaker opens after N consecutive failures |
| `test_open_breaker_fails_fast` | **AFTER fix:** OPEN breaker returns in <50ms (vs 60,000ms) |
| `test_breaker_recovers_to_half_open` | OPEN → HALF-OPEN transition after recovery timeout |
| `test_successful_probe_closes_breaker` | Successful probe resets breaker to CLOSED |
| `test_api_returns_fallback_when_open` | `/ai/generate` serves cached fallback instantly |
| `test_api_trips_breaker_on_repeated_failure` | Real endpoint trips breaker on dead LLM URL |
| `test_breaker_status_endpoint` | `/breaker/status` exposes internal state |
| `test_breaker_reset_endpoint` | `/breaker/reset` clears open circuit |

---

## Circuit Breaker — State Machine

```
         ┌──────────────────────────────────────┐
         │                                      │
    CLOSED ──(N failures)──► OPEN ──(timeout)──► HALF-OPEN
         ▲                                          │
         └──────────(probe success)─────────────────┘
```

- **CLOSED** → normal operation, failures counted
- **OPEN** → fail-fast, serve fallback immediately (no blocking)
- **HALF-OPEN** → one probe allowed; success resets, failure re-opens

---

## Demo Video Outline (≤2 min)

1. **(0:00–0:30)** Show server running. Hit `/ai/generate` 3 times without the breaker logic — demonstrate blocking behavior (each call waits for timeout).
2. **(0:30–1:00)** Run `pytest -v` — show all 10 tests passing, highlight `test_without_breaker_requests_block` vs `test_open_breaker_fails_fast`.
3. **(1:00–1:30)** Live demo: hit `/ai/generate` repeatedly, watch `curl -i` responses — see `source: fallback` and `X-Student-ID` header appear after breaker opens.
4. **(1:30–2:00)** Call `/breaker/reset`, show breaker returns to CLOSED, explain HALF-OPEN recovery probe.
