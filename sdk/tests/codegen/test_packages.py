import ast
import json
import sys
import types
from textwrap import dedent, indent

import graphql
import pytest
from graphql import build_schema

import dagger.client
from codegen import cli, partition
from codegen.packages import (
    SESSION_NAMES,
    client_package,
    core_package,
    global_package,
    write_global,
    write_package,
)
from codegen.partition import ClientError, ClientNameError, core_digest
from dagger.client import Session, Target
from dagger.client._core import Context
from dagger.client._session import SharedConnection

_CORE = """
    directive @sourceMap(module: String, filename: String)
        on OBJECT | FIELD_DEFINITION | ENUM | ENUM_VALUE | INPUT_OBJECT
    directive @expectedType(name: String!) on FIELD_DEFINITION | ARGUMENT_DEFINITION

    enum Severity { LOW HIGH }
    type Directory {
        id: ID! @expectedType(name: "Directory")
        entries: [String!]!
    }
    type File { id: ID! @expectedType(name: "File") }
"""

_LINTER = """
    type Linter @sourceMap(module: "linter") {
        id: ID! @expectedType(name: "Linter")
        lint(src: ID! @expectedType(name: "Directory"), level: Severity): String!
        report: LinterReport!
    }
    type LinterReport @sourceMap(module: "linter") { errors: Int! }
"""

_GLOW = """
    type Glow @sourceMap(module: "glow") { render(text: String!): String! }
"""

_MEMBERS = {
    _LINTER: {
        "Binding": 'asLinter: Linter! @sourceMap(module: "linter")',
        "Query": """
            linter(
                source: ID! @expectedType(name: "Directory"),
                config: String
            ): Linter! @sourceMap(module: "linter")
        """,
    },
    _GLOW: {
        "Binding": 'asGlow: Glow! @sourceMap(module: "glow")',
        "Query": 'glow: Glow! @sourceMap(module: "glow")',
    },
}


def _sdl(*clients: str, **members: str) -> str:
    """Core and the given clients, with the fields they contribute to core types.

    A keyword replaces what the clients contribute to that type.
    """
    fields = {
        "Binding": ["name: String!"],
        "Env": ["name: String!"],
        "Query": ["directory: Directory!"],
    }
    for contributed in (*(_MEMBERS[c] for c in clients), members):
        for type_name, field in contributed.items():
            if contributed is members or type_name not in members:
                fields[type_name].append(field)
    return (
        _CORE
        + "".join(clients)
        + "".join(f"type {name} {{ {' '.join(f)} }}" for name, f in fields.items())
    )


def _schema(*clients: str, **members: str) -> graphql.GraphQLSchema:
    return build_schema(_sdl(*clients, **members))


def _linter(*clients: str, **members: str) -> str:
    _, files = client_package(_schema(*clients, **members), "linter", "./linter")
    return files["__init__.py"]


def test_core_holds_no_client_type():
    code = core_package(_schema(_LINTER, _GLOW))["__init__.py"]

    assert "class Directory(_Type):" in code
    assert "class Binding(_Type):" in code
    assert "Linter" not in code
    assert "Glow" not in code
    assert "def linter(" not in code
    assert "def glow(" not in code


def test_core_entry_point():
    schema = _schema(_LINTER)
    files = core_package(schema)
    code = files["__init__.py"]

    assert files["py.typed"] == ""
    assert "def core(*, session: _Session | None = None) -> Query:" in code
    assert "return _client_root(Query, None, None, [], session=session)" in code
    assert "check_core" not in code.replace("check_core as _check_core", "")
    assert f'CORE_DIGEST = "{core_digest(schema)}"' in code
    assert '"CORE_DIGEST",' in code
    assert '"core",' in code
    # Only the temporary global client has them.
    assert "class Client(" not in code
    assert "dag = " not in code


def test_core_is_the_same_whatever_the_clients():
    core = core_package(_schema())

    assert core_package(_schema(_LINTER)) == core
    assert core_package(_schema(_LINTER, _GLOW)) == core


def test_legacy_core_is_the_same_whatever_the_clients():
    core = core_package(_schema(), "v0.20.0")
    # A client type named like a legacy ID must not take the class out of core.
    taken = 'type DirectoryID @sourceMap(module: "linter") { size: Int! }'
    schema = build_schema(_sdl(_LINTER) + taken)

    assert "class DirectoryID(_Scalar):" in core["__init__.py"]
    assert core_package(schema, "v0.20.0") == core


def _digest(files: dict[str, str]) -> str:
    code = files["__init__.py"]
    return code[code.index("CORE_DIGEST = ") :].splitlines()[0]


def test_core_digest_follows_the_compatibility_mode_only():
    schema = _schema()

    assert _digest(core_package(schema, "v0.21.0")) == _digest(
        core_package(schema, "v0.22.0")
    )
    assert _digest(core_package(schema, "v0.20.0")) != _digest(
        core_package(schema, "v0.21.0")
    )


