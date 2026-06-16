from django.db import models
from decimal import Decimal
from django.core.validators import MinValueValidator, MaxValueValidator

class Transaction(models.Model):
    class PaymentMethods(models.TextChoices):
        CARD = "CARD", "card"
        BANK = "BANK", "bank transfer",
        MOMO = "MOMO", "momo"

    class SupportedCurrencies(models.TextChoices):
        GHS = "GHS", "GHS",

    class TransactionStatus(models.TextChoices):
        PENDING = "PENDING", "pending"
        COMPLETED = "COMPLETED", "completed",
        FAILED = "FAILED", "failed"

    amount = models.DecimalField("amount being paid", decimal_places=2, max_digits=10, validators=[MinValueValidator(Decimal(0.0)), MaxValueValidator(Decimal(25000.0))])
    phone_number = models.CharField("phone number of person paying", max_length=40)
    email = models.CharField("email of person paying", max_length=40)
    full_name = models.CharField("full name of person paying", max_length=50)
    payment_method = models.CharField(choices=PaymentMethods.choices, default=PaymentMethods.MOMO, max_length=10)
    currency = models.CharField(choices=SupportedCurrencies.choices, max_length=10, default=SupportedCurrencies.GHS)
    created_at = models.DateTimeField(auto_now_add=True)
    idempotency_key = models.CharField(max_length=20, unique=True, primary_key=True)
    payload_hash = models.CharField(max_length=100)
    status = models.CharField(choices=TransactionStatus.choices, default=TransactionStatus.COMPLETED, max_length=10)
