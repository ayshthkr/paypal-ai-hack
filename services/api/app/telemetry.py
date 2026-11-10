"""Per-request timings shared by synchronous workers and the ASGI middleware."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import perf_counter

metrics: ContextVar[dict | None] = ContextVar("request_metrics", default=None)


@contextmanager
def measure(name):
    start = perf_counter()
    try:
        yield
    finally:
        timings = metrics.get()
        if timings is not None:
            timings[name] = timings.get(name, 0.0) + (perf_counter() - start) * 1000


def timed(name):
    def decorate(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            with measure(name):
                return function(*args, **kwargs)
        return wrapper
    return decorate
