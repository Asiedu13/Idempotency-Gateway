# Circuit Breaker

The `payments` app wraps every call to the downstream payment processor in a circuit breaker. When the failure rate spikes past a configured threshold, the breaker **trips OPEN** and the API stops accepting new processing requests until a cooldown elapses and a probe confirms recovery.

This document explains how the pieces fit together so an operator or new contributor can reason about the behavior without re-reading the source.

## Why

`POST /api/v1/process-payment` ultimately calls a downstream PSP. If that PSP is unhealthy, naively forwarding every request:

- piles up `FAILED` transactions in the DB,
- holds connections / workers blocked on slow-failing calls,
- amplifies a partial outage into total downtime.

The breaker auto-pauses new processing during an incident, fails fast with a clear `503 + Retry-After`, and probes once per cooldown to detect recovery without flooding the dependency.

## State machine

```
            failures cross
            threshold (>= MIN_SAMPLES)
   ┌─────────┐ ─────────────────────────► ┌────────┐
   │ CLOSED  │                            │  OPEN  │
   │ (normal)│ ◄───── success in ──────── │(reject)│
   └─────────┘        HALF_OPEN           └────────┘
        ▲                                      │
        │                                      │ cooldown
        │ success in                           │ elapsed
        │ HALF_OPEN                            ▼
        │                                ┌───────────┐
        └─────────── failure ──────────  │ HALF_OPEN │
                     in HALF_OPEN        │  (probe)  │
                     re-opens            └───────────┘
```

| From      | Trigger                                                     | To        |
|-----------|-------------------------------------------------------------|-----------|
| CLOSED    | `record_failure` and `failures/total >= FAILURE_THRESHOLD` with `total >= MIN_SAMPLES` | OPEN      |
| OPEN      | `check_or_raise` while `now < opened_at + COOLDOWN_SECONDS` | OPEN (raises `CircuitOpenError`) |
| OPEN      | `check_or_raise` after cooldown elapsed                     | HALF_OPEN |
| HALF_OPEN | `record_success`                                            | CLOSED    |
| HALF_OPEN | `record_failure`                                            | OPEN      |

## Configuration

Set in `finsafe_psp/settings.py`:

```python
CIRCUIT_BREAKER = {
    "FAILURE_THRESHOLD": 0.5,   # ratio of failures/total in the current window
    "MIN_SAMPLES": 10,          # need at least this many outcomes before evaluating
    "WINDOW_SECONDS": 60,       # counters reset when this elapses since window_start
    "COOLDOWN_SECONDS": 30,     # how long OPEN must persist before a HALF_OPEN probe is allowed
}

PSP_STUB_FAILURE_RATE = 0.0     # 0.0 = stub PSP always succeeds, 1.0 = always fails
```

`PSP_STUB_FAILURE_RATE` only affects `payments/processor.py` — a placeholder for the real PSP integration. Bump it to exercise the breaker locally.

## Components

| File | Responsibility |
|------|----------------|
| `payments/models.py` — `CircuitBreakerState` | Persistent single-row record (`pk=1`) holding `state`, `opened_at`, `window_start`, `success_count`, `failure_count`. `get_instance()` is a `get_or_create` helper so first access auto-creates the row. |
| `payments/exceptions.py` | `ProcessorError` (downstream call failed) and `CircuitOpenError(retry_after_seconds)` (breaker rejected this request). |
| `payments/processor.py` | Stub PSP. `process_payment(transaction)` simulates a call and may raise `ProcessorError` based on `PSP_STUB_FAILURE_RATE`. Replace this with a real PSP integration; the breaker contract stays the same. |
| `payments/circuit_breaker.py` | The three public functions the view calls: `check_or_raise()`, `record_success()`, `record_failure()`. |
| `payments/views.py` — `TransactionsViewV1` | Wires the breaker into the request flow (see below). |
| `payments/admin.py` | Registers `CircuitBreakerState` so ops can view counters and manually reset `state`. |

## Request flow

