"""Code generation from the names the engine formats (v1.0.0-beta.21 views).

The gate is the schema having ``Query.formatIdentifiers``: a schema without it
generates the code it always did, names file or not.
"""

import json
import sys
import types
import warnings

import pytest
from graphql import GraphQLEnumType, GraphQLEnumValue, build_schema
from graphql import GraphQLField as Field
from graphql import GraphQLList as List
from graphql import GraphQLNonNull as NonNull
from graphql import GraphQLObjectType as Object
from graphql import GraphQLString as String

from codegen import cli
from codegen.cli import load_names
from codegen.generator import (
    NAMES_FORMAT,
    Context,
    _ObjectField,
    generate,
    has_format_identifiers,
)
from codegen.generator import Enum as EnumHandler
from codegen.packages import client_package, core_package, global_package
from dagger.client._core import Context as QueryContext
from dagger.client._core import make_converter

# Names as helpers/names writes them in snake_case. Made up where they differ
# from the legacy conversion, to tell which one was used.
_NAMES = {
    "prerequisiteSHAs": "prerequisite_shas",
    "experimentalWithAllGPUs": "experimental_with_all_gpus",
    "withFS": "with_fs",
    "from": "from",
    "str": "str",
    "gitRef": "git_ref",
    "withRepoURL": "with_repo_url_engine",
    "repoURL": "repo_url_engine",
}

_FORMAT_IDENTIFIERS = "formatIdentifiers(names: [String!]!): [String!]!"


@pytest.mark.parametrize(
    ("graphql", "expected"),
    [
        ("prerequisiteSHAs", "prerequisite_shas"),
        ("experimentalWithAllGPUs", "experimental_with_all_gpus"),
        ("withFS", "with_fs"),
        ("withRepoURL", "with_repo_url_engine"),
        ("from", "from_"),  # reserved keyword, escaped after formatting
        ("str", "str_"),  # builtin, escaped after formatting
        ("envVariable", "env_variable"),  # not in the names: fallback
    ],
)
def test_context_format_name(graphql, expected):
    assert Context(names=_NAMES).format_name(graphql) == expected


def test_context_format_name_without_names():
    # Older schemas have no engine names: keep guessing, as before.
    assert Context().format_name("prerequisiteSHAs") == "prerequisite_sh_as"


def test_has_format_identifiers():
    sdl = "type Query { %s }"
    assert has_format_identifiers(build_schema(sdl % _FORMAT_IDENTIFIERS))
    assert not has_format_identifiers(build_schema(sdl % "version: String!"))


def _git_ref() -> Object:
    return Object(
        "GitRef",
        lambda: {"prerequisiteSHAs": Field(NonNull(List(NonNull(String))))},
    )


def test_legacy_alias():
    parent = _git_ref()
    handler = _ObjectField(
        Context(names=_NAMES),
        "prerequisiteSHAs",
        parent.fields["prerequisiteSHAs"],
        parent,
    )
    assert handler.func_signature() == "async def prerequisite_shas(self) -> list[str]:"
    code = str(handler)
    assert "def prerequisite_sh_as(self, *args, **kwargs):" in code
    assert "return self.prerequisite_shas(*args, **kwargs)" in code
    assert "DeprecationWarning" in code


def test_no_legacy_alias_without_names():
    parent = _git_ref()
    handler = _ObjectField(
        Context(),
        "prerequisiteSHAs",
        parent.fields["prerequisiteSHAs"],
        parent,
    )
    assert handler.legacy_name is None
    assert "**kwargs" not in str(handler)


def test_deprecation_reference():
    ctx = Context(names=_NAMES)
    assert (
        ctx.rewrite_notice("Use `prerequisiteSHAs` instead.", "", "")
        == "Use prerequisite_shas instead."
    )


_NAMES_SCHEMA = """
    type GitRef {
        prerequisiteSHAs: [String!]!
        withRepoURL(repoURL: String!): GitRef!
    }
    type Query {
        gitRef: GitRef!
        %s
    }
"""