def test_client_holds_its_own_types():
    code = _linter(_LINTER, _GLOW)

    assert "class Linter(_Type):" in code
    assert "class LinterReport(_Type):" in code
    assert "async def lint(self, src: Directory, *, level: Severity" in code
    assert "class Glow(" not in code
    assert "as_glow" not in code
    assert "class Directory(" not in code
    assert "class Query(" not in code


def _imports(code: str, module: str) -> dict[str, str]:
    """Names imported from a module, by the name they get."""
    return {
        alias.asname or alias.name: alias.name
        for node in ast.parse(code).body
        if isinstance(node, ast.ImportFrom)
        and f"{'.' * node.level}{node.module}" == module
        for alias in node.names
    }


def _defs(code: str, name: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in ast.parse(code).body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == name
    ]


def test_client_imports_the_core_types_it_names():
    code = _linter(_LINTER, _GLOW)

    imports = _imports(code, "dagger_clients.core")
    assert {imports[n] for n in ("Binding", "Directory", "Severity")} == {
        "Binding",
        "Directory",
        "Severity",
    }
    assert "File" not in imports.values()
    assert "Glow" not in imports.values()
    assert _imports(code, "._target") == {
        "CORE_DIGEST": "CORE_DIGEST",
        "NAME": "NAME",
        "PIN": "PIN",
        "REF": "REF",
    }
    assert "\n_TARGET = _Target(name=NAME, ref=REF, pin=PIN)\n" in code


def test_client_checks_core_before_importing_its_symbols():
    # A stale core that dropped a symbol must fail on check_core, with its
    # "run dagger generate" message, not on a plain ImportError before it.
    code = _linter(_LINTER, _GLOW)

    statements = [ast.unparse(node) for node in ast.parse(code).body]
    digest = statements.index(
        "from dagger_clients.core import CORE_DIGEST as _installed_core"
    )
    check = statements.index("_check_core(NAME, CORE_DIGEST, _installed_core)")
    symbols = [
        i
        for i, statement in enumerate(statements)
        if statement.startswith("from dagger_clients.core import")
        and "CORE_DIGEST" not in statement
    ]
    assert digest < check
    assert symbols
    assert all(check < i for i in symbols)


def test_client_entry_function():
    code = _linter(_LINTER)

    assert (
        "def linter(source: Directory, *, config: str | None = None, "
        "session: _Session | None = None,) -> Linter:"
    ) in code
    assert 'raise _type_error("linter", "source", source, "Directory")' in code
    assert (
        indent(
            dedent(
                """\
                _args = [
                    _Arg("source", source),
                    _Arg("config", config, None),
                ]
                return _client_root(Linter, _TARGET, "linter", _args, session=session)
                """
            ),
            "    ",
        )
        in code
    )


def test_client_entry_function_renames_a_session_argument():
    query = 'linter(session: String): Linter! @sourceMap(module: "linter")'
    _, files = client_package(_schema(_LINTER, Query=query), "linter", "./linter")
    code = files["__init__.py"]

    assert (
        "def linter(*, session_: str | None = None, "
        "session: _Session | None = None,) -> Linter:"
    ) in code
    assert '_Arg("session", session_, None),' in code


def test_client_contributed_field():
    code = _linter(_LINTER, _GLOW)

    assert (
        dedent(
            """
            def as_linter(binding: Binding, /) -> Linter:
                _args: list[_Arg] = []
                _ctx = _client_select(binding, _TARGET, "asLinter", _args)
                return Linter(_ctx)
            """
        )
        in code
    )
    assert '"as_linter",' in code
    assert "@overload" not in code


def test_client_contributed_field_executes_a_leaf():
    env = 'linterCount: Int! @sourceMap(module: "linter")'
    code = _linter(_LINTER, Env=env)

    assert "async def linter_count(env: Env, /) -> int:" in code
    assert '_ctx = _client_select(env, _TARGET, "linterCount", _args)' in code
    assert "return await _ctx.execute(int)" in code


def test_client_contributed_field_that_returns_an_id_of_its_receiver():
    binding = 'linted: ID! @expectedType(name: "Binding") @sourceMap(module: "linter")'
    code = _linter(_LINTER, Binding=binding)

    assert "async def linted(binding: Binding, /) -> Binding:" in code
    # Through client_select like any contributed field, so that the module is
    # loaded before the query, never straight through the receiver's context.
    assert '_ctx = _client_select(binding, _TARGET, "linted", _args)' in code
    assert "binding._ctx" not in code
    assert "return Binding(" in code


def test_client_contributed_field_receiver_avoids_an_argument_name():
    env = """
        withLinter(env: ID @expectedType(name: "Env")): Env!
            @sourceMap(module: "linter")
    """
    code = _linter(_LINTER, Env=env)

    assert "def with_linter(env_: Env, /, *, env: Env | None = None) -> Env:" in code
    assert '_ctx = _client_select(env_, _TARGET, "withLinter", _args)' in code


