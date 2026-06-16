from rest_framework import generics, status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response

from . import circuit_breaker, processor
from .exceptions import CircuitOpenError, ProcessorError
from .models import Transaction
from .serializers import TransactionSerializer
from .utils import get_payload_hash


class ProcessorUnavailable(APIException):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_detail = "Payment processor error."
    default_code = "processor_error"


class TransactionsViewV1(generics.CreateAPIView):
    serializer_class = TransactionSerializer

    def initial(self, request, *args, **kwargs):
        idem_key = request.headers.get('Idempotency-Key')
        if not idem_key:
            raise ValidationError({
                "Error": "Missing Idempotency-Key"
            })
        return super().initial(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        idem_key = request.headers.get('Idempotency-Key')
        payload_hash = get_payload_hash(request.data)
        self.idempotency_key = idem_key
        self.payload_hash = payload_hash

        transaction = Transaction.objects.filter(idempotency_key=idem_key).first()
        if transaction:
            # Check for fraudulent requests
            if transaction.payload_hash != payload_hash:
                return Response(
                    {
                        "error": "Idempotency key already used for a different request body."
                    },
                    status=status.HTTP_409_CONFLICT
                )
            # Repeated requests
            serializer = self.get_serializer(transaction)
            response = Response(serializer.data, status=201)
            response['X-Cache-Hit'] = True
            return response

        try:
            circuit_breaker.check_or_raise()
        except CircuitOpenError as exc:
            response = Response(
                {
                    "error": "Service temporarily unavailable.",
                    "retry_after": exc.retry_after_seconds,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
            response['Retry-After'] = exc.retry_after_seconds
            return response

        return super().post(request, *args, **kwargs)

    def perform_create(self, serializer):
        transaction = serializer.save(
            idempotency_key=self.idempotency_key,
            payload_hash=self.payload_hash,
            status=Transaction.TransactionStatus.PENDING,
        )
        try:
            processor.process_payment(transaction)
        except ProcessorError:
            transaction.status = Transaction.TransactionStatus.FAILED
            transaction.save(update_fields=["status"])
            circuit_breaker.record_failure()
            raise ProcessorUnavailable()
        except Exception:
            transaction.status = Transaction.TransactionStatus.FAILED
            transaction.save(update_fields=["status"])
            circuit_breaker.record_failure()
            raise

        transaction.status = Transaction.TransactionStatus.COMPLETED
        transaction.save(update_fields=["status"])
        circuit_breaker.record_success()
