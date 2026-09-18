"""What a generated client hands the SDK: its target and its core digest."""

import contextlib
import contextvars
import dataclasses
import logging
import re
from collections.abc import Iterable, Iterator

from dagger._exceptions import QueryError, StaleClientError

logger = logging.getLogger(__name__)

GENERATE_HINT = "Run `dagger generate`."

# What the engine's validator answers when the schema lacks a field the
# bindings have. Anchored, because a resolver may quote the same words.
MISSING_FIELD = re.compile(
    r'^Cannot query field "[^"]*" on type "[^"]*"', re.IGNORECASE
)


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
    # Validation fails before anything runs, so it carries no path; an error
    # with one comes from a resolver, whatever its message says.
    missing = next(
        (e for e in error.errors if not e.path and MISSING_FIELD.match(e.message)),
        None,
    )
    if missing is None:
        return None
    names = ", ".join(sorted(t.name for t in targets))
    msg = f"{missing} The client for {names} is out of date. {GENERATE_HINT}"
    return StaleClientError(msg)
