"""The hand-written API calls send what the generated bindings send."""

import pytest

from dagger.client import gen
from dagger.client._session import SharedConnection
from dagger.client.gen import dag
from dagger.mod import _api

Kind = gen.TypeDefKind
Policy = gen.FunctionCachePolicy


def string(api):
    kind = "STRING_KIND" if api is _api else Kind.STRING_KIND
    return api.type_def().with_kind(kind)


def func(api):
    return api.function("fn", string(api))


def json(api, value: str):
    return value if api is _api else gen.JSON(value)


TYPE_DEF_CASES = {
    "optional": lambda api: api.type_def().with_optional(True),
    "kind": string,
    "list_of": lambda api: api.type_def().with_list_of(string(api)),
    "scalar": lambda api: api.type_def().with_scalar("S", description="doc"),
    "scalar_no_doc": lambda api: api.type_def().with_scalar("S", description=None),
    "enum": lambda api: api.type_def().with_enum("E", description=None),
    "enum_member": lambda api: (
        api.type_def()
        .with_enum("E", description="doc")
        .with_enum_member("A", value="a", description=None, deprecated="old")
    ),
    "interface": lambda api: api.type_def().with_interface("I", description="doc"),
    "object": lambda api: api.type_def().with_object(
        "O", description=None, deprecated="old"
    ),
    "field": lambda api: (
        api.type_def()
        .with_object("O")
        .with_field("f", string(api), description="doc", deprecated=None)
    ),
    "function": lambda api: api.type_def().with_object("O").with_function(func(api)),
    "constructor": lambda api: (
        api.type_def().with_object("O").with_constructor(func(api))
    ),
}

FUNCTION_CASES = {
    "description": lambda api: func(api).with_description("doc"),
    "cache_never": lambda api: func(api).with_cache_policy(
        "Never" if api is _api else Policy.Never
    ),
    "cache_session": lambda api: func(api).with_cache_policy(
        "PerSession" if api is _api else Policy.PerSession
    ),
    "cache_ttl": lambda api: func(api).with_cache_policy(
        "Default" if api is _api else Policy.Default, time_to_live="5m"
    ),
    "deprecated": lambda api: func(api).with_deprecated(reason="old"),
    "markers": lambda api: (
        func(api).with_check().with_generator().with_up().with_agent()
    ),
    "arg": lambda api: func(api).with_arg(
        "a",
        string(api).with_optional(True),
        description="doc",
        default_value=json(api, '"x"'),
        default_path="./src",
        default_address="alpine:latest",
        ignore=[".venv"],
        deprecated="old",
    ),
    "arg_bare": lambda api: func(api).with_arg(
        "a",
        string(api),
        description=None,
        default_value=None,
        default_path=None,
        default_address=None,
        ignore=None,
        deprecated=None,
    ),
}

MODULE_CASES = {
    "description": lambda api: api.module().with_description("doc"),
    "object": lambda api: api.module().with_object(api.type_def().with_object("O")),
    "interface": lambda api: api.module().with_interface(
        api.type_def().with_interface("I")
    ),
    "enum": lambda api: api.module().with_enum(api.type_def().with_enum("E")),
    "error": lambda api: api.error("boom").with_value("k", json(api, "1")),
}

CASES = {
    **{f"type_def.{k}": v for k, v in TYPE_DEF_CASES.items()},
    **{f"function.{k}": v for k, v in FUNCTION_CASES.items()},
    **{f"module.{k}": v for k, v in MODULE_CASES.items()},
}


@pytest.mark.parametrize("build", CASES.values(), ids=CASES.keys())
def test_selections_match_generated_bindings(selections, build):
    assert selections(build(_api)) == selections(build(dag))


class FakeSession:
    """Answers any function call query, and keeps what it was asked."""

    def __init__(self):
        self.queries: list[str] = []

    async def execute(self, query: str):
        self.queries.append(query)
        return {
            "error": {"id": "error-id"},
            "currentFunctionCall": {
                "parentName": "Main",
                "name": "hello",
                "parent": "{}",
                "inputArgs": [{"name": "who", "value": '"you"'}],
                "returnValue": None,
                "returnError": None,
            },
        }


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch):
    fake = FakeSession()
    monkeypatch.setattr(SharedConnection, "session", fake)
    return fake


async def _drive(api, session: FakeSession, value) -> list[str]:
    call = api.current_function_call()
    assert await call.parent_name() == "Main"
    assert await call.name() == "hello"
    assert await call.parent() == "{}"
    await call.return_value(value)
    await call.return_error(api.error("boom"))
    queries, session.queries = session.queries, []
    return queries


@pytest.mark.anyio
async def test_function_call_queries_match_generated_bindings(session: FakeSession):
    raw = await _drive(_api, session, '"hi"')
    generated = await _drive(dag, session, gen.JSON('"hi"'))

    assert raw == generated
    assert 'returnValue(value: "\\"hi\\"")' in raw[3]
    assert 'returnError(error: "error-id")' in raw[5]


@pytest.mark.anyio
async def test_input_args_come_in_one_query(session: FakeSession):
    args = await _api.current_function_call().input_args()

    assert args == [_api.ArgValue(name="who", value='"you"')]
    assert session.queries == [
        "query {\n"
        "  currentFunctionCall {\n"
        "    inputArgs {\n"
        "      name\n"
        "      value\n"
        "    }\n"
        "  }\n"
        "}"
    ]


def test_enum_values_go_out_bare():
    query = (
        _api.type_def()
        .with_kind("STRING_KIND")
        ._ctx.select("TypeDef", "id", [])
        .build()
    )

    assert "withKind(kind: STRING_KIND)" in query
