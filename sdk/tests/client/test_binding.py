import dataclasses
from typing import Any

import anyio
import graphql
import pytest

from dagger.client import _binding
from dagger.client._binding import ModuleBinding
from dagger.client._core import Context
from dagger.client.base import Type

pytestmark = pytest.mark.anyio

LOCAL = ModuleBinding(name="hello", kind="LOCAL_SOURCE", ref="/mods/hello")
GIT = ModuleBinding(
    name="hello",
    kind="GIT_SOURCE",
    ref="github.com/acme/hello@v1",
    pin="abc123",
)


class FakeSession:
    """Just enough of ClientSession for the preamble and the query builder."""

    def __init__(self, schema: graphql.GraphQLSchema | None = None):
        self.schema = schema or graphql.build_schema("type Query { hello: String }")
        self.response: Any = {"hello": "hi"}
        self.served: set[ModuleBinding] = set()
        self.serve_lock = anyio.Lock()
        self.executed: list[str] = []
        self.refetches = 0
        self.fail_serve = False
        self.fail_refetch = False
        self.delay = 0.0

    async def execute(self, request) -> Any:
        self.executed.append(graphql.print_ast(request.document))
        if self.delay:
            await anyio.sleep(self.delay)
        if self.fail_serve and "serve" in self.executed[-1]:
            msg = "serve failed"
            raise RuntimeError(msg)
        return self.response

    async def refetch_schema(self) -> None:
        self.refetches += 1
        if self.fail_refetch:
            msg = "refetch failed"
            raise RuntimeError(msg)

    async def get_schema(self) -> graphql.GraphQLSchema:
        return self.schema


@dataclasses.dataclass
class FakeConnection:
    session: FakeSession


@pytest.fixture
def session():
    return FakeSession()


@pytest.fixture
def conn(session):
    return FakeConnection(session)


@pytest.fixture
def standalone(monkeypatch):
    monkeypatch.setattr(_binding, "_module_runtime", False)


@pytest.fixture
def module_runtime(monkeypatch):
    monkeypatch.setattr(_binding, "_module_runtime", True)


def test_rejects_a_kind_a_client_cannot_serve():
    with pytest.raises(ValueError, match="DIR_SOURCE"):
        ModuleBinding(name="x", kind="DIR_SOURCE", ref="/x")


def test_local_query_serves_by_workspace_path_under_the_final_name():
    doc = graphql.parse(LOCAL.query())
    assert graphql.print_ast(doc) == graphql.print_ast(
        graphql.parse(
            """
            {
              currentWorkspace {
                moduleSource(path: "/mods/hello") {
                  withName(name: "hello") { asModule { serve } }
                }
              }
            }
            """
        )
    )


def test_git_query_serves_by_ref_and_pin():
    doc = graphql.parse(GIT.query())
    assert graphql.print_ast(doc) == graphql.print_ast(
        graphql.parse(
            """
            {
              moduleSource(refString: "github.com/acme/hello@v1", refPin: "abc123") {
                withName(name: "hello") { asModule { serve } }
              }
            }
            """
        )
    )


@pytest.mark.usefixtures("standalone")
async def test_serves_once_per_session_then_refetches(conn, session):
    await LOCAL.ensure_served(conn)
    await LOCAL.ensure_served(conn)

    assert len(session.executed) == 1
    assert "serve" in session.executed[0]
    assert session.refetches == 1
    assert session.served == {LOCAL}


@pytest.mark.usefixtures("standalone")
async def test_a_second_session_serves_again(conn):
    await LOCAL.ensure_served(conn)
    other = FakeConnection(FakeSession())
    await LOCAL.ensure_served(other)

    assert len(other.session.executed) == 1


@pytest.mark.usefixtures("standalone")
async def test_different_bindings_each_serve(conn, session):
    await LOCAL.ensure_served(conn)
    await GIT.ensure_served(conn)

    assert len(session.executed) == 2
    assert session.served == {LOCAL, GIT}


