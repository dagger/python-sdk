import copy
import re

import graphql
import pytest

from codegen import ast
from codegen.partition import (
    PartitionError,
    client_module_name,
    client_module_names,
    entry_field,
    narrow_to_core,
    owner_of,
    ownership,
    root_type_for,
)

SDL = """
interface Node { id: ID! }

type Query {
    container: Container!
    e2E: E2E!
    hello(name: String!): Hello!
    node(id: ID!): Node
}

type Container implements Node {
    id: ID!
    stdout: String!
}

type Binding {
    name: String!
    asHello: Hello!
}

type Hello implements Node {
    id: ID!
    greet(name: String!): String!
    report(kind: HelloKind = FULL): HelloReport!
    build(opts: HelloOpts): Container!
}

type HelloReport implements Node {
    id: ID!
    text: String!
}

enum HelloKind { FULL SHORT }

input HelloOpts { verbose: Boolean }

scalar HelloToken

type E2E implements Node {
    id: ID!
    run: String!
}
"""

OWNERS = {
    "Hello": "hello",
    "HelloReport": "hello",
    "HelloKind": "hello",
    "HelloOpts": "hello",
    "HelloToken": "hello",
    ("Hello", "greet"): "hello",
    ("Hello", "report"): "hello",
    ("Hello", "build"): "hello",
    ("Query", "hello"): "hello",
    ("Binding", "asHello"): "hello",
    "E2E": "e2e",
    ("E2E", "run"): "e2e",
    ("Query", "e2E"): "e2e",
}


@pytest.fixture
def schema(introspection):
    return introspection(SDL, OWNERS)["__schema"]


def type_names(schema) -> set[str]:
    return {t["name"] for t in schema["types"]}


def field_names(schema, type_name: str) -> set[str]:
    type_ = next(t for t in schema["types"] if t["name"] == type_name)
    return {f["name"] for f in type_["fields"]}


def test_owner_of_reads_the_json_quoted_module():
    directives = [{"name": "sourceMap", "args": [{"name": "module", "value": '"e2e"'}]}]
    assert owner_of(directives) == "e2e"
    assert owner_of([]) == ""
    assert owner_of(None) == ""


def test_ownership_splits_types_and_fields(schema):
    owned = ownership(schema)
    assert owned.owned_types("hello") == {
        "Hello",
        "HelloReport",
        "HelloKind",
        "HelloOpts",
        "HelloToken",
    }
    # Fields on owned types are the type's; only fields on core types are listed.
    assert owned.owned_fields("hello") == {("Query", "hello"), ("Binding", "asHello")}
    assert owned.owned_types("e2e") == {"E2E"}
    assert owned.owned_fields("e2e") == {("Query", "e2E")}
    assert owned.modules == {"hello", "e2e"}


def test_scalar_ownership_is_read(schema):
    assert ownership(schema).types["HelloToken"] == "hello"


def test_insert_stubs_leaves_the_shared_specified_scalars_alone(schema):
    built = graphql.build_client_schema({"__schema": schema})
    ast.insert_stubs(schema, built)

    assert built.get_type("HelloToken").ast_node is not None
    assert graphql.GraphQLString.ast_node is None
    assert graphql.GraphQLID.ast_node is None


def test_narrow_to_core_drops_owned_symbols_and_prunes_references(schema):
    core = narrow_to_core(schema)

    assert type_names(core) == {
        "Query",
        "Node",
        "Container",
        "Binding",
        "String",
        "Boolean",
        "ID",
    } | {t["name"] for t in schema["types"] if t["name"].startswith("__")}
    assert field_names(core, "Query") == {"container", "node"}
    assert field_names(core, "Binding") == {"name"}
    node = next(t for t in core["types"] if t["name"] == "Node")
    assert [t["name"] for t in node["possibleTypes"]] == ["Container"]


def test_narrow_to_core_builds_a_valid_client_schema(schema):
    built = graphql.build_client_schema({"__schema": narrow_to_core(schema)})
    assert built.get_type("Container") is not None
    assert built.get_type("Hello") is None
    assert "hello" not in built.query_type.fields


def test_narrow_to_core_does_not_mutate_its_input(schema):
    before = copy.deepcopy(schema)
    narrow_to_core(schema)
    assert schema == before


CORE_SDL = """
interface Node { id: ID! }
type Container implements Node { id: ID! stdout: String! }
"""


def test_core_is_the_same_whichever_module_the_schema_was_bound_to(introspection):
    bound_to_hello = introspection(
        CORE_SDL
        + """
        type Query { container: Container! hello: Hello! }
        type Hello implements Node { id: ID! greet: String! }
        """,
        {"Hello": "hello", ("Hello", "greet"): "hello", ("Query", "hello"): "hello"},
    )["__schema"]
    bound_to_e2e = introspection(
        CORE_SDL
        + """
        type Query { container: Container! e2E: E2E! }
        type E2E implements Node { id: ID! run: String! }
        """,
        {"E2E": "e2e", ("E2E", "run"): "e2e", ("Query", "e2E"): "e2e"},
    )["__schema"]

    assert narrow_to_core(bound_to_hello) == narrow_to_core(bound_to_e2e)


def test_entry_field_and_root_type_are_read_off_the_schema(schema):
    assert entry_field(schema, "hello")["name"] == "hello"
    assert root_type_for(schema, "hello") == "Hello"
    # Not a capitalization rule: module e2e has root type E2E.
    assert entry_field(schema, "e2e")["name"] == "e2E"
    assert root_type_for(schema, "e2e") == "E2E"


def test_entry_field_requires_exactly_one_owned_query_field(schema):
    with pytest.raises(PartitionError, match="expected exactly 1"):
        entry_field(schema, "nobody")


def test_unions_are_rejected(introspection):
    result = introspection(
        """
        type Query { pick: Pick! }
        type A { a: String! }
        type B { b: String! }
        union Pick = A | B
        """
    )
    with pytest.raises(PartitionError, match="union"):
        ownership(result["__schema"])


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        ("hello", "hello"),
        ("hello-world", "hello_world"),
        ("helloWorld", "hello_world"),
        ("HelloWorld", "hello_world"),
        ("hello.world", "hello_world"),
        ("import", "import_"),
        ("e2e", "e2e"),
        ("my-sdk2", "my_sdk2"),
    ],
)
def test_client_module_name(module, expected):
    assert client_module_name(module) == expected


@pytest.mark.parametrize("module", ["__init__", "__main__", "2fa", "", "a b"])
def test_client_module_name_rejects_unusable_names(module):
    with pytest.raises(PartitionError):
        client_module_name(module)


def test_client_module_names_normalizes_each_in_order():
    assert client_module_names(["hello-world", "e2e", "import"]) == [
        "hello_world",
        "e2e",
        "import_",
    ]


def test_client_module_names_rejects_two_that_share_a_file():
    with pytest.raises(PartitionError, match=re.escape("foo_bar.py")):
        client_module_names(["foo-bar", "dep", "foo_bar"])
