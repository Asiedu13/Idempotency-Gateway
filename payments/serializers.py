from rest_framework import serializers
from .models import Transaction

class TransactionSerializer(serializers.ModelSerializer):

    class Meta:
        model = Transaction
        fields = ("amount", "phone_number", "full_name", "currency", "status")