```
POST /api/v1/process-payment
        │
        ▼
initial(): Idempotency-Key header present?     ── no ──► 400
        │ yes
        ▼
post(): Transaction with this idempotency_key?
        │
        ├─ yes, payload_hash differs   ─────────────────► 409  (fraudulent reuse)
        ├─ yes, payload_hash matches   ─────────────────► 201 + X-Cache-Hit: True   ◄── breaker NOT consulted
        │                                                                              (replays don't touch the PSP)
        ▼ no existing row
circuit_breaker.check_or_raise()
        │
        ├─ OPEN, cooldown not elapsed  ─────────────────► 503 + Retry-After
        ├─ OPEN, cooldown elapsed      ── transition ──► HALF_OPEN, allow probe
        ▼ CLOSED or HALF_OPEN
super().post() → perform_create()
        │
        ▼
save Transaction(status=PENDING)
        │
        ▼
processor.process_payment(transaction)
        │
        ├─ ProcessorError             ─► status=FAILED, record_failure(), raise 502
        ├─ any other Exception        ─► status=FAILED, record_failure(), re-raise (500)
        └─ success                    ─► status=COMPLETED, record_success(), return 201
```

The breaker check sits **after** the idempotency cache hit so successful retries of an already-completed transaction keep returning the cached response even while the breaker is open — the cache hit doesn't generate downstream load, so blocking it would be pointless customer pain.

## Counter window

`success_count` and `failure_count` accumulate against a window starting at `window_start`. On every `record_*` call, `_maybe_roll_window` checks whether `now - window_start >= WINDOW_SECONDS`; if so it resets both counters and advances `window_start`. This is a tumbling (not sliding) window — simpler and good enough for outage detection at the timescales we care about.

The threshold only fires once `success_count + failure_count >= MIN_SAMPLES`, so a single failure in an otherwise-idle window cannot trip the breaker.

## Concurrency

Every `check_or_raise`, `record_success`, and `record_failure` runs inside `transaction.atomic()` and pulls the singleton row with `select_for_update()`. Two workers racing on a near-threshold failure cannot both increment-and-decide independently; one waits for the other's lock. On SQLite (dev) `select_for_update` is a no-op but the surrounding transaction still serializes writes; on Postgres (prod) the row lock is real.

## HTTP responses

| Scenario | Status | Body | Headers |
|----------|--------|------|---------|
| Breaker OPEN | `503 Service Unavailable` | `{"error": "Service temporarily unavailable.", "retry_after": <int seconds>}` | `Retry-After: <int>` |
| Processor failed (breaker still CLOSED for this request) | `502 Bad Gateway` | `{"detail": "Payment processor error."}` (DRF `APIException`) | — |
| Success | `201 Created` | serialized `Transaction` | (no `X-Cache-Hit`) |
| Idempotent replay | `201 Created` | serialized `Transaction` | `X-Cache-Hit: True` (bypasses breaker) |

## Operating

- **Inspect state**: Django admin → *Circuit breaker states* → row with `id=1`. Shows current `state`, `opened_at`, counters.
- **Manually reset**: edit the row in admin, set `state=CLOSED`, save. The next `record_*` call will reset the counters via the window-roll logic; you can also zero them directly via the admin (counter fields are read-only by default but easy to change in `payments/admin.py` if needed).
- **Force a trip in dev**: set `PSP_STUB_FAILURE_RATE = 1.0`, restart, send `MIN_SAMPLES` POSTs with different idempotency keys. Subsequent requests will get 503 until you flip the knob back and either wait `COOLDOWN_SECONDS` or reset the row manually.

## Testing

`payments/tests.py` contains:

- `TransactionsViewV1Tests` — pre-existing idempotency coverage (5 tests).
- `CircuitBreakerTests` (6 tests):
  - `test_breaker_opens_after_failure_threshold`
  - `test_open_response_has_retry_after_header`
  - `test_cached_response_bypasses_open_breaker`
  - `test_cooldown_elapses_then_half_open_success_closes_breaker`
  - `test_half_open_failure_reopens_breaker`
  - `test_view_exception_counts_as_failure`

The tests patch `payments.processor.process_payment` directly to deterministically drive success/failure outcomes, and patch `payments.circuit_breaker.timezone.now` to fast-forward past the cooldown without sleeping. Run with:

```
python manage.py test payments
```

## Extending

- **Real PSP**: replace the body of `processor.process_payment` with the actual call. Raise `ProcessorError` (or let exceptions propagate) on failure; the view + breaker do the rest.
- **Per-method or per-currency breakers**: change `CircuitBreakerState` from a singleton to a row keyed by `(payment_method, currency)` and have the helpers accept a key. The state machine logic doesn't change.
- **Metrics**: hook `record_success` / `record_failure` to your metrics client to emit counters; the trip transition is a great spot to fire an alert.