def test_client_overloads_one_name_on_two_receivers():
    env = 'asLinter(strict: Boolean): Linter! @sourceMap(module: "linter")'
    code = _linter(_LINTER, Env=env)

    *overloads, dispatcher = _defs(code, "as_linter")
    assert [ast.unparse(d.args) for d in overloads] == [
        "binding: Binding, /",
        "env: Env, /, *, strict: bool | None=None",
    ]
    assert all(ast.unparse(d.decorator_list) == "_overload" for d in overloads)
    assert not dispatcher.decorator_list
    # One selection per receiver, each with its own field arguments.
    assert '_client_select(binding, _TARGET, "asLinter", _args)' in code
    assert '_client_select(env, _TARGET, "asLinter", _args)' in code
    assert '_Arg("strict", strict, None)' in code
    exported = code[code.index("__all__") :]
    assert exported.count('"as_linter",') == 1
    assert exported.count('"') == 2 * len(
        ("Linter", "LinterReport", "as_linter", "linter")
    )
    compile(code, "linter", "exec")


def _names(code: str) -> tuple[set[str], set[str]]:
    """Names a module imports, and names it defines at the top level."""
    tree = ast.parse(code)
    imported = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    defined = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    }
    return imported, defined


@pytest.mark.parametrize("name", ["Arg", "Type", "Session", "Callable"])
def test_client_type_named_like_a_helper_does_not_shadow_it(name: str):
    own = f'type {name} @sourceMap(module: "linter") {{ value: Int! }}'
    _, files = client_package(build_schema(_sdl(_LINTER) + own), "linter", ".")
    code = files["__init__.py"]

    assert f"class {name}(" in code
    imported, defined = _names(code)
    assert not imported & defined
    # The entry function still builds its arguments with the query builder.
    assert '_Arg("source", source)' in code


def test_core_type_named_like_a_helper_does_not_shadow_it():
    own = "type Type { value: Int! }"
    code = core_package(build_schema(_sdl() + own))["__init__.py"]

    assert "class Type(" in code
    imported, defined = _names(code)
    assert not imported & defined


def test_client_is_the_same_whatever_the_other_clients():
    alone = client_package(_schema(_LINTER), "linter", "./linter")

    assert client_package(_schema(_LINTER, _GLOW), "linter", "./linter") == alone
    assert client_package(_schema(_GLOW, _LINTER), "linter", "./linter") == alone


@pytest.mark.parametrize("schema_version", ["v0.20.0", "v0.21.0"])
def test_packages_compile(schema_version: str):
    env = 'asLinter(strict: Boolean): Linter! @sourceMap(module: "linter")'
    schema = _schema(_LINTER, _GLOW, Env=env)
    _, client = client_package(schema, "linter", ".", schema_version=schema_version)

    for files in (core_package(schema, schema_version), client):
        for name, content in files.items():
            compile(content, name, "exec")


def test_core_type_whose_fields_are_all_contributed_compiles():
    # Partitioning can leave a class with no body of its own.
    query = 'type Query { linter: Linter! @sourceMap(module: "linter") }'
    binding = "type Binding { name: String! }"
    schema = build_schema(_CORE + _LINTER + query + binding)

    code = core_package(schema)["__init__.py"]

    assert "class Query(_Root):\n    ...\n" in code
    compile(code, "core", "exec")


def test_interface_with_no_own_member_compiles():
    # id is on every Type, so the protocol of this interface has no member.
    bare = 'interface Bare { id: ID! @expectedType(name: "Bare") }'
    schema = build_schema(_sdl() + bare)

    code = core_package(schema)["__init__.py"]

    assert "class Bare(_Protocol):\n    ...\n" in code
    compile(code, "core", "exec")


def test_target_is_plain_data():
    schema = _schema(_LINTER)
    package, files = client_package(schema, "linter", "./modules/linter")

    assert package == "linter"
    assert files["py.typed"] == ""
    assert files["_target.py"] == dedent(
        f"""\
        # Code generated by dagger. DO NOT EDIT.

        NAME = "linter"
        REF = "./modules/linter"
        PIN = None
        CORE_DIGEST = "{core_digest(schema)}"
        """
    )


def test_target_with_a_pin_and_a_given_core_digest():
    schema = _schema(_GLOW)
    _, files = client_package(
        schema,
        "glow",
        "github.com/eunomie/glow",
        "4f1c9e",
        core_digest=core_digest(schema),
    )

    assert 'NAME = "glow"' in files["_target.py"]
    assert 'REF = "github.com/eunomie/glow"' in files["_target.py"]
    assert 'PIN = "4f1c9e"' in files["_target.py"]
    assert f'CORE_DIGEST = "{core_digest(schema)}"' in files["_target.py"]
    assert "import" not in files["_target.py"]


