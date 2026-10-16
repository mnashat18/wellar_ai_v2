class WellarError(Exception):
    def __init__(self, code: str, message: str, status_code: int, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


class IdempotencyConflict(WellarError):
    def __init__(self):
        super().__init__("idempotency_conflict", "Idempotency key was already used with a different request", 409)


class QueueFull(WellarError):
    def __init__(self):
        super().__init__("queue_full", "Scan queue is at capacity", 503, True)