def test_generate_with_names():
    code = generate(build_schema(_NAMES_SCHEMA % _FORMAT_IDENTIFIERS), names=_NAMES)

    assert "async def prerequisite_shas(self) -> list[str]:" in code
    assert "def prerequisite_sh_as(self, *args, **kwargs):" in code
    assert "def with_repo_url_engine(self, repo_url_engine: str) -> Self:" in code
    assert 'Arg("repoURL", repo_url_engine),' in code
    assert "def git_ref(self) -> GitRef:" in code
    assert "class GitRef(Type):" in code


def test_generate_without_names():
    code = generate(build_schema(_NAMES_SCHEMA % _FORMAT_IDENTIFIERS))

    assert "async def prerequisite_sh_as(self) -> list[str]:" in code
    assert "def with_repo_url(self, repo_url: str) -> Self:" in code
    assert "**kwargs" not in code


def test_generate_names_gate():
    # A schema without Query.formatIdentifiers keeps the legacy conversion,
    # even with names, and generates exactly what it did without them.
    schema = build_schema(_NAMES_SCHEMA % "")

    code = generate(schema, names=_NAMES)

    assert code == generate(schema)
    assert "async def prerequisite_sh_as(self) -> list[str]:" in code
    assert "def with_repo_url(self, repo_url: str) -> Self:" in code


def test_load_names(tmp_path):
    assert load_names(None) is None

    path = tmp_path / "names.json"
    path.write_text("{}")  # a schema without Query.formatIdentifiers
    assert load_names(path) is None

    path.write_text(
        json.dumps(
            {
                NAMES_FORMAT: {"withGPU": "with_gpu"},
                "PASCAL:CAPITALIZED": {"withGPU": "WithGpu"},
            }
        )
    )
    assert load_names(path) == {"withGPU": "with_gpu"}


def test_enum_render_conventional():
    # With the engine's names, members are SCREAMING_SNAKE and their values
    # the API's values, which go over the wire; the API's other spellings
    # stay as deprecated aliases.
    ctx = Context(names={"Default": "default", "PerSession": "per_session"})
    type_ = GraphQLEnumType(
        "Compression",
        {
            "Gzip": GraphQLEnumValue("Gzip", description="Legacy."),
            "GZIP": GraphQLEnumValue("Gzip", description="Gzip."),
            "PerSession": GraphQLEnumValue("PerSession"),
            "Default": GraphQLEnumValue("Default"),
            "HTTP2": GraphQLEnumValue("HTTP2"),
        },
    )
    assert EnumHandler(ctx).render(type_) == (
        "\n"
        "class Compression(ValueEnum):\n"
        "\n"
        "    DEFAULT = 'Default'\n"
        "    Default = 'Default'\n"
        '    """.. deprecated:: Use DEFAULT instead."""\n'
        "\n"
        "    GZIP = 'Gzip'\n"
        '    """Gzip."""\n'
        "    Gzip = 'Gzip'\n"
        '    """.. deprecated:: Use GZIP instead."""\n'
        "\n"
        "    HTTP2 = 'HTTP2'\n"
        "\n"
        "    PER_SESSION = 'PerSession'\n"
        "    PerSession = 'PerSession'\n"
        '    """.. deprecated:: Use PER_SESSION instead."""\n'
    )


_ENUM_SCHEMA = """
    enum Policy { DEFAULT PER_SESSION Default PerSession }
    type Query {
        policy: Policy!
        run(policy: Policy = PerSession): String!
        %s
    }
"""

_ENUM_NAMES = {
    "DEFAULT": "default",
    "PER_SESSION": "per_session",
    "Default": "default",
    "PerSession": "per_session",
    "run": "run",
}


def test_generate_enum_names():
    code = generate(build_schema(_ENUM_SCHEMA % _FORMAT_IDENTIFIERS), names=_ENUM_NAMES)

    assert "from dagger.client.base import Input, Root, Scalar, Type, ValueEnum" in code
    assert "class Policy(ValueEnum):" in code
    assert "PER_SESSION = 'PER_SESSION'" in code
    assert "PerSession = 'PerSession'" in code
    # A default names the canonical member, not a deprecated alias.
    assert "policy: Policy | None = Policy.PER_SESSION" in code


