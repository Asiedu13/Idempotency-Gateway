from datetime import timedelta
from django.conf import settings
from django.db import transaction as db_transaction
from django.utils import timezone
from .exceptions import CircuitOpenError
from .models import CircuitBreakerState


def _config():
    return settings.CIRCUIT_BREAKER


def _maybe_roll_window(breaker, now):
    window = timedelta(seconds=_config()["WINDOW_SECONDS"])
    if now - breaker.window_start >= window:
        breaker.window_start = now
        breaker.success_count = 0
        breaker.failure_count = 0


def _evaluate_threshold(breaker):
    cfg = _config()
    total = breaker.success_count + breaker.failure_count
    if total < cfg["MIN_SAMPLES"]:
        return False
    return (breaker.failure_count / total) >= cfg["FAILURE_THRESHOLD"]


def _trip_open(breaker, now):
    breaker.state = CircuitBreakerState.State.OPEN
    breaker.opened_at = now


def _close(breaker, now):
    breaker.state = CircuitBreakerState.State.CLOSED
    breaker.opened_at = None
    breaker.window_start = now
    breaker.success_count = 0
    breaker.failure_count = 0


def check_or_raise():
    CircuitBreakerState.get_instance()
    now = timezone.now()
    cooldown = timedelta(seconds=_config()["COOLDOWN_SECONDS"])

    with db_transaction.atomic():
        breaker = CircuitBreakerState.objects.select_for_update().get(
            pk=CircuitBreakerState.SINGLETON_PK
        )

        if breaker.state == CircuitBreakerState.State.OPEN:
            elapsed = now - (breaker.opened_at or now)
            if elapsed < cooldown:
                retry_after = max(1, int((cooldown - elapsed).total_seconds()))
                raise CircuitOpenError(retry_after)
            breaker.state = CircuitBreakerState.State.HALF_OPEN
            breaker.window_start = now
            breaker.success_count = 0
            breaker.failure_count = 0
            breaker.save(update_fields=["state", "window_start", "success_count", "failure_count"])


def record_success():
    CircuitBreakerState.get_instance()
    now = timezone.now()

    with db_transaction.atomic():
        breaker = CircuitBreakerState.objects.select_for_update().get(
            pk=CircuitBreakerState.SINGLETON_PK
        )

        if breaker.state == CircuitBreakerState.State.HALF_OPEN:
            _close(breaker, now)
        else:
            _maybe_roll_window(breaker, now)
            breaker.success_count += 1

        breaker.save()


def record_failure():
    CircuitBreakerState.get_instance()
    now = timezone.now()

    with db_transaction.atomic():
        breaker = CircuitBreakerState.objects.select_for_update().get(
            pk=CircuitBreakerState.SINGLETON_PK
        )

        if breaker.state == CircuitBreakerState.State.HALF_OPEN:
            _trip_open(breaker, now)
        else:
            _maybe_roll_window(breaker, now)
            breaker.failure_count += 1
            if breaker.state == CircuitBreakerState.State.CLOSED and _evaluate_threshold(breaker):
                _trip_open(breaker, now)

        breaker.save()