@pytest.mark.parametrize("given", ["", "sha256:other"])
def test_client_refuses_a_core_digest_that_is_not_its_schema_core(given: str):
    # Otherwise check_core would bless a client generated against other core
    # types, which is the version skew it is there to catch.
    schema = _schema(_GLOW)

    with pytest.raises(ClientError, match=f'"{given}".*{core_digest(schema)}'):
        client_package(schema, "glow", "./glow", core_digest=given)


class _Runtime:
    """Fake of what the generated code needs from dagger.client, recording calls."""

    def __init__(self) -> None:
        self.selected: list[tuple] = []
        self.rooted: list[tuple] = []

    class Session: ...

    class Target:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    def check_core(self, name: str, wanted: str, installed: str) -> None:
        assert wanted == installed, name

    def client_root(self, cls, target, name, args, *, session=None):
        self.rooted.append((cls, target, name, args))
        return cls(Context())

    def client_select(self, receiver, target, name, args):
        self.selected.append((receiver, target, name, args))
        return Context()


@pytest.fixture
def runtime(monkeypatch):
    """Load generated packages against a fake runtime; returns the loader."""
    fake = _Runtime()
    for name in ("Session", "Target", "check_core", "client_root", "client_select"):
        monkeypatch.setattr(dagger.client, name, getattr(fake, name), raising=False)
    namespace = types.ModuleType("dagger_clients")
    monkeypatch.setitem(sys.modules, "dagger_clients", namespace)

    def _module(name: str, code: str) -> types.ModuleType:
        module = types.ModuleType(name)
        module.__package__ = name.rpartition(".")[0] or name
        monkeypatch.setitem(sys.modules, name, module)
        exec(compile(code, name, "exec"), module.__dict__)
        return module

    def load(schema: graphql.GraphQLSchema, name: str) -> types.ModuleType:
        _module("dagger_clients.core", core_package(schema)["__init__.py"])
        package, files = client_package(schema, name, ".")
        client = f"dagger_clients.{package}"
        module = types.ModuleType(client)
        module.__package__ = client
        monkeypatch.setitem(sys.modules, client, module)
        _module(f"{client}._target", files["_target.py"])
        exec(compile(files["__init__.py"], client, "exec"), module.__dict__)
        return module

    load.selected = fake.selected  # type: ignore[attr-defined]
    load.rooted = fake.rooted  # type: ignore[attr-defined]
    load.Target = fake.Target  # type: ignore[attr-defined]
    return load


_ANIMALS = """
    interface Animal {
        id: ID! @expectedType(name: "Animal")
        sound: String!
        asLinter: Linter! @sourceMap(module: "linter")
    }
    type Zebra implements Animal {
        id: ID! @expectedType(name: "Zebra")
        sound: String!
        asLinter(strict: Boolean!): Linter! @sourceMap(module: "linter")
    }
"""


def test_overload_dispatches_on_the_exact_type_of_the_receiver(runtime):
    # A Zebra is an Animal structurally, so an isinstance chain would pick the
    # Animal hook first and reject Zebra's own argument.
    schema = build_schema(_sdl(_LINTER) + _ANIMALS)
    linter = runtime(schema, "linter")
    core = sys.modules["dagger_clients.core"]
    zebra = core.Zebra(Context())

    result = linter.as_linter(zebra, strict=True)

    assert isinstance(result, linter.Linter)
    receiver, _, name, args = runtime.selected[-1]
    assert receiver is zebra
    assert name == "asLinter"
    assert [(a.name, a.value) for a in args] == [("strict", True)]


def test_overload_dispatches_an_implementation_to_its_interface_hook(runtime):
    # With no hook of its own, a Zebra is still an Animal.
    animals = _ANIMALS.replace(
        'asLinter(strict: Boolean!): Linter! @sourceMap(module: "linter")', ""
    )
    schema = build_schema(_sdl(_LINTER) + animals)
    linter = runtime(schema, "linter")
    core = sys.modules["dagger_clients.core"]
    zebra = core.Zebra(Context())

    assert isinstance(linter.as_linter(zebra), linter.Linter)
    assert runtime.selected[-1][0] is zebra
    with pytest.raises(TypeError, match="as_linter"):
        linter.as_linter("zebra")


def test_client_that_names_no_core_symbol_compiles(runtime):
    # Nothing to import from core but the digest: no empty import block.
    schema = build_schema(_sdl() + _named("solo", "Solo", "solo"))

    solo = runtime(schema, "solo")

    assert isinstance(solo.solo(), solo.Solo)
    assert "import (" not in client_package(schema, "solo", ".")[1]["__init__.py"]


