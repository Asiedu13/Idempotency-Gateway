from django.urls import path
from payments import views

urlpatterns = [
    path("process-payment", views.TransactionsView.as_view())
]