"""Module type definitions as plain data.

Both ways of loading a module derive their type definitions from a
:class:`ModuleDescription`: the runtime materialises it into API calls when
the engine asks for the types, and the static entrypoint renders it to Dang
at generate time.
"""

from __future__ import annotations

import dataclasses
import enum
import inspect
from typing import Any

import dagger
from dagger.client._guards import is_id_type_subclass
from dagger.client.base import Scalar
from dagger.mod._utils import (
    get_doc,
    get_object_type,
    is_annotated,
    is_initvar,
    is_nullable,
    is_subclass,
    is_union,
    list_of,
    non_null,
    strip_annotations,
)

Kind = dagger.TypeDefKind

_BUILTINS: dict[Any, dagger.TypeDefKind] = {
    str: Kind.STRING_KIND,
    int: Kind.INTEGER_KIND,
    float: Kind.FLOAT_KIND,
    bool: Kind.BOOLEAN_KIND,
    type(None): Kind.VOID_KIND,
}


@dataclasses.dataclass(frozen=True, slots=True)
class TypeRef:
    """A reference to an API type."""

    kind: dagger.TypeDefKind
    name: str = ""
    description: str | None = None
    optional: bool = False
    elem: TypeRef | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ArgumentDescription:
    name: str
    type: TypeRef
    # The runtime marks a nullable argument optional a second time, on top
    # of the type reference; kept so both paths produce the same definition.
    nullable: bool = False
    description: str | None = None
    default_value: str | None = None
    default_path: str | None = None
    default_address: str | None = None
    ignore: tuple[str, ...] | None = None
    deprecated: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class FunctionDescription:
    name: str
    returns: TypeRef
    description: str | None = None
    cache: str | None = None
    deprecated: str | None = None
    check: bool = False
    generator: bool = False
    service: bool = False
    agent: bool = False
    args: tuple[ArgumentDescription, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class FieldDescription:
    name: str
    type: TypeRef
    description: str | None = None
    deprecated: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class EnumMemberDescription:
    name: str
    value: str
    description: str | None = None
    deprecated: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class EnumDescription:
    name: str
    description: str | None = None
    members: tuple[EnumMemberDescription, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class ObjectDescription:
    name: str
    interface: bool = False
    description: str | None = None
    deprecated: str | None = None
    fields: tuple[FieldDescription, ...] = ()
    functions: tuple[FunctionDescription, ...] = ()
    constructor: FunctionDescription | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ModuleDescription:
    main_object: str
    description: str | None = None
    objects: tuple[ObjectDescription, ...] = ()
    enums: tuple[EnumDescription, ...] = ()


def describe_type(  # noqa: C901, PLR0911
    annotation: Any,
    context: str = "type",
) -> TypeRef:
    """Describe a Python annotation as an API type reference."""
    if is_initvar(annotation):
        return describe_type(annotation.type, context)

    if is_annotated(annotation):
        return describe_type(strip_annotations(annotation), context)

    typ = type(None) if annotation is None else annotation
    error_msg = f"unsupported {context}: {typ!r}"

    optional = is_nullable(typ)
    typ = non_null(typ)

    # Can't represent unions in the API.
    if is_union(typ):
        raise TypeError(error_msg)

    if typ in _BUILTINS:
        return TypeRef(_BUILTINS[typ], optional=optional)

    if el := list_of(typ):
        return TypeRef(Kind.LIST_KIND, optional=optional, elem=describe_type(el))

    if inspect.isclass(cls := typ):
        name = cls.__name__

        if is_subclass(cls, enum.Enum):
            return TypeRef(Kind.ENUM_KIND, name, get_doc(cls), optional)

        if is_subclass(cls, Scalar):
            return TypeRef(Kind.SCALAR_KIND, name, get_doc(cls), optional)

        # object defined in this module
        if obj_type := get_object_type(cls):
            kind = Kind.INTERFACE_KIND if obj_type.interface else Kind.OBJECT_KIND
            return TypeRef(kind, name, optional=optional)

        # object type from API (codegen)
        if is_id_type_subclass(cls):
            return TypeRef(Kind.OBJECT_KIND, name, optional=optional)

    raise TypeError(error_msg)
