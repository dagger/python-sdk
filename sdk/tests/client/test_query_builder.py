import enum
from dataclasses import dataclass

import pytest

from dagger.client._core import Arg, Context, snake_to_camel, to_literal
from dagger.client.base import Enum, Input, Scalar, Type


class Color(Enum):
    RED = "RED"


class Platform(Scalar):
    pass


@dataclass(slots=True)
class Block(Input):
    kind: str
    call_id: str | None = None
    ssh_url: str | None = None

    _graphql_names = (("ssh_url", "sshURL"),)


@dataclass(slots=True)
class Message(Input):
    kind: str
    text: str | None = ""
    errored: bool | None = False


class Container(Type):
    def echo(self, args: list[str]) -> "Container":
        return Container(self._select("echo", [Arg("args", args)]))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "null"),
        (True, "true"),
        (False, "false"),
        (1, "1"),
        (1.5, "1.5"),
        ('a "quoted" line\n', '"a \\"quoted\\" line\\n"'),
        (Platform("linux/amd64"), '"linux/amd64"'),
        (Color.RED, "RED"),
        (["a", 1, None], '["a", 1, null]'),
        ({"name": "x", "value": 2}, '{name: "x", value: 2}'),
        (Block(kind="text", call_id="c1"), '{kind: "text", callId: "c1"}'),
        (Block(kind="text", ssh_url="u"), '{kind: "text", sshURL: "u"}'),
    ],
)
def test_to_literal(value, expected):
    assert to_literal(value) == expected


def test_input_literal_omits_defaults_but_sends_explicit_none():
    assert to_literal(Message(kind="text")) == '{kind: "text"}'
    assert to_literal(Message(kind="text", text=None)) == '{kind: "text", text: null}'
    assert (
        to_literal(Message(kind="text", errored=None))
        == '{kind: "text", errored: null}'
    )


def test_to_literal_rejects_unsupported():
    with pytest.raises(Exception, match="Cannot serialize"):
        to_literal(object())
    with pytest.raises(Exception, match="Cannot serialize"):
        to_literal(float("inf"))


def test_build_nested_selection():
    ctx = (
        Context()
        .root_select("container", [])
        .select("Container", "from", [Arg("address", "alpine:3.20")])
        .select("Container", "withExec", [Arg("args", ["echo", "hi"])])
        .select("Container", "stdout", [])
    )

    assert ctx.build() == (
        "query {\n"
        "  container {\n"
        '    from(address: "alpine:3.20") {\n'
        '      withExec(args: ["echo", "hi"]) {\n'
        "        stdout\n"
        "      }\n"
        "    }\n"
        "  }\n"
        "}"
    )


def test_build_omits_default_args():
    ctx = Context().root_select(
        "container",
        [Arg("platform", None, None), Arg("expand", False, False)],
    )

    assert ctx.build() == "query {\n  container\n}"


def test_select_snapshots_mutable_args():
    args = ["echo", "hi"]
    block = Block(kind="text", call_id="c1")
    ctx = Context().root_select("container", [Arg("args", args), Arg("block", block)])
    query = ctx.build()

    args.append("later")
    block.call_id = "changed"

    assert ctx.build() == query


def test_select_copies_args_per_fork():
    args = ["a"]
    base = Context().root_select("container", [])
    left = base.select("Container", "withExec", [Arg("args", args)])
    right = base.select("Container", "withExec", [Arg("args", args)])

    left.selections[-1].args["args"].append("b")

    assert 'withExec(args: ["a"])' in right.build()


def test_build_aliases_multiple_fields():
    ctx = (
        Context()
        .root_select("version", [])
        .select_multiple("Version", python_name="pythonName", other="other")
    )

    assert ctx.build() == (
        "query {\n  version {\n    python_name: pythonName\n    other\n  }\n}"
    )


def test_build_inline_fragment_for_ids():
    ctx = Context().select_id("Container", "abc").select("Container", "stdout", [])

    assert ctx.build() == (
        "query {\n"
        '  node(id: "abc") {\n'
        "    ... on Container {\n"
        "      stdout\n"
        "    }\n"
        "  }\n"
        "}"
    )


def test_build_requires_a_selection():
    with pytest.raises(Exception, match="No field has been selected"):
        Context().build()


def test_get_value_walks_selections():
    ctx = Context().root_select("container", []).select("Container", "stdout", [])

    assert ctx.get_value({"container": {"stdout": "hi"}}, str) == "hi"


def test_get_value_rejects_null_for_required():
    ctx = Context().root_select("version", [])

    with pytest.raises(Exception, match="null response"):
        ctx.get_value({"version": None}, str)
    assert ctx.get_value({"version": None}, str | None) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("call_id", "callId"),
        ("ssh_url", "sshUrl"),
        ("name", "name"),
    ],
)
def test_snake_to_camel(value, expected):
    assert snake_to_camel(value, upper=False) == expected
    assert snake_to_camel(value) == expected[:1].upper() + expected[1:]


def test_enum_literal_before_str():
    class StrColor(str, enum.Enum):
        BLUE = "blue"

    assert to_literal(StrColor.BLUE) == "BLUE"
