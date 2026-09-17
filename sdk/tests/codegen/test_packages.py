import json
from textwrap import dedent, indent

import graphql
import pytest
from graphql import build_schema

from codegen import cli
from codegen.packages import client_package, core_package, write_package
from codegen.partition import ClientError, ClientNameError, core_digest

_CORE = """
    directive @sourceMap(module: String, filename: String)
        on OBJECT | FIELD_DEFINITION | ENUM | INPUT_OBJECT
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

    assert "class Directory(Type):" in code
    assert "class Binding(Type):" in code
    assert "Linter" not in code
    assert "Glow" not in code
    assert "def linter(" not in code
    assert "def glow(" not in code


def test_core_entry_point():
    schema = _schema(_LINTER)
    files = core_package(schema)
    code = files["__init__.py"]

    assert files["py.typed"] == ""
    assert "def core(*, session: Session | None = None) -> Query:" in code
    assert "return client_root(Query, None, session=session, args=[])" in code
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


def test_client_holds_its_own_types():
    code = _linter(_LINTER, _GLOW)

    assert "class Linter(Type):" in code
    assert "class LinterReport(Type):" in code
    assert "async def lint(self, src: Directory, *, level: Severity" in code
    assert "class Glow(" not in code
    assert "as_glow" not in code
    assert "class Directory(" not in code
    assert "class Query(" not in code


def test_client_imports_the_core_types_it_names():
    code = _linter(_LINTER, _GLOW)

    assert (
        dedent(
            """
            from dagger_clients.core import (
                Binding,
                Directory,
                Severity,
            )

            from ._target import NAME, PIN, REF

            TARGET = Target(name=NAME, ref=REF, pin=PIN)
            """
        )
        in code
    )


def test_client_entry_function():
    code = _linter(_LINTER)

    assert (
        "def linter(source: Directory, *, config: str | None = None, "
        "session: Session | None = None,) -> Linter:"
    ) in code
    assert 'raise _type_error("linter", "source", source, "Directory")' in code
    assert (
        indent(
            dedent(
                """\
                _args = [
                    Arg("source", source),
                    Arg("config", config, None),
                ]
                return client_root(Linter, TARGET, session=session, args=_args)
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
        "session: Session | None = None,) -> Linter:"
    ) in code
    assert 'Arg("session", session_, None),' in code


def test_client_contributed_field():
    code = _linter(_LINTER, _GLOW)

    assert (
        dedent(
            """
            def as_linter(binding: Binding, /) -> Linter:
                _args: list[Arg] = []
                _ctx = client_select(binding, TARGET, "asLinter", _args)
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
    assert '_ctx = client_select(env, TARGET, "linterCount", _args)' in code
    assert "return await _ctx.execute(int)" in code


def test_client_contributed_field_receiver_avoids_an_argument_name():
    env = """
        withLinter(env: ID @expectedType(name: "Env")): Env!
            @sourceMap(module: "linter")
    """
    code = _linter(_LINTER, Env=env)

    assert "def with_linter(env_: Env, /, *, env: Env | None = None) -> Env:" in code
    assert '_ctx = client_select(env_, TARGET, "withLinter", _args)' in code


def test_client_overloads_one_name_on_two_receivers():
    env = 'asLinter(strict: Boolean): Linter! @sourceMap(module: "linter")'
    code = _linter(_LINTER, Env=env)

    assert (
        dedent(
            """
            @overload
            def as_linter(binding: Binding, /) -> Linter: ...


            @overload
            def as_linter(env: Env, /, *, strict: bool | None = None,) -> Linter: ...


            def as_linter(receiver, /, *args, **kwargs):
                if isinstance(receiver, Binding):
                    return _binding_as_linter(receiver, *args, **kwargs)
                if isinstance(receiver, Env):
                    return _env_as_linter(receiver, *args, **kwargs)
                raise _type_error("as_linter", "receiver", receiver, "Binding | Env")
            """
        )
        in code
    )
    assert "def _binding_as_linter(binding: Binding, /) -> Linter:" in code
    assert "def _env_as_linter(env: Env, /, *, strict: bool | None" in code
    exported = code[code.index("__all__") :]
    assert exported.count('"as_linter",') == 1
    assert "_binding_as_linter" not in exported


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
    _, files = client_package(
        _schema(_GLOW),
        "glow",
        "github.com/eunomie/glow",
        "4f1c9e",
        core_digest="sha256:given",
    )

    assert 'NAME = "glow"' in files["_target.py"]
    assert 'REF = "github.com/eunomie/glow"' in files["_target.py"]
    assert 'PIN = "4f1c9e"' in files["_target.py"]
    assert 'CORE_DIGEST = "sha256:given"' in files["_target.py"]
    assert "import" not in files["_target.py"]


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
        "def my_project_dev(*, session: Session | None = None) -> MyProjectDev:"
        in (files["__init__.py"])
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
    cli.main(
        [
            "generate-client",
            *("-i", str(introspection)),
            *("-o", str(out)),
            *("--name", "linter"),
            *("--ref", "github.com/acme/linter"),
            *("--pin", "4f1c9e"),
        ]
    )

    assert sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()) == [
        "dagger_clients/core/__init__.py",
        "dagger_clients/core/py.typed",
        "dagger_clients/linter/__init__.py",
        "dagger_clients/linter/_target.py",
        "dagger_clients/linter/py.typed",
    ]
    core = (out / "dagger_clients/core/__init__.py").read_text()
    client = (out / "dagger_clients/linter/__init__.py").read_text()
    target = (out / "dagger_clients/linter/_target.py").read_text()
    assert "Linter" not in core
    assert "def as_linter(binding: Binding, /) -> Linter:" in client
    assert 'PIN = "4f1c9e"' in target
    # The client was generated against the core of the same schema.
    digest = core[core.index("CORE_DIGEST = ") :].splitlines()[0]
    assert digest in target


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