def test_client_accepts_any_object_for_a_generic_id(runtime):
    # No @expectedType: any Type is accepted, under the alias the package has.
    query = 'linter(source: ID!): Linter! @sourceMap(module: "linter")'
    schema = _schema(_LINTER, Query=query)
    code = client_package(schema, "linter", ".")[1]["__init__.py"]

    assert "def linter(source: _Type, *, session: _Session | None = None,)" in code
    linter = runtime(schema, "linter")
    directory = sys.modules["dagger_clients.core"].Directory(Context())
    assert isinstance(linter.linter(directory), linter.Linter)
    with pytest.raises(TypeError, match="linter"):
        linter.linter("source")


def test_client_type_named_target_does_not_shadow_the_descriptor(runtime):
    own = 'type TARGET @sourceMap(module: "linter") { value: Int! }'
    schema = build_schema(_sdl(_LINTER) + own)

    linter = runtime(schema, "linter")
    directory = sys.modules["dagger_clients.core"].Directory(Context())
    linter.linter(directory)

    _, target, _, _ = runtime.rooted[-1]
    assert isinstance(target, runtime.Target)
    assert target.kwargs == {"name": "linter", "ref": ".", "pin": None}
    assert (
        "class TARGET(_Type):"
        in client_package(schema, "linter", ".")[1]["__init__.py"]
    )


def _named(module: str, root: str, constructor: str) -> str:
    """A client whose name the engine turned into a type and a constructor."""
    return f"""
        type {root} @sourceMap(module: "{module}") {{ run: String! }}
        extend type Query {{ {constructor}: {root}! @sourceMap(module: "{module}") }}
    """


def test_client_name_becomes_a_package():
    schema = build_schema(
        _sdl() + _named("My-Project.dev", "MyProjectDev", "myProjectDev")
    )
    package, files = client_package(schema, "my-project.dev", ".")

    assert package == "my_project_dev"
    assert (
        "def my_project_dev(*, session: _Session | None = None) -> MyProjectDev:"
        in (files["__init__.py"])
    )
    # The field name is the engine's, so the SDK never derives it.
    assert (
        '_client_root(MyProjectDev, _TARGET, "myProjectDev", _args, session=session)'
        in files["__init__.py"]
    )
    # The descriptor pins the name as given, which is the one the engine knows.
    assert 'NAME = "my-project.dev"' in files["_target.py"]


@pytest.mark.parametrize(
    ("module", "reason"),
    [
        ("class", "keyword"),
        ("core", "core bindings"),
        ("_hidden", 'starts with "_"'),
        ("my linter", "not a Python identifier"),
    ],
)
def test_client_name_refused(module: str, reason: str):
    schema = build_schema(_sdl() + _named(module, "Thing", "thing"))

    with pytest.raises(ClientNameError, match=reason):
        client_package(schema, module, ".")


def test_client_name_refused_when_two_clients_become_one_package():
    schema = build_schema(
        _sdl()
        + _named("my-linter", "MyLinter", "myLinter")
        + _named("my.linter", "MyLinter2", "myLinter2")
    )

    with pytest.raises(ClientNameError, match='both become the package "my_linter"'):
        client_package(schema, "my-linter", ".")


def test_client_missing_from_the_schema():
    with pytest.raises(
        ClientError, match='nothing to the client "glow"; it has: linter'
    ):
        client_package(_schema(_LINTER), "glow", ".")


def test_packages_refuse_a_wrong_attribution():
    # Otherwise the field lands in the linter package, and glow gets nothing.
    stray = """
        type Glow @sourceMap(module: "glow") {
            lint: Int! @sourceMap(module: "linter")
        }
    """
    schema = build_schema(_sdl(_LINTER) + stray)

    with pytest.raises(ClientError, match=r'"Glow\.lint"'):
        client_package(schema, "linter", ".")
    with pytest.raises(ClientError, match=r'"Glow\.lint"'):
        core_package(schema)


def test_client_refuses_a_type_of_another_client():
    env = 'glowLinter: Glow! @sourceMap(module: "linter")'

    with pytest.raises(ClientError, match=r'"Env\.glowLinter" .* names "Glow"'):
        _linter(_LINTER, _GLOW, Env=env)


def _introspection(sdl: str) -> dict:
    """Introspection result, with directives the way the engine adds them."""
    schema = build_schema(sdl)

    def directives(node) -> list[dict]:
        return [
            {
                "name": d.name.value,
                "args": [
                    {"name": a.name.value, "value": graphql.print_ast(a.value)}
                    for a in d.arguments
                ],
            }
            for d in (node.directives if node else ())
        ]

    result = graphql.introspection_from_schema(schema)
    for tp in result["__schema"]["types"]:
        named = schema.type_map[tp["name"]]
        tp["directives"] = directives(named.ast_node)
        for field in tp["fields"] or ():
            member = named.fields[field["name"]]
            field["directives"] = directives(member.ast_node)
            for arg in field["args"]:
                arg["directives"] = directives(member.args[arg["name"]].ast_node)
        for field in tp["inputFields"] or ():
            field["directives"] = directives(named.fields[field["name"]].ast_node)
        for value in tp["enumValues"] or ():
            value["directives"] = directives(named.values[value["name"]].ast_node)
    return {**result, "__schemaVersion": "v0.21.0"}


