import typing
from collections.abc import Sequence
from typing import TypeGuard

from dagger.client.base import Type


@typing.runtime_checkable
class HasID(typing.Protocol):
    async def id(self) -> str: ...


IDType = Type
IDTypeSeq = Sequence[Type]


def is_id_type_subclass(v: object) -> TypeGuard[type[Type]]:
    """Check if a class is a client binding for an object with an ID."""
    return (
        isinstance(v, type) and issubclass(v, Type) and callable(getattr(v, "id", None))
    )


def is_id_type(v: object) -> TypeGuard[Type]:
    """Check if a value is a client binding for an object with an ID."""
    return isinstance(v, Type) and callable(getattr(v, "id", None))


def is_id_type_sequence(v: object) -> TypeGuard[Sequence[Type]]:
    """Check if a value is a sequence of client bindings with IDs."""
    return (
        isinstance(v, Sequence)
        and not isinstance(v, str)
        and all(is_id_type(x) for x in v)
    )


def type_error(method: str, param: str, value: object, expected: str) -> TypeError:
    """The error a generated method raises for an argument of the wrong type."""
    shown = repr(value)
    if len(shown) > 60:  # noqa: PLR2004
        shown = shown[:57] + "..."
    return TypeError(
        f"Method dagger.client.gen.{method}() parameter {param}={shown} "
        f"expected to be of type {expected}."
    )
