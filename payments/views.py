from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from .serializers import TransactionSerializer
from .models import Transaction
from .utils import get_payload_hash
import time

# Create your views here.
class TransactionsView(generics.CreateAPIView):
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
                    status=status.HTTP_409_CONFLICT  # or 422
                )
            # Repeated requests
            serializer = self.get_serializer(transaction)
            response = Response(serializer.data, status=201)
            response['X-Cache-Hit'] = True
            return response
        return super().post(request, *args, **kwargs)

    def perform_create(self, serializer):
        time.sleep(2)
        serializer.save(idempotency_key=self.idempotency_key, payload_hash=self.payload_hash)