def test_generate_enum_without_names():
    # Older schemas generate the code they always did.
    code = generate(build_schema(_ENUM_SCHEMA % ""), names=_ENUM_NAMES)

    assert "from dagger.client.base import Enum, Input, Root, Scalar, Type" in code
    assert "class Policy(Enum):" in code
    assert "PerSession = 'PerSession'" in code
    assert "policy: Policy | None = Policy.PerSession" in code


def test_generate_module_enum_values():
    # A dependency module's enum keeps its members as they always were: named
    # by schema value, holding the module's value, and sent by name, since its
    # values aren't schema values.
    sdl = """
        directive @enumValue(value: String!) on ENUM_VALUE
        enum Policy { DEFAULT PER_SESSION }
        enum DepStatus {
            ACTIVE @enumValue(value: "ACTIVE value")
            INACTIVE @enumValue(value: "INACTIVE value")
        }
        type Query {
            policy: Policy!
            status(status: DepStatus = ACTIVE): DepStatus!
            %s
        }
    """
    names = {
        "DEFAULT": "default",
        "PER_SESSION": "per_session",
        "ACTIVE": "active",
        "INACTIVE": "inactive",
        "status": "status",
    }

    code = generate(build_schema(sdl % _FORMAT_IDENTIFIERS), names=names)

    assert (
        "from dagger.client.base import Enum, Input, Root, Scalar, Type, ValueEnum"
        in code
    )
    assert "class Policy(ValueEnum):" in code
    assert "class DepStatus(Enum):" in code
    assert "ACTIVE = 'ACTIVE value'" in code
    assert "status: DepStatus | None = DepStatus.ACTIVE" in code


# A partitioned schema, as a scope generates it: core and one client.
_PARTITIONED = """
    directive @sourceMap(module: String, filename: String)
        on OBJECT | FIELD_DEFINITION | ENUM | ENUM_VALUE | INPUT_OBJECT
    directive @expectedType(name: String!) on FIELD_DEFINITION | ARGUMENT_DEFINITION
    directive @enumValue(value: String!) on ENUM_VALUE

    # As the engine serves it: the legacy values, and the conventional ones
    # standing for them.
    enum ImageMediaTypes {
        OCIMediaTypes
        DockerMediaTypes
        OCI @enumValue(value: "OCIMediaTypes")
        DOCKER @enumValue(value: "DockerMediaTypes")
    }
    type GitCommit {
        id: ID! @expectedType(name: "GitCommit")
        shortSHA: String!
        parentSHAs: [String!]!
        export(mediaTypes: ImageMediaTypes = OCI): String!
    }
    type Linter @sourceMap(module: "linter") {
        id: ID! @expectedType(name: "Linter")
        lintURL(baseURL: String!): String!
    }
    type Query {
        gitCommit: GitCommit!
        linter: Linter! @sourceMap(module: "linter")
        %s
    }
"""

_PARTITIONED_NAMES = {
    "Query": "query",
    "GitCommit": "git_commit",
    "gitCommit": "git_commit",
    "shortSHA": "short_sha",
    "parentSHAs": "parent_shas",
    "export": "export",
    "mediaTypes": "media_types",
    "ImageMediaTypes": "image_media_types",
    "OCI": "oci",
    "DOCKER": "docker",
    "OCIMediaTypes": "oci_media_types",
    "DockerMediaTypes": "docker_media_types",
    "Linter": "linter",
    "linter": "linter",
    "lintURL": "lint_url",
    "baseURL": "base_url",
    "formatIdentifiers": "format_identifiers",
    "names": "names",
}


