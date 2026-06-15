from django.shortcuts import render
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from .serializers import TransactionSerializer
from .models import Transaction
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

        if not idem_key:
            return Response({
                "Error": "Missing Idempotency-Key"
            }, status=400)

        transaction = Transaction.objects.filter(idempotency_key=idem_key).first()
        if transaction:
            serializer = self.get_serializer(transaction)
            response = Response(serializer.data, status=201)
            response['X-Cache-Hit'] = True
            return response
        return super().post(request, *args, **kwargs)

    def perform_create(self, serializer):
        time.sleep(2)
        serializer.save(idempotency_key=self.request.idempotency_key)