@pytest.fixture
def introspection(tmp_path):
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(_introspection(_sdl(_LINTER, _GLOW))))
    return path


def test_cli_generates_packages(tmp_path, introspection):
    out = tmp_path / "src"

    cli.main(["generate-core", "-i", str(introspection), "-o", str(out)])
    core = (out / "dagger_clients/core/__init__.py").read_text()
    digest = core[core.index("CORE_DIGEST = ") :].splitlines()[0]

    # In real use, the caller hands the client the digest of the core it
    # generated, because the two must match for the client to import.
    cli.main(
        [
            "generate-client",
            *("-i", str(introspection)),
            *("-o", str(out)),
            *("--name", "linter"),
            *("--ref", "github.com/acme/linter"),
            *("--pin", "4f1c9e"),
            *("--core-digest", digest.removeprefix("CORE_DIGEST = ").strip('"')),
        ]
    )
    cli.main(
        [
            "generate-client",
            *("-i", str(introspection)),
            *("-o", str(out)),
            *("--name", "glow"),
            *("--ref", "./glow"),
        ]
    )

    assert sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()) == [
        "dagger_clients/core/__init__.py",
        "dagger_clients/core/py.typed",
        "dagger_clients/glow/__init__.py",
        "dagger_clients/glow/_target.py",
        "dagger_clients/glow/py.typed",
        "dagger_clients/linter/__init__.py",
        "dagger_clients/linter/_target.py",
        "dagger_clients/linter/py.typed",
    ]
    client = (out / "dagger_clients/linter/__init__.py").read_text()
    target = (out / "dagger_clients/linter/_target.py").read_text()
    assert "Linter" not in core
    assert "def as_linter(binding: Binding, /) -> Linter:" in client
    assert 'PIN = "4f1c9e"' in target
    assert digest in target
    # Without the flag, the digest is the one of the same schema's core.
    assert digest in (out / "dagger_clients/glow/_target.py").read_text()


def test_cli_refuses_a_core_digest_of_another_core(tmp_path, introspection, capsys):
    args = ["generate-client", "-i", str(introspection), "-o", str(tmp_path)]

    with pytest.raises(SystemExit):
        cli.main([*args, "--name", "glow", "--ref", ".", "--core-digest", "sha256:x"])

    assert '"sha256:x"' in capsys.readouterr().err
    assert not (tmp_path / "dagger_clients").exists()


@pytest.mark.parametrize("flag", ["--name", "--ref"])
def test_cli_refuses_an_empty_name_or_ref(tmp_path, introspection, capsys, flag):
    args = ["generate-client", "-i", str(introspection), "-o", str(tmp_path)]
    given = {"--name": "glow", "--ref": ".", flag: ""}

    with pytest.raises(SystemExit):
        cli.main([*args, *(a for f, v in given.items() for a in (f, v))])

    assert f"argument {flag}" in capsys.readouterr().err
    assert not (tmp_path / "dagger_clients").exists()


def test_cli_reads_what_write_package_writes(tmp_path):
    schema = _schema(_LINTER)
    package, files = client_package(schema, "linter", ".")

    root = write_package(tmp_path, package, files)

    assert root == tmp_path / "dagger_clients" / "linter"
    assert {p.name: p.read_text() for p in root.iterdir()} == files


def test_cli_refuses_a_client_name(tmp_path, introspection, capsys):
    args = ["generate-client", "-i", str(introspection), "-o", str(tmp_path)]

    with pytest.raises(SystemExit):
        cli.main([*args, "--name", "core", "--ref", "."])

    assert 'client name "core" is taken by the core bindings' in capsys.readouterr().err
    assert not (tmp_path / "dagger_clients").exists()


def test_cli_still_generates_one_file(tmp_path, introspection):
    output = tmp_path / "gen.py"

    cli.main(["generate", "-i", str(introspection), "-o", str(output)])

    code = output.read_text()
    assert "class Linter(Type):" in code
    assert "class Client(Query):" in code
    assert "dag = Client()" in code


# The temporary global client, generated only with the flag.


def _global(*clients: str, **members: str) -> str:
    return global_package([_schema(*clients, **members)])["__init__.py"]


def _exported(code: str) -> set[str]:
    node = next(
        n
        for n in ast.parse(code).body
        if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == "__all__"
    )
    return set(ast.literal_eval(node.value))


def test_global_client_is_temporary_and_says_so():
    code = _global(_LINTER)

    assert "Temporary" in code
    assert "global-client = true" in code
    assert "dagger generate" in code