@pytest.fixture
def load_core(monkeypatch: pytest.MonkeyPatch):
    namespace = types.ModuleType("dagger_clients")
    namespace.__path__ = []
    monkeypatch.setitem(sys.modules, "dagger_clients", namespace)

    def load(code: str) -> types.ModuleType:
        name = "dagger_clients.core"
        module = types.ModuleType(name)
        monkeypatch.setitem(sys.modules, name, module)
        exec(compile(code, name, "exec"), module.__dict__)
        return module

    return load


def test_core_package_with_names(load_core):
    schema = build_schema(_PARTITIONED % _FORMAT_IDENTIFIERS)
    code = core_package(schema, "v1.0.0-beta.21", _PARTITIONED_NAMES)["__init__.py"]

    assert "from dagger.client.base import ValueEnum as _ValueEnum" in code
    assert "class ImageMediaTypes(_ValueEnum):" in code
    assert "media_types: ImageMediaTypes | None = ImageMediaTypes.OCI" in code

    core = load_core(code)
    media = core.ImageMediaTypes
    assert [m.name for m in media] == ["DOCKER", "OCI"]
    assert media.OCIMediaTypes is media.OCI
    # Sent as the value it stands for, which every view has.
    assert media.OCI.value == "OCIMediaTypes"

    commit = core.GitCommit(QueryContext())
    assert commit.short_sha
    assert commit.parent_shas
    # The legacy conversion's name keeps working, deprecated.
    with pytest.warns(DeprecationWarning, match="parent_sh_as"):
        commit.parent_sh_as().close()


def test_core_package_names_gate(load_core):
    # The same schema without formatIdentifiers: the names don't apply.
    schema = build_schema(_PARTITIONED % "")
    code = core_package(schema, "v1.0.0-beta.20", _PARTITIONED_NAMES)["__init__.py"]

    assert code == core_package(schema, "v1.0.0-beta.20")["__init__.py"]
    assert "_ValueEnum" not in code
    assert "class ImageMediaTypes(_Enum):" in code
    assert "def parent_sh_as(self)" in code

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        load_core(code)


def test_client_package_with_names():
    schema = build_schema(_PARTITIONED % _FORMAT_IDENTIFIERS)
    _, files = client_package(schema, "linter", "./linter", names=_PARTITIONED_NAMES)
    code = files["__init__.py"]

    assert "def lint_url(self, base_url: str) -> str:" in code
    assert '_Arg("baseURL", base_url),' in code


def test_global_package_with_names():
    schema = build_schema(_PARTITIONED % _FORMAT_IDENTIFIERS)
    code = global_package([schema], "v1.0.0-beta.21", _PARTITIONED_NAMES)["__init__.py"]

    assert "def git_commit(self) -> GitCommit:" in code
    assert ".git_commit()" in code


def test_cli_reads_names(tmp_path, monkeypatch: pytest.MonkeyPatch):
    schema = build_schema(_ENUM_SCHEMA % _FORMAT_IDENTIFIERS)
    monkeypatch.setattr(cli, "read_schema", lambda _: (schema, "v1.0.0-beta.21"))
    names = tmp_path / "names.json"
    names.write_text(json.dumps({NAMES_FORMAT: _ENUM_NAMES}))
    output = tmp_path / "gen.py"

    cli.main(["generate", "-i", "schema.json", "-n", str(names), "-o", str(output)])
    assert "class Policy(ValueEnum):" in output.read_text()

    cli.main(["generate", "-i", "schema.json", "-o", str(output)])
    assert "class Policy(Enum):" in output.read_text()


@pytest.mark.parametrize("member", ["PER_SESSION", "PerSession"])
def test_value_enum_on_the_wire(member):
    from dagger.client import base
    from dagger.client._core import _scalar_literal

    class Policy(base.ValueEnum):
        DEFAULT = "DEFAULT"
        PER_SESSION = "PER_SESSION"
        Default = "Default"
        PerSession = "PerSession"

    value = Policy[member]
    # Sent by value, so a deprecated alias sends the spelling it stands for.
    assert _scalar_literal(value) == value.value
    conv = make_converter(QueryContext())
    assert conv.unstructure(value) == value.value
    # Read back by name, aliases included.
    assert conv.structure(member, Policy) is value
