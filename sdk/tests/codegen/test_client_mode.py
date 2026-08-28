import argparse
import ast
import importlib.util
import json
import re
import sys
import types

import pytest

import dagger
from codegen.cli import render, run_client_name
from codegen.partition import PartitionError
from dagger.client._binding import ModuleBinding

SDL = """
interface Node { id: ID! }
interface Exportable { id: ID! export(path: String!): String! }

type Query {
    container: Container!
    e2E: E2E!
    hello(name: String!, greeting: String = "hi"): Hello!
    node(id: ID!): Node
    version: String!
}

type Container implements Node & Exportable {
    id: ID!
    stdout: String!
    export(path: String!): String!
}

type Directory implements Node {
    id: ID!
    entries: [String!]!
    asHello: Hello!
    tag(kind: HelloKind!): Directory!
}

type File implements Node {
    id: ID!
    name: String!
    asHello: Hello!
    withHelloInput(name: String!, value: String!): File!
}

scalar JSON
scalar HelloToken
enum CacheSharingMode { SHARED LOCKED }
enum HelloKind { FULL SHORT }
input HelloOpts { verbose: Boolean }

type Hello implements Node {
    id: ID!
    greet(name: String!): String!
    report(kind: HelloKind = FULL): HelloReport!
    reports: [HelloReport!]!
    build(opts: HelloOpts, cache: CacheSharingMode = SHARED): Container!
    exportable: Exportable!
    token: HelloToken!
    json: JSON!
    withGreeting(greeting: String!): Hello!
    sync: ID!
}

type HelloReport implements Node {
    id: ID!
    text: String!
    hello: Hello!
}

type E2E implements Node {
    id: ID!
    run: String!
}
"""

HELLO_OWNERS = {
    "Hello": "hello",
    "HelloReport": "hello",
    "HelloKind": "hello",
    "HelloOpts": "hello",
    "HelloToken": "hello",
    ("Hello", "greet"): "hello",
    ("Hello", "report"): "hello",
    ("Hello", "reports"): "hello",
    ("Hello", "build"): "hello",
    ("Hello", "exportable"): "hello",
    ("Hello", "token"): "hello",
    ("Hello", "json"): "hello",
    ("Hello", "withGreeting"): "hello",
    ("Hello", "sync"): "hello",
    ("HelloReport", "text"): "hello",
    ("HelloReport", "hello"): "hello",
    ("Query", "hello"): "hello",
    ("Directory", "asHello"): "hello",
    ("Directory", "tag"): "hello",
    ("File", "asHello"): "hello",
    ("File", "withHelloInput"): "hello",
}

E2E_OWNERS = {
    "E2E": "e2e",
    ("E2E", "run"): "e2e",
    ("Query", "e2E"): "e2e",
}

LOCAL_BINDING = {
    "name": "hello",
    "kind": "LOCAL_SOURCE",
    "ref": "/.dagger/modules/hello",
    "pin": "",
}
GIT_BINDING = {
    "name": "hello",
    "kind": "GIT_SOURCE",
    "ref": "github.com/acme/hello",
    "pin": "abc123",
}


@pytest.fixture
def result(introspection):
    return introspection(SDL, HELLO_OWNERS | E2E_OWNERS)


@pytest.fixture
def hello_client(result) -> str:
    return render(result, mode="client", module="hello", binding=LOCAL_BINDING)