@pytest.mark.usefixtures("module_runtime")
async def test_module_runtime_never_serves(conn, session, monkeypatch):
    # The variable a user might export is irrelevant: only the runtime's own
    # mark counts.
    monkeypatch.delenv("DAGGER_MODULE", raising=False)
    await LOCAL.ensure_served(conn)

    assert session.executed == []
    assert session.refetches == 0


@pytest.mark.usefixtures("standalone")
async def test_dagger_module_variable_does_not_mark_a_runtime(
    conn, session, monkeypatch
):
    monkeypatch.setenv("DAGGER_MODULE", "hello")
    await LOCAL.ensure_served(conn)

    assert len(session.executed) == 1


@pytest.mark.usefixtures("standalone")
async def test_concurrent_first_uses_serve_once(conn, session):
    session.delay = 0.01
    async with anyio.create_task_group() as tg:
        for _ in range(5):
            tg.start_soon(LOCAL.ensure_served, conn)

    assert len(session.executed) == 1
    assert session.refetches == 1


@pytest.mark.usefixtures("standalone")
async def test_failed_serve_marks_nothing_and_retries(conn, session):
    session.fail_serve = True
    with pytest.raises(RuntimeError, match="serve failed"):
        await LOCAL.ensure_served(conn)
    assert session.served == set()
    assert session.refetches == 0

    session.fail_serve = False
    await LOCAL.ensure_served(conn)
    assert session.served == {LOCAL}
    assert len(session.executed) == 2


@pytest.mark.usefixtures("standalone")
async def test_failed_refetch_marks_nothing(conn, session):
    session.fail_refetch = True
    with pytest.raises(RuntimeError, match="refetch failed"):
        await LOCAL.ensure_served(conn)

    assert session.served == set()


def test_binding_travels_with_the_query_context(conn):
    ctx = Context(conn).with_binding(LOCAL)

    assert ctx.with_binding(LOCAL) is ctx
    assert ctx.root_select("hello", []).bindings == (LOCAL,)
    assert ctx.select("Query", "hello", []).bindings == (LOCAL,)
    assert ctx.select_id("Hello", "id").bindings == (LOCAL,)


@pytest.mark.usefixtures("standalone")
async def test_execute_serves_before_the_query(conn, session):
    ctx = Context(conn).with_binding(LOCAL).root_select("hello", [])

    assert await ctx.execute(str) == "hi"
    assert len(session.executed) == 2
    assert "serve" in session.executed[0]
    assert "hello" in session.executed[1]
    assert "serve" not in session.executed[1]


OBJECT_SDL = """
interface Node { id: ID! }
type Hello implements Node { id: ID! sync: ID! }
type Query { hello: Hello! hellos: [Hello!]! node(id: ID!): Node }
"""


class Hello(Type):
    __slots__ = ()


@pytest.fixture
def object_conn():
    return FakeConnection(FakeSession(graphql.build_schema(OBJECT_SDL)))


@pytest.mark.usefixtures("standalone")
async def test_execute_object_list_serves_and_keeps_the_binding(object_conn):
    object_conn.session.response = {"hellos": [{"id": "hello-1"}]}
    ctx = Context(object_conn).with_binding(LOCAL).root_select("hellos", [])

    [hello] = await ctx.execute_object_list(Hello)

    assert "serve" in object_conn.session.executed[0]
    assert hello._ctx.bindings == (LOCAL,)


@pytest.mark.usefixtures("standalone")
async def test_execute_sync_serves_and_keeps_the_binding(object_conn):
    object_conn.session.response = {"hello": {"sync": "hello-1"}}
    ctx = Context(object_conn).with_binding(LOCAL).root_select("hello", [])

    synced = await ctx.execute_sync(Hello(ctx))

    assert "serve" in object_conn.session.executed[0]
    assert synced._ctx.bindings == (LOCAL,)
