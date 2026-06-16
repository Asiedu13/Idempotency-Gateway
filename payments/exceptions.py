class ProcessorError(Exception):
    """Raised by the stub PSP when downstream processing fails."""


class CircuitOpenError(Exception):
    """Raised when the breaker is OPEN and the cooldown has not elapsed."""

    def __init__(self, retry_after_seconds: int):
        super().__init__(f"Circuit open, retry in {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds
