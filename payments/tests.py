from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from . import circuit_breaker
from .exceptions import ProcessorError
from .models import CircuitBreakerState, Transaction


class TransactionsViewV1Tests(APITestCase):
    url = "/api/v1/process-payment"

    def setUp(self):
        self.idem_key = "idem-key-001"
        self.payload = {
            "amount": "100.00",
            "phone_number": "0244000000",
            "email": "buyer@example.com",
            "full_name": "Buyer One",
            "payment_method": "MOMO",
            "currency": "GHS",
        }

    def _post(self, payload=None, idem_key=None):
        kwargs = {"format": "json"}
        if idem_key is not None:
            kwargs["HTTP_IDEMPOTENCY_KEY"] = idem_key
        return self.client.post(self.url, payload if payload is not None else self.payload, **kwargs)

    def test_missing_idempotency_key_returns_400(self):
        response = self._post()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Missing Idempotency-Key", str(response.data))
        self.assertEqual(Transaction.objects.count(), 0)

    def test_first_request_creates_transaction(self):
        response = self._post(idem_key=self.idem_key)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(Transaction.objects.get().idempotency_key, self.idem_key)
        self.assertNotIn("X-Cache-Hit", response)

    def test_repeated_request_returns_cached_response(self):
        first = self._post(idem_key=self.idem_key)
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        second = self._post(idem_key=self.idem_key)

        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second["X-Cache-Hit"], "True")
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(second.data, first.data)

    def test_fraudulent_request_different_payload_same_key_returns_409(self):
        first = self._post(idem_key=self.idem_key)
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        tampered = dict(self.payload, amount="999.00")
        response = self._post(payload=tampered, idem_key=self.idem_key)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            response.data,
            {"error": "Idempotency key already used for a different request body."},
        )
        self.assertEqual(Transaction.objects.count(), 1)

    def test_different_keys_with_same_payload_create_separate_transactions(self):
        first = self._post(idem_key="idem-key-aaa")
        second = self._post(idem_key="idem-key-bbb")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Transaction.objects.count(), 2)


def _payload(amount="100.00"):
    return {
        "amount": amount,
        "phone_number": "0244000000",
        "email": "buyer@example.com",
        "full_name": "Buyer One",
        "payment_method": "MOMO",
        "currency": "GHS",
    }


class CircuitBreakerTests(APITestCase):
    url = "/api/v1/process-payment"

    def _post(self, idem_key, payload=None):
        return self.client.post(
            self.url,
            payload or _payload(),
            format="json",
            HTTP_IDEMPOTENCY_KEY=idem_key,
        )

    def _drive_failures(self, count, start=0):
        with mock.patch(
            "payments.processor.process_payment",
            side_effect=ProcessorError("forced"),
        ):
            for i in range(count):
                self._post(idem_key=f"fail-{start + i}")

    def _drive_successes(self, count, start=0):
        with mock.patch("payments.processor.process_payment", return_value=None):
            for i in range(count):
                self._post(idem_key=f"ok-{start + i}")

    def test_breaker_opens_after_failure_threshold(self):
        cfg = settings.CIRCUIT_BREAKER
        self._drive_failures(cfg["MIN_SAMPLES"])

        breaker = CircuitBreakerState.get_instance()
        self.assertEqual(breaker.state, CircuitBreakerState.State.OPEN)

        with mock.patch("payments.processor.process_payment", return_value=None):
            response = self._post(idem_key="after-trip")

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(Transaction.objects.filter(idempotency_key="after-trip").count(), 0)

    def test_open_response_has_retry_after_header(self):
        self._drive_failures(settings.CIRCUIT_BREAKER["MIN_SAMPLES"])

        response = self._post(idem_key="rejected")

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn("Retry-After", response)
        self.assertGreaterEqual(int(response["Retry-After"]), 1)
        self.assertEqual(response.data["error"], "Service temporarily unavailable.")
        self.assertIn("retry_after", response.data)

    def test_cached_response_bypasses_open_breaker(self):
        with mock.patch("payments.processor.process_payment", return_value=None):
            first = self._post(idem_key="cached-key")
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        breaker = CircuitBreakerState.get_instance()
        breaker.state = CircuitBreakerState.State.OPEN
        breaker.opened_at = timezone.now()
        breaker.save()

        second = self._post(idem_key="cached-key")
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second["X-Cache-Hit"], "True")

    def test_cooldown_elapses_then_half_open_success_closes_breaker(self):
        self._drive_failures(settings.CIRCUIT_BREAKER["MIN_SAMPLES"])
        self.assertEqual(CircuitBreakerState.get_instance().state, CircuitBreakerState.State.OPEN)

        future = timezone.now() + timedelta(
            seconds=settings.CIRCUIT_BREAKER["COOLDOWN_SECONDS"] + 1
        )
        with mock.patch("payments.circuit_breaker.timezone.now", return_value=future), \
             mock.patch("payments.processor.process_payment", return_value=None):
            response = self._post(idem_key="probe-success")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(CircuitBreakerState.get_instance().state, CircuitBreakerState.State.CLOSED)

    def test_half_open_failure_reopens_breaker(self):
        self._drive_failures(settings.CIRCUIT_BREAKER["MIN_SAMPLES"])
        self.assertEqual(CircuitBreakerState.get_instance().state, CircuitBreakerState.State.OPEN)

        future = timezone.now() + timedelta(
            seconds=settings.CIRCUIT_BREAKER["COOLDOWN_SECONDS"] + 1
        )
        with mock.patch("payments.circuit_breaker.timezone.now", return_value=future), \
             mock.patch(
                 "payments.processor.process_payment",
                 side_effect=ProcessorError("probe failed"),
             ):
            response = self._post(idem_key="probe-fail")

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(CircuitBreakerState.get_instance().state, CircuitBreakerState.State.OPEN)

    def test_view_exception_counts_as_failure(self):
        with mock.patch(
            "payments.processor.process_payment",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                self._post(idem_key="boom-key")

        self.assertEqual(
            Transaction.objects.get(idempotency_key="boom-key").status,
            Transaction.TransactionStatus.FAILED,
        )
        self.assertEqual(CircuitBreakerState.get_instance().failure_count, 1)
