import pytest
from graphql import build_schema

from codegen.partition import (
    ClientError,
    ClientNameError,
    check_attribution,
    contributed_fields,
    core_digest,
    modules,
    own_fields,
    package_name,
    package_names,
    source_module,
)

_DIRECTIVES = """
    directive @sourceMap(module: String, filename: String)
        on OBJECT | FIELD_DEFINITION | ENUM | ENUM_VALUE | INPUT_FIELD_DEFINITION
    directive @expectedType(name: String!) on FIELD_DEFINITION | ARGUMENT_DEFINITION
"""

_CORE = """
    type Directory { id: ID! @expectedType(name: "Directory") }
"""

_LINTER = """
    type Linter @sourceMap(module: "linter", filename: "main.py") {
        lint: String! @sourceMap(module: "linter")
    }
    enum LinterLevel @sourceMap(module: "linter") { LOW HIGH }
"""

_GLOW = """
    type Glow @sourceMap(module: "glow") { render: String! size: Int! }
"""


def _schema(*clients: str, core: str = _CORE):
    binding = ["name: String!"]
    query = ["directory: Directory!"]
    if _LINTER in clients:
        binding.append('asLinter: Linter! @sourceMap(module: "linter")')
        query.append('linter: Linter! @sourceMap(module: "linter")')
    if _GLOW in clients:
        binding.append('asGlow: Glow! @sourceMap(module: "glow")')
        query.append('glow: Glow! @sourceMap(module: "glow")')
    return build_schema(
        _DIRECTIVES
        + core
        + "".join(clients)
        + f"type Binding {{ {' '.join(binding)} }}"
        + f"type Query {{ {' '.join(query)} }}"
    )


def test_source_module():
    schema = _schema(_LINTER)

    assert source_module(schema.type_map["Linter"].ast_node) == "linter"
    assert source_module(schema.type_map["LinterLevel"].ast_node) == "linter"
    assert source_module(schema.type_map["Directory"].ast_node) is None
    # A type built without a definition is core, like a type with no directive.
    assert source_module(None) is None


def test_modules():
    assert modules(_schema()) == []
    assert modules(_schema(_LINTER, _GLOW)) == ["glow", "linter"]


def test_own_fields_leave_out_contributed_fields():
    schema = _schema(_LINTER, _GLOW)

    assert list(own_fields(schema.type_map["Binding"])) == ["name"]
    assert list(own_fields(schema.type_map["Query"])) == ["directory"]
    # Every field of a client's type goes with the type.
    assert list(own_fields(schema.type_map["Linter"])) == ["lint"]


def test_check_attribution_accepts_fields_contributed_to_core_types():
    assert check_attribution(_schema(_LINTER, _GLOW)) is None


def test_check_attribution_refuses_a_field_of_one_client_given_to_another():
    linter = """
        type Linter @sourceMap(module: "linter") {
            lint: String! @sourceMap(module: "glow")
        }
    """

    with pytest.raises(
        ClientError, match=r'"Linter\.lint" .* "glow", but .* "Linter" .* "linter"'
    ):
        check_attribution(_schema(linter))


@pytest.mark.parametrize(
    ("member", "place"),
    [
        ('enum Severity { LOW HIGH @sourceMap(module: "linter") }', "Severity.HIGH"),
        ('input Options { level: Int @sourceMap(module: "linter") }', "Options.level"),
    ],
)
def test_check_attribution_refuses_a_member_that_is_not_a_field(member, place):
    with pytest.raises(
        ClientError, match=f'"{place}" .* "linter", but only a field of a core'
    ):
        check_attribution(_schema(_LINTER, core=_CORE + member))


def test_contributed_fields():
    schema = _schema(_LINTER, _GLOW)

    assert [(t.name, name) for t, name, _ in contributed_fields(schema, "linter")] == [
        ("Binding", "asLinter"),
        ("Query", "linter"),
    ]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("linter", "linter"),
        ("my-project-dev", "my_project_dev"),
        ("My.Project", "my_project"),
    ],
)
def test_package_name(name: str, expected: str):
    assert package_name(name) == expected


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("my linter", "not a Python identifier"),
        ("2fast", "not a Python identifier"),
        ("", "not a Python identifier"),
        ("class", "keyword"),
        ("Import", "keyword"),
        ("_private", 'starts with "_"'),
        ("-dash", 'starts with "_"'),
        ("core", "core bindings"),
        ("CORE", "core bindings"),
    ],
)
def test_package_name_refused(name: str, reason: str):
    with pytest.raises(ClientNameError, match=reason):
        package_name(name)


def test_package_names_refuse_a_collision():
    assert package_names(["my-linter", "glow"]) == {
        "my-linter": "my_linter",
        "glow": "glow",
    }
    with pytest.raises(ClientNameError, match=r'"my-linter" and "my\.linter"'):
        package_names(["my-linter", "glow", "my.linter"])


def test_core_digest_ignores_clients():
    digest = core_digest(_schema())

    assert digest.startswith("sha256:")
    assert core_digest(_schema(_LINTER)) == digest
    # Glow brings in `Int`, which the schema lists only because of it.
    assert core_digest(_schema(_LINTER, _GLOW)) == digest


@pytest.mark.parametrize(
    "core",
    [
        # a new field
        'type Directory { id: ID! @expectedType(name: "Directory") name: String! }',
        # a directive that the printed schema doesn't show
        'type Directory { id: ID! @expectedType(name: "File") }',
    ],
)
def test_core_digest_follows_core(core: str):
    assert core_digest(_schema(_LINTER, core=core)) != core_digest(_schema(_LINTER))


def test_core_digest_follows_the_compatibility_mode_only():
    schema = _schema()

    assert core_digest(schema, legacy_sdk_compat=True) != core_digest(schema)
    # Not the version: two modern versions render one core.
    assert core_digest(schema, legacy_sdk_compat=False) == core_digest(schema)


def test_core_digest_ignores_member_order():
    reordered = """
        type Directory { name: String! id: ID! @expectedType(name: "Directory") }
    """
    ordered = """
        type Directory { id: ID! @expectedType(name: "Directory") name: String! }
    """
    assert core_digest(_schema(core=reordered)) == core_digest(_schema(core=ordered))
