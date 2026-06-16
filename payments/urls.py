from django.urls import path
from payments import views

urlpatterns = [
    path("api/v1/process-payment", views.TransactionsViewV1.as_view())
]