def test_global_client_delegates_root_fields_to_core():
    code = _global(_LINTER)

    assert "from dagger.client import Session as _Session" in code
    assert "class Client(_Session):" in code
    assert (
        "    def directory(self) -> Directory:\n"
        "        return _core.core(session=self).directory()\n"
    ) in code
    assert "\ndag = Client()\n" in code
    assert "from dagger_clients.core import *" in code


def test_global_client_has_one_method_per_client():
    code = _global(_LINTER, _GLOW)

    assert (
        "    def linter(self, source: Directory, *, config: str | None = None,) "
        "-> Linter:\n"
        "        return _linter.linter(source, config=config, session=self)\n"
    ) in code
    assert (
        "    def glow(self) -> Glow:\n        return _glow.glow(session=self)\n" in code
    )
    assert "import dagger_clients.linter as _linter" in code
    assert "import dagger_clients.glow as _glow" in code


def test_global_client_keeps_the_legacy_name_of_a_session_argument():
    query = 'linter(session: String): Linter! @sourceMap(module: "linter")'
    code = _global(_LINTER, Query=query)

    assert "def linter(self, *, session: str | None = None) -> Linter:" in code
    assert "return _linter.linter(session_=session, session=self)" in code


def test_global_client_has_a_method_for_a_contributed_root_field():
    query = 'lintAll(strict: Boolean!): String! @sourceMap(module: "linter")'
    code = _global(_LINTER, Query=query)

    # No entry: the schema has no constructor, but the global client still
    # carries the field the way the legacy dag did.
    assert (
        "    async def lint_all(self, strict: bool) -> str:\n"
        "        return await _linter.lint_all(_core.core(session=self), strict)\n"
    ) in code


def test_global_client_puts_contributed_fields_on_the_core_classes():
    env = 'asLinter(strict: Boolean): Linter! @sourceMap(module: "linter")'
    schema = build_schema(_sdl(_LINTER, _GLOW, Env=env) + _ANIMALS)
    code = global_package([schema])["__init__.py"]

    assert "Binding.as_linter = _linter.as_linter  # type: ignore[attr-defined]" in code
    assert "Env.as_linter = _linter.as_linter  # type: ignore[attr-defined]" in code
    assert "Binding.as_glow = _glow.as_glow  # type: ignore[attr-defined]" in code
    assert "Zebra.as_linter = _linter.as_linter" in code
    assert "_AnimalClient.as_linter = _linter.as_linter" in code
    assert code.count("_AnimalClient.as_linter =") == 1
    assert "_AnimalClient,\n" in code


def test_global_client_exports_the_types_and_itself_only():
    exported = _exported(_global(_LINTER, _GLOW))

    assert {"Directory", "Binding", "Query", "Severity", "File"} <= exported
    assert {"Linter", "LinterReport", "Glow"} <= exported
    assert {"Client", "dag"} <= exported
    assert not exported & {"core", "CORE_DIGEST", "linter", "glow", "as_linter"}
    assert not {n for n in exported if n.startswith("_")}


def test_global_client_takes_one_schema_per_client():
    together = global_package([_schema(_LINTER, _GLOW)])

    assert global_package([_schema(_LINTER), _schema(_GLOW)]) == together
    assert global_package([_schema(_GLOW), _schema(_LINTER)]) == together


def test_global_client_refuses_schemas_of_two_cores():
    other = build_schema(_sdl(_GLOW) + "type Extra { n: Int! }")

    with pytest.raises(ClientError, match="two cores"):
        global_package([_schema(_LINTER), other])


def test_global_client_refuses_two_clients_that_become_one_package():
    schemas = [
        build_schema(_sdl() + _named("my-linter", "MyLinter", "myLinter")),
        build_schema(_sdl() + _named("my.linter", "MyLinter2", "myLinter2")),
    ]

    with pytest.raises(ClientNameError, match='both become the package "my_linter"'):
        global_package(schemas)


@pytest.mark.parametrize("field", ["close", "load", "execute"])
def test_global_client_refuses_a_core_field_that_hides_the_session(field: str):
    schema = _schema(Query=f"{field}: String!")

    with pytest.raises(ClientError) as info:
        global_package([schema])

    assert str(info.value) == (
        f'the global client cannot have a method for "Query.{field}" of core: '
        f"it would hide Session.{field}"
    )


def test_global_client_refuses_a_client_that_hides_the_session():
    schema = build_schema(_sdl() + _named("connect", "Connect", "connect"))

    with pytest.raises(ClientError) as info:
        global_package([schema])

    assert str(info.value) == (
        'the global client cannot have a method for "Query.connect" of the '
        'client "connect": it would hide Session.connect'
    )


def test_global_client_knows_every_name_of_a_session():
    # The generator cannot import the SDK, so it keeps its own list.
    instance = Session(SharedConnection())

    assert {n for n in dir(instance) if not n.startswith("__")} == SESSION_NAMES


def test_global_client_with_no_client():
    code = _global()

    assert "def directory(self) -> Directory:" in code
    assert "import dagger_clients.core as _core" in code
    assert code.count("dagger_clients.") == 2
    compile(code, "dagger_global", "exec")


