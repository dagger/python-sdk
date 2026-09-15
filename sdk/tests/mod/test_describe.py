import enum
import typing
from dataclasses import InitVar
from typing import Annotated

import pytest
from typing_extensions import Doc, Self

import dagger
from dagger import DefaultPath, Ignore, Name, dag
from dagger.mod import Module
from dagger.mod._converter import to_typedef, typedef_from
from dagger.mod._describe import TypeRef, describe_type
from dagger.mod._module import _module_from

Kind = dagger.TypeDefKind


class Color(enum.Enum):
    """A color."""

    RED = "red"
    """The red one."""

    BLUE = "blue"


@pytest.fixture
def mod() -> Module:
    m = Module("Main")
    m.enum_type(Color)

    @m.interface
    class Greeter(typing.Protocol):
        @m.function
        def greet(self, name: str) -> str: ...

    @m.object_type
    class Helper:
        """A helper."""

        level: Color = m.field()

    @m.object_type
    class Main:
        """The main object."""

        source: dagger.Directory
        greeting: str = m.field(default="hello")
        count: Annotated[int, Doc("How many")] = m.field(default=1, name="howMany")
        extra: InitVar[str] = ""

        @m.function
        def container(self, base: Annotated[str, Name("from")] = "alpine") -> str:
            """A container."""
            return base

        @m.function(name="shout", doc="Shout it", cache="never")
        async def loud(self, who: str | None = None, times: int = 1) -> str:
            return (who or "nobody") * times

        @m.function
        @m.check
        def lint(self) -> None: ...

        @m.function
        def helpers(
            self, src: Annotated[dagger.Directory, DefaultPath("."), Ignore([".venv"])]
        ) -> list[Helper]: ...

        @m.function
        def mob(self) -> list[Self]: ...

        @m.function
        def maybe(self) -> list[str] | None: ...

        @m.function
        def greeter(self, g: Greeter) -> Greeter: ...

        @m.function(deprecated="use shout")
        def old(self, p: dagger.Platform) -> dagger.JSON: ...

        make_helper = m.function()(Helper)

    return m


def test_objects_and_enums(mod: Module):
    desc = mod.describe()

    assert desc.main_object == "Main"
    assert [o.name for o in desc.objects] == ["Greeter", "Helper", "Main"]
    assert [e.name for e in desc.enums] == ["Color"]

    greeter, helper, main = desc.objects
    assert greeter.interface is True
    assert greeter.constructor is None
    assert [f.name for f in greeter.functions] == ["greet"]

    assert helper.description == "A helper."
    assert helper.fields[0].type == TypeRef(Kind.ENUM_KIND, "Color", "A color.")
    assert helper.constructor is None

    assert main.description == "The main object."
    assert [f.name for f in main.functions] == [
        "make_helper",
        "container",
        "greeter",
        "helpers",
        "lint",
        "shout",
        "maybe",
        "mob",
        "old",
    ]


def test_fields(mod: Module):
    main = mod.describe().objects[2]

    assert [(f.name, f.type.kind) for f in main.fields] == [
        ("greeting", Kind.STRING_KIND),
        ("howMany", Kind.INTEGER_KIND),
    ]
    assert main.fields[1].description == "How many"


def test_constructor(mod: Module):
    ctor = mod.describe().objects[2].constructor

    assert ctor is not None
    assert ctor.name == ""
    assert ctor.returns == TypeRef(Kind.OBJECT_KIND, "Main")
    assert [a.name for a in ctor.args] == ["source", "greeting", "count", "extra"]
    assert ctor.args[0].type == TypeRef(Kind.OBJECT_KIND, "Directory")
    assert ctor.args[1].default_value == '"hello"'
    assert ctor.args[2].description == "How many"
    assert ctor.args[3].default_value == '""'


def test_function_metadata(mod: Module):
    functions = {f.name: f for f in mod.describe().objects[2].functions}

    shout = functions["shout"]
    assert shout.description == "Shout it"
    assert shout.cache == "never"
    assert shout.args[0].nullable is True
    assert shout.args[0].type == TypeRef(Kind.STRING_KIND, optional=True)
    assert shout.args[0].default_value == "null"
    assert shout.args[1].default_value == "1"

    assert functions["lint"].check is True
    assert functions["lint"].returns == TypeRef(Kind.VOID_KIND, optional=True)

    src = functions["helpers"].args[0]
    assert src.default_path == "."
    assert src.ignore == (".venv",)
    assert functions["helpers"].returns == TypeRef(
        Kind.LIST_KIND, elem=TypeRef(Kind.OBJECT_KIND, "Helper")
    )

    assert functions["mob"].returns == TypeRef(
        Kind.LIST_KIND, elem=TypeRef(Kind.OBJECT_KIND, "Main")
    )
    assert functions["maybe"].returns == TypeRef(
        Kind.LIST_KIND, optional=True, elem=TypeRef(Kind.STRING_KIND)
    )
    assert functions["greeter"].returns == TypeRef(Kind.INTERFACE_KIND, "Greeter")
    assert functions["container"].args[0].name == "from"

    old = functions["old"]
    assert old.deprecated == "use shout"
    assert old.args[0].type.kind == Kind.SCALAR_KIND
    assert old.args[0].type.name == "Platform"
    assert old.returns.kind == Kind.SCALAR_KIND

    make_helper = functions["make_helper"]
    assert make_helper.description == "A helper."
    assert [a.name for a in make_helper.args] == ["level"]


def test_enum_members(mod: Module):
    color = mod.describe().enums[0]

    assert color.description == "A color."
    assert [(m.name, m.value, m.description) for m in color.members] == [
        ("RED", "red", "The red one."),
        ("BLUE", "blue", None),
    ]


def test_typedef_from_matches_to_typedef():
    for annotation in (str, list[str] | None, list[list[str] | None], None):
        assert typedef_from(describe_type(annotation)) == to_typedef(annotation)


def test_unsupported_type():
    with pytest.raises(TypeError, match=r"unsupported type: int \| str"):
        describe_type(int | str)


def test_module_materialisation():
    mod = Module("Foo")

    @mod.object_type
    class Foo:
        """Foo doc."""

        name: str = mod.field(default="foo")

        @mod.function
        def hello(self, who: str | None = None) -> str:
            """Say hello."""
            return who or self.name

    string = dag.type_def().with_kind(Kind.STRING_KIND)
    expected = dag.module().with_object(
        dag.type_def()
        .with_object("Foo", description="Foo doc.", deprecated=None)
        .with_field("name", string, description=None, deprecated=None)
        .with_function(
            dag.function("hello", string)
            .with_description("Say hello.")
            .with_arg(
                "who",
                dag.type_def()
                .with_optional(True)
                .with_kind(Kind.STRING_KIND)
                .with_optional(True),
                description=None,
                default_value=dagger.JSON("null"),
                default_path=None,
                default_address=None,
                ignore=None,
                deprecated=None,
            )
        )
        .with_constructor(
            dag.function("", dag.type_def().with_object("Foo"))
            .with_description("Foo doc.")
            .with_arg(
                "name",
                string,
                description=None,
                default_value=dagger.JSON('"foo"'),
                default_path=None,
                default_address=None,
                ignore=None,
                deprecated=None,
            )
        )
    )

    desc = mod.describe()
    assert desc.description is None
    assert _module_from(desc) == expected
