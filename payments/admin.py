from django.contrib import admin

from .models import CircuitBreakerState, Transaction


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("idempotency_key", "amount", "currency", "status", "created_at")
    list_filter = ("status", "payment_method", "currency")
    search_fields = ("idempotency_key", "email", "phone_number")


@admin.register(CircuitBreakerState)
class CircuitBreakerStateAdmin(admin.ModelAdmin):
    list_display = ("id", "state", "opened_at", "window_start", "success_count", "failure_count")
    readonly_fields = ("success_count", "failure_count", "window_start", "opened_at")
    fields = ("state", "opened_at", "window_start", "success_count", "failure_count")