@pytest.mark.parametrize("schema_version", ["v0.20.0", "v0.21.0"])
def test_global_package_compiles(schema_version: str):
    env = 'asLinter(strict: Boolean): Linter! @sourceMap(module: "linter")'
    schema = build_schema(_sdl(_LINTER, _GLOW, Env=env) + _ANIMALS)

    files = global_package([schema], schema_version)

    assert files["py.typed"] == ""
    compile(files["__init__.py"], "dagger_global", "exec")


@pytest.fixture
def installed(monkeypatch):
    """Load core, every client and the global client on the real runtime."""
    namespace = types.ModuleType("dagger_clients")
    monkeypatch.setitem(sys.modules, "dagger_clients", namespace)

    def _module(name: str, code: str) -> types.ModuleType:
        module = types.ModuleType(name)
        module.__package__ = name.rpartition(".")[0] or name
        monkeypatch.setitem(sys.modules, name, module)
        exec(compile(code, name, "exec"), module.__dict__)
        return module

    def load(schema: graphql.GraphQLSchema) -> types.ModuleType:
        _module("dagger_clients.core", core_package(schema)["__init__.py"])
        for name in partition.modules(schema):
            package, files = client_package(schema, name, ".")
            client = f"dagger_clients.{package}"
            module = types.ModuleType(client)
            module.__package__ = client
            monkeypatch.setitem(sys.modules, client, module)
            _module(f"{client}._target", files["_target.py"])
            exec(compile(files["__init__.py"], client, "exec"), module.__dict__)
        return _module("dagger_global", global_package([schema])["__init__.py"])

    return load


def test_global_dag_is_a_session_over_the_shared_connection(installed):
    global_ = installed(_schema(_LINTER))

    assert isinstance(global_.dag, Session)
    assert type(global_.dag) is global_.Client
    assert global_.dag.connection is SharedConnection()


def test_global_dag_returns_the_core_classes(installed):
    global_ = installed(_schema(_LINTER))
    core = sys.modules["dagger_clients.core"]

    directory = global_.dag.directory()

    assert type(directory) is core.Directory
    assert directory._ctx.conn is global_.dag
    assert global_.Directory is core.Directory


def test_old_and_new_calls_mix(installed):
    global_ = installed(_schema(_LINTER))
    core = sys.modules["dagger_clients.core"]
    linter = sys.modules["dagger_clients.linter"]
    target = Target(name="linter", ref=".")

    new_with_old = linter.linter(global_.dag.directory())
    old_with_new = global_.dag.linter(core.core().directory(), config="x")

    assert type(new_with_old) is type(old_with_new) is linter.Linter
    assert new_with_old._ctx.targets == old_with_new._ctx.targets == {target}
    assert old_with_new._ctx.conn is global_.dag
    assert [f.name for f in old_with_new._ctx.selections] == ["linter"]
    assert old_with_new._ctx.selections[0].args["config"] == "x"


def test_contributed_field_is_a_method_at_run_time(installed):
    global_ = installed(_schema(_LINTER))
    core = sys.modules["dagger_clients.core"]
    linter = sys.modules["dagger_clients.linter"]
    binding = core.Binding(Context(global_.dag))

    result = binding.as_linter()

    assert type(result) is linter.Linter
    assert result._ctx.targets == {Target(name="linter", ref=".")}
    assert result._ctx.conn is global_.dag


def test_cli_generates_the_global_client_from_one_schema_per_client(tmp_path):
    linter = tmp_path / "linter.json"
    glow = tmp_path / "glow.json"
    linter.write_text(json.dumps(_introspection(_sdl(_LINTER))))
    glow.write_text(json.dumps(_introspection(_sdl(_GLOW))))
    out = tmp_path / "src"

    cli.main(["generate-global", "-i", str(linter), "-i", str(glow), "-o", str(out)])

    code = (out / "dagger_global/__init__.py").read_text()
    assert (out / "dagger_global/py.typed").read_text() == ""
    assert "def linter(self, source: Directory" in code
    assert "def glow(self) -> Glow:" in code
    assert not (out / "dagger_clients").exists()


def test_cli_refuses_global_schemas_of_two_cores(tmp_path, introspection, capsys):
    other = tmp_path / "other.json"
    other.write_text(json.dumps(_introspection(_sdl() + "type Extra { n: Int! }")))

    with pytest.raises(SystemExit):
        cli.main(
            ["generate-global", "-i", str(introspection), "-i", str(other), "-o", "x"]
        )

    assert "two cores" in capsys.readouterr().err


def test_write_global_writes_next_to_the_namespace(tmp_path):
    files = global_package([_schema(_LINTER)])

    root = write_global(tmp_path, files)

    assert root == tmp_path / "dagger_global"
    assert {p.name: p.read_text() for p in root.iterdir()} == files