def load(source: str, name: str = "dagger.clients.hello") -> types.ModuleType:
    """Import generated source as a module, against the real dagger package."""
    spec = importlib.util.spec_from_loader(name, loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        exec(compile(source, f"{name}.py", "exec"), module.__dict__)
    finally:
        sys.modules.pop(name, None)
    return module


def signature(source: str, name: str) -> str:
    match = re.search(
        rf"^(?:async )?def {name}\((.*?)\) -> (.*?):$",
        source,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"no function {name} in generated source"
    return " ".join(match.group(0).split()).replace("( ", "(").replace(",)", ")")


def describe(result: dict, description: str, *, reason: str | None = None) -> None:
    """Give every symbol the hello module owns the same description."""
    for type_ in result["__schema"]["types"]:
        if not type_["name"].startswith("Hello"):
            continue
        type_["description"] = description
        for field in type_.get("fields") or ():
            field["description"] = description
            for arg in field.get("args") or ():
                arg["description"] = description
            if reason is not None and field["name"] == "greet":
                field["isDeprecated"] = True
                field["deprecationReason"] = reason
        for input_field in type_.get("inputFields") or ():
            input_field["description"] = description
        for value in type_.get("enumValues") or ():
            value["description"] = description


class TestCoreMode:
    def test_drops_owned_symbols_and_keeps_the_root(self, result):
        core = render(result)

        assert "class Hello(" not in core
        assert "class E2E(" not in core
        assert "def hello(" not in core
        assert "def as_hello(" not in core
        assert "class Container(Type):" in core
        assert "class Client(Query):" in core
        assert "dag = Client()" in core
        assert "class File(Type):" in core
        assert "def as_hello(" not in core

    def test_is_identical_whichever_module_the_schema_was_bound_to(self, introspection):
        core = "type Container { id: ID! stdout: String! }"
        bound_to_hello = introspection(
            core + " type Query { container: Container! hello: Hello! }"
            " type Hello { id: ID! greet: String! }",
            {
                "Hello": "hello",
                ("Hello", "greet"): "hello",
                ("Query", "hello"): "hello",
            },
        )
        bound_to_e2e = introspection(
            core + " type Query { container: Container! e2E: E2E! }"
            " type E2E { id: ID! run: String! }",
            {"E2E": "e2e", ("E2E", "run"): "e2e", ("Query", "e2E"): "e2e"},
        )
        assert render(bound_to_hello) == render(bound_to_e2e)

    def test_default_mode_matches_todays_output_for_a_module_free_schema(
        self, introspection
    ):
        # No owners: nothing to narrow, so the historical single-mode output.
        core = render(introspection(SDL))
        assert "class Hello(Type):" in core
        assert "def hello(self, name: str, *, greeting: str | None = " in core


class TestClientMode:
    def test_binding_is_rendered_verbatim(self, result):
        local = render(result, mode="client", module="hello", binding=LOCAL_BINDING)
        assert (
            "_BINDING = ModuleBinding(\n"
            "    name='hello',\n"
            "    kind='LOCAL_SOURCE',\n"
            "    ref='/.dagger/modules/hello',\n"
            "    pin='',\n"
            ")"
        ) in local

        git = render(result, mode="client", module="hello", binding=GIT_BINDING)
        assert "kind='GIT_SOURCE'" in git
        assert "ref='github.com/acme/hello'" in git
        assert "pin='abc123'" in git

        # Only the binding differs between the two.
        assert local.replace("LOCAL_SOURCE", "").replace(
            "/.dagger/modules/hello", ""
        ) == git.replace("GIT_SOURCE", "").replace("github.com/acme/hello", "").replace(
            "abc123", ""
        )

    def test_renders_only_owned_types(self, hello_client):
        assert "class Hello(Type):" in hello_client
        assert "class HelloReport(Type):" in hello_client
        assert "class HelloKind(Enum):" in hello_client
        assert "class HelloOpts(Input):" in hello_client
        assert "class HelloToken(Scalar):" in hello_client
        assert "class E2E(" not in hello_client
        assert "class Container(" not in hello_client
        assert "class Query(" not in hello_client
        assert "class Client(" not in hello_client
        assert "dag = " not in hello_client

    def test_entry_point_takes_constructor_args_and_a_keyword_only_client(
        self, hello_client
    ):
        assert signature(hello_client, "hello") == (
            "def hello(name: str, *, greeting: str | None = 'hi', "
            "client: _core.Client | None = None) -> Hello:"
        )
        assert (
            "_ctx = (_core.dag if client is None else client)._ctx"
            '.with_binding(_BINDING).root_select("hello", _args)  # noqa: SLF001'
        ) in hello_client
        assert "Arg(\"greeting\", greeting, 'hi')," in hello_client
        assert "return Hello(_ctx)" in hello_client
        assert "Client for the `hello` module." in hello_client

    def test_core_references_go_through_the_alias(self, hello_client):
        # return annotation, construction, list element and interface impl
        assert "-> _core.Container:" in hello_client
        assert "return _core.Container(_ctx)" in hello_client
        assert "-> _core.Exportable:" in hello_client
        assert "return _core._ExportableClient(_ctx)" in hello_client
        assert "return await _ctx.execute(_core.JSON)" in hello_client
        # enum defaults and parameter annotations
        assert (
            "cache: _core.CacheSharingMode | None = _core.CacheSharingMode.SHARED"
            in (hello_client)
        )
        # owned symbols stay bare
        assert "return await _ctx.execute(HelloToken)" in hello_client
        assert "kind: HelloKind | None = HelloKind.FULL" in hello_client
        assert "return await _ctx.execute_object_list(HelloReport)" in hello_client
        assert "-> Self:" in hello_client  # withGreeting

    def test_owned_fields_on_other_core_types_become_functions(self, hello_client):
        # every function on a core type is qualified by its parent.
        assert signature(hello_client, "directory_as_hello") == (
            "def directory_as_hello(directory: _core.Directory) -> Hello:"
        )
        assert signature(hello_client, "file_as_hello") == (
            "def file_as_hello(file: _core.File) -> Hello:"
        )
        assert (
            "_ctx = file._ctx.with_binding(_BINDING)"
            '.select("File", "asHello", _args)  # noqa: SLF001'
        ) in hello_client
        assert signature(hello_client, "file_with_hello_input") == (
            "def file_with_hello_input(file: _core.File, name: str, value: str)"
            " -> _core.File:"
        )

    def test_an_owned_type_is_bare_even_on_a_core_parent(self, hello_client):
        assert signature(hello_client, "directory_tag") == (
            "def directory_tag(directory: _core.Directory, kind: HelloKind)"
            " -> _core.Directory:"
        )
        assert 'Arg("kind", kind),' in hello_client
        assert (
            "_ctx = directory._ctx.with_binding(_BINDING)"
            '.select("Directory", "tag", _args)  # noqa: SLF001'
        ) in hello_client
        assert "return _core.Directory(_ctx)" in hello_client

    @pytest.mark.parametrize(
        "description",
        [
            'a """block""" quote',
            "a windows path C:\\Users\\x",
            "a trailing backslash \\",
            'a trailing quote "',
            'ends with a block quote """',
            'multi\nline """ with C:\\x\nand a trailing \\',
            "a" * 33 + "\\" + "b" * 100,
            "z" * 200,
        ],
    )
    def test_a_tricky_description_renders_valid_python(self, result, description):
        describe(result, description)

        client = render(result, mode="client", module="hello", binding=LOCAL_BINDING)

        compile(client, "dagger/clients/hello.py", "exec")

    @pytest.mark.parametrize(
        "description",
        [
            'a trailing quote "',
            '\\"""',
            "carriage\rreturn",
            "nul\x00byte",
            'C:\\Users\\x "q" \\',
            "a" * 33 + "\\" + "b" * 100,
        ],
    )
    def test_descriptions_and_deprecation_reasons_are_escaped(
        self, result, description
    ):
        reason = 'use "greet"\nor C:\\x\x00 instead\\'
        describe(result, description, reason=reason)

        client = render(result, mode="client", module="hello", binding=LOCAL_BINDING)
        module = load(client)

        assert module.Hello.__doc__ == description
        warning = re.search(r'warnings\.warn\(\s*("(?:\\.|[^"\\])*")', client)
        assert warning
        assert ast.literal_eval(warning.group(1)) == (
            f'Method "greet" is deprecated: {reason}'
        )

    def test_exports(self, hello_client):
        exported = re.search(r"__all__ = \[\n(.*?)\]", hello_client, re.DOTALL)
        assert exported
        names = {line.strip().strip('",') for line in exported.group(1).splitlines()}
        assert names == {
            "Hello",
            "HelloReport",
            "HelloKind",
            "HelloOpts",
            "HelloToken",
            "hello",
            "directory_as_hello",
            "file_as_hello",
            "directory_tag",
            "file_with_hello_input",
        }
        assert "_BINDING" not in names

    def test_root_type_is_read_off_the_schema(self, result):
        e2e = render(
            result,
            mode="client",
            module="e2e",
            binding={"name": "e2e", "kind": "LOCAL_SOURCE", "ref": "/e2e", "pin": ""},
        )
        assert "class E2E(Type):" in e2e
        assert signature(e2e, "e2e") == (
            "def e2e(*, client: _core.Client | None = None) -> E2E:"
        )
        assert "class Hello(" not in e2e

    def test_imports_and_runs_against_the_real_package(self, hello_client):
        module = load(hello_client)

        assert (
            ModuleBinding(
                name="hello", kind="LOCAL_SOURCE", ref="/.dagger/modules/hello", pin=""
            )
            == module._BINDING
        )
        hello = module.hello("world")
        assert isinstance(hello, module.Hello)
        assert hello._ctx.bindings == (module._BINDING,)
        assert [f.name for f in hello._ctx.selections] == ["hello"]
        assert hello._ctx.selections[0].args == {"name": "world"}

        # an explicit client is honoured, and core types are the real ones
        other = dagger.Client()
        assert module.hello("x", client=other)._ctx.conn is other._ctx.conn
        assert module.file_as_hello(dagger.File(other._ctx))

    def test_argument_named_client_is_escaped(self, introspection):
        result = introspection(
            "type Query { hello(client: String!): Hello! }"
            " type Hello { greet: String! }",
            {
                "Hello": "hello",
                ("Query", "hello"): "hello",
                ("Hello", "greet"): "hello",
            },
        )
        client = render(result, mode="client", module="hello", binding=LOCAL_BINDING)
        assert signature(client, "hello") == (
            "def hello(client_: str, *, client: _core.Client | None = None) -> Hello:"
        )
        assert 'Arg("client", client_),' in client

    def test_colliding_argument_names_are_rejected(self, introspection):
        result = introspection(
            "type Query { hello(client: String!, client_: String!): Hello! } "
            "type Hello { greet: String! }",
            {
                "Hello": "hello",
                ("Query", "hello"): "hello",
                ("Hello", "greet"): "hello",
            },
        )
        with pytest.raises(PartitionError, match="client_"):
            render(result, mode="client", module="hello", binding=LOCAL_BINDING)

    @pytest.mark.parametrize(
        ("module", "root"), [("arg", "Arg"), ("client", "Client"), ("type", "Type")]
    )
    def test_a_module_whose_names_the_client_file_binds_is_rejected(
        self, introspection, module, root
    ):
        result = introspection(
            f"type Query {{ {module}: {root}! }} type {root} {{ greet: String! }}",
            {root: module, ("Query", module): module, (root, "greet"): module},
        )
        with pytest.raises(PartitionError, match=root):
            render(result, mode="client", module=module, binding=LOCAL_BINDING)

    def test_a_root_type_named_after_a_core_type_is_rejected(self, introspection):
        result = introspection(
            "type Query { container: Container! } type Container { id: ID! }",
            {("Query", "container"): "container"},
        )
        with pytest.raises(PartitionError, match="core type"):
            render(result, mode="client", module="container", binding=LOCAL_BINDING)

    def test_module_without_an_entry_point_is_rejected(self, result):
        with pytest.raises(PartitionError, match="expected exactly 1"):
            render(result, mode="client", module="nobody", binding=LOCAL_BINDING)

    def test_client_mode_requires_module_and_binding(self, result):
        with pytest.raises(ValueError, match="--module and --binding"):
            render(result, mode="client")

    def test_bound_module_below_the_floor_is_rejected(self, result):
        with pytest.raises(ValueError, match=re.escape("v0.20.8")):
            render(
                result,
                mode="client",
                module="hello",
                binding=LOCAL_BINDING,
                engine_version="v0.20.8",
            )
        for ok in ("v1.0.0-beta.11", "v1.0.0-0", "latest", ""):
            render(
                result,
                mode="client",
                module="hello",
                binding=LOCAL_BINDING,
                engine_version=ok,
            )


def test_client_name_prints_one_name_per_line(capsys):
    run_client_name(argparse.Namespace(module=["foo-bar", "e2e"]))

    assert capsys.readouterr().out == "foo_bar\ne2e\n"


def test_client_name_rejects_two_modules_that_share_a_file(capsys):
    with pytest.raises(SystemExit) as exc:
        run_client_name(argparse.Namespace(module=["foo-bar", "foo_bar"]))

    assert exc.value.code == 2
    assert "foo_bar.py" in capsys.readouterr().err


def test_binding_round_trips_through_json():
    assert json.loads(json.dumps(LOCAL_BINDING)) == LOCAL_BINDING
