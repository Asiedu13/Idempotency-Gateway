import random
import time
from django.conf import settings
from .exceptions import ProcessorError


def process_payment(transaction):
    time.sleep(2.00)
    failure_rate = getattr(settings, "PSP_STUB_FAILURE_RATE", 0.0)
    if failure_rate and random.random() < failure_rate:
        raise ProcessorError("Stub PSP failed")
