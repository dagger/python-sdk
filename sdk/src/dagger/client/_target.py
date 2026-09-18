"""What a generated client hands the SDK: its target and its core digest."""

import contextlib
import contextvars
import dataclasses
import logging
from collections.abc import Iterable, Iterator

from dagger._exceptions import QueryError, StaleClientError

logger = logging.getLogger(__name__)

GENERATE_HINT = "Run `dagger generate`."

# What the engine answers when the schema lacks a field the bindings have.
MISSING_FIELD = "cannot query field"


@dataclasses.dataclass(frozen=True, slots=True)
class Target:
    """The module a client was generated for."""

    name: str
    ref: str
    pin: str | None = None


_registering: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "registering", default=False
)


@contextlib.contextmanager
def registering_types() -> Iterator[None]:
    """Mark the window in which the SDK registers a module's types.

    A stale client only warns here: a module with a client to itself has
    to run before that client can be regenerated.
    """
    token = _registering.set(True)
    try:
        yield
    finally:
        _registering.reset(token)


def check_core(client: str, expected: str, installed: str) -> None:
    """Refuse a client generated against another core."""
    if expected == installed:
        return
    msg = (
        f"Client {client!r} was generated for core {expected}, "
        f"but the installed core is {installed}. {GENERATE_HINT}"
    )
    if _registering.get():
        logger.warning(msg)
        return
    raise StaleClientError(msg)


def stale_client_error(
    error: QueryError, targets: Iterable[Target]
) -> StaleClientError | None:
    """The error a missing field means once the module was loaded."""
    if MISSING_FIELD not in str(error).lower():
        return None
    names = ", ".join(sorted(t.name for t in targets))
    msg = f"{error} The client for {names} is out of date. {GENERATE_HINT}"
    return StaleClientError(msg)
