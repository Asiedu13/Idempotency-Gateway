from unittest import mock

from rest_framework import status
from rest_framework.test import APITestCase

from .models import Transaction


@mock.patch("payments.views.time.sleep", lambda *_a, **_kw: None)
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
