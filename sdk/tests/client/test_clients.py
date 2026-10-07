"""The SDK side of a generated client: its session, its target, its load."""

import gc
import logging
import threading
import time
import weakref

import anyio
import anyio.lowlevel
import pytest

import dagger
from dagger._exceptions import (
    ClientLoadError,
    QueryError,
    QueryErrorValue,
    StaleClientError,
    TransportError,
)
from dagger.client import (
    Session,
    Target,
    check_core,
    client_root,
    client_select,
    default_session,
    registering_types,
)
from dagger.client._core import Arg, Context
from dagger.client._session import BaseConnection, as_session
from dagger.client.base import Type

pytestmark = pytest.mark.anyio

GLOW = Target(name="glow", ref="github.com/eunomie/glow", pin="4f1c9e")
# The module's own path in the workspace: that is what the engine serves.
LINTER = Target(name="linter", ref="./.dagger/modules/linter")

MISSING_FIELD = 'Cannot query field "glow" on type "Query".'
# What beta.13 answers: a validation error, before anything runs.
VALIDATION = {"code": "GRAPHQL_VALIDATION_FAILED"}


class Answer(dict):
    """A response with every field, at every depth."""

    def __missing__(self, key):
        return Answer()


class FakeSession:
    """Answers every query, and keeps what it was asked.

    A query that contains a key of ``fail`` raises that value instead.
    """

    def __init__(self):
        self.data: dict = Answer()
        self.queries: list[str] = []
        self.fail: dict[str, Exception] = {}
        self.checkpoints = 0

    async def execute(self, query: str):
        self.queries.append(query)
        for needle, error in self.fail.items():
            if needle in query:
                raise error
        if is_load(query):
            # Let another task in, so a missing lock shows as a second load.
            for _ in range(self.checkpoints):
                await anyio.lowlevel.checkpoint()
            return None
        return self.data

    async def close(self):
        pass

    @property
    def loads(self) -> list[str]:
        return [q for q in self.queries if is_load(q)]


def is_load(query: str) -> bool:
    return "serve" in query


class FakeConnection(BaseConnection):
    def __init__(self):
        self.session = FakeSession()


def session() -> Session:
    return Session(FakeConnection())


class Query(Type):
    pass


class Glow(Type):
    def again(self) -> "Glow":
        return Glow(self._select("again", []))

    async def output(self) -> str:
        return await self._select("output", []).execute(str)


def glow(*, session: Session | None = None) -> Glow:
    return client_root(Glow, GLOW, "glow", [], session=session)


async def test_load_once_per_session():
    s = session()
    s.session.data = {"glow": {"output": "hi", "again": {"output": "again"}}}

    assert await glow(session=s).output() == "hi"
    assert await glow(session=s).again().output() == "again"

    assert len(s.session.loads) == 1
    assert s.session.queries.index(s.session.loads[0]) == 0


async def test_load_once_per_target_in_each_session():
    first, second = session(), session()

    await glow(session=first).output()
    await glow(session=second).output()
    await glow(session=first).output()

    assert len(first.session.loads) == 1
    assert len(second.session.loads) == 1


async def test_concurrent_queries_load_once():
    s = session()
    s.session.checkpoints = 3

    async with anyio.create_task_group() as tg:
        tg.start_soon(glow(session=s).output)
        tg.start_soon(glow(session=s).output)

    assert len(s.session.loads) == 1


async def test_git_target_loads_before_its_query():
    s = session()

    await glow(session=s).output()

    load, query = s.session.queries
    assert is_load(load)
    assert GLOW.ref in load
    assert GLOW.pin in load
    assert query == "query {\n  glow {\n    output\n  }\n}"


async def test_local_target_loads_before_its_query():
    s = session()

    await client_root(Glow, LINTER, "linter", [], session=s).output()

    load, query = s.session.queries
    assert is_load(load)
    assert LINTER.ref in load
    assert query == "query {\n  linter {\n    output\n  }\n}"


NO_SERVE_MODULE = QueryError(
    [
        QueryErrorValue(
            'Cannot query field "serveModule" on type "Query".', extensions=VALIDATION
        )
    ],
    "query",
)


async def test_engine_without_serve_module_fails_the_load_not_as_stale():
    # An engine below the floor lacks the field. That is the engine's age,
    # not the client's, so it is no stale client: regenerating cannot help.
    s = session()
    s.session.fail["serveModule"] = NO_SERVE_MODULE

    with pytest.raises(ClientLoadError) as info:
        await glow(session=s).output()

    assert not isinstance(info.value, StaleClientError)
    assert info.value.__cause__ is NO_SERVE_MODULE
    assert len(s.session.loads) == 1


async def test_failed_load_names_the_target_and_the_cause():
    s = session()
    cause = TransportError("engine went away")
    s.session.fail["serve"] = cause

    with pytest.raises(ClientLoadError, match="glow") as info:
        await glow(session=s).output()

    assert info.value.target == GLOW
    assert "engine went away" in str(info.value)
    assert info.value.__cause__ is cause
    assert isinstance(info.value, dagger.ClientLoadError)


async def test_failed_load_is_retried():
    s = session()
    s.session.fail["serve"] = TransportError("not yet")

    with pytest.raises(ClientLoadError):
        await glow(session=s).output()
    s.session.fail.clear()
    await glow(session=s).output()

    assert len(s.session.loads) == 2


async def test_conflict_names_both_descriptors_pins_included():
    s = session()
    repinned = Target(name=GLOW.name, ref=GLOW.ref, pin="9b2d7a")

    await glow(session=s).output()
    with pytest.raises(ClientLoadError) as info:
        await client_root(Glow, repinned, "glow", [], session=s).output()

    assert repr(GLOW) in str(info.value)
    assert repr(repinned) in str(info.value)
    assert info.value.target == repinned


async def test_conflict_after_a_failed_load_does_not_say_loaded():
    s = session()
    s.session.fail["serve"] = TransportError("engine went away")
    other = Target(name="glow", ref="github.com/eunomie/glow-fork")

    with pytest.raises(ClientLoadError):
        await glow(session=s).output()
    with pytest.raises(ClientLoadError) as info:
        await client_root(Glow, other, "glow", [], session=s).output()

    assert "already holds" in str(info.value)
    assert "loaded" not in str(info.value)


async def test_no_target_attaches_nothing():
    s = session()
    s.session.data = {"version": "v1"}
    root = client_root(Query, None, None, [], session=s)

    assert root._ctx.targets == frozenset()
    assert not root._ctx.selections
    assert await root._select("version", []).execute(str) == "v1"
    assert s.session.loads == []


def test_root_field_with_args():
    s = session()
    obj = client_root(Glow, GLOW, "glow", [Arg("name", "x")], session=s)

    assert obj._ctx.targets == {GLOW}
    assert obj._ctx.build() == 'query {\n  glow(name: "x")\n}'


def test_selections_keep_the_target():
    obj = glow(session=session())

    assert obj.again()._ctx.targets == {GLOW}
    assert obj._select("output", []).targets == {GLOW}


def test_default_session_when_none_given():
    assert glow()._ctx.conn is default_session()
    assert default_session() is default_session()


def test_default_session_is_created_once_under_contention(monkeypatch):
    from dagger.client import _session

    in_init = threading.Event()

    class SlowSession(Session):
        def __init__(self, connection=None):
            in_init.set()
            # Long enough for the other thread to read the default meanwhile.
            time.sleep(0.1)
            super().__init__(connection)

    monkeypatch.setattr(_session, "_default", None)
    monkeypatch.setattr(_session, "Session", SlowSession)
    seen: list[Session] = []

    def second():
        in_init.wait()
        seen.append(default_session())

    thread = threading.Thread(target=second)
    thread.start()
    seen.append(default_session())
    thread.join()

    assert seen[0] is seen[1]


def test_one_session_per_connection():
    conn = FakeConnection()

    assert as_session(conn) is as_session(conn)


def over(conn: BaseConnection, target: Target) -> Glow:
    """A client built on a bare connection, the way a root type does."""
    ctx = Context(conn, targets=frozenset([target])).root_select("glow", [])
    return Glow(ctx)


async def test_executions_through_one_connection_load_once():
    conn = FakeConnection()

    await over(conn, GLOW).output()
    await over(conn, GLOW).output()

    assert len(conn.session.loads) == 1


async def test_name_guard_holds_across_wrappers_of_one_connection():
    conn = FakeConnection()
    other = Target(name="glow", ref="github.com/eunomie/glow-fork")

    await over(conn, GLOW).output()
    with pytest.raises(ClientLoadError):
        await over(conn, other).output()


async def test_closing_a_bare_connection_forgets_what_was_loaded():
    conn = FakeConnection()

    await over(conn, GLOW).output()
    await conn.close()
    await over(conn, GLOW).output()

    assert len(conn.session.loads) == 2


def test_session_dies_with_its_connection():
    conn = FakeConnection()
    gone = weakref.ref(conn)
    as_session(conn)

    del conn
    gc.collect()

    assert gone() is None


def test_client_select_keeps_the_receiver_session():
    s = session()
    receiver = client_root(Query, None, None, [], session=s)

    ctx = client_select(receiver, GLOW, "asGlow", [Arg("strict", True)])

    assert ctx.conn is s
    assert ctx.targets == {GLOW}
    assert [(f.type_name, f.name) for f in ctx.selections] == [("Query", "asGlow")]
    assert ctx.build() == "query {\n  asGlow(strict: true)\n}"


def test_client_select_adds_to_the_receiver_targets():
    receiver = client_root(Glow, LINTER, "linter", [], session=session())

    ctx = client_select(receiver, GLOW, "asGlow", [])

    assert ctx.targets == {LINTER, GLOW}
    assert [f.name for f in ctx.selections] == ["linter", "asGlow"]


async def test_client_select_loads_in_the_receiver_session():
    s = session()
    receiver = client_root(Query, None, None, [], session=s)

    await Glow(client_select(receiver, GLOW, "asGlow", [])).output()

    assert len(s.session.loads) == 1


async def test_missing_field_becomes_stale_client_error():
    s = session()
    error = QueryError([QueryErrorValue(MISSING_FIELD, extensions=VALIDATION)], "query")
    s.session.fail["output"] = error

    with pytest.raises(StaleClientError, match="dagger generate") as info:
        await glow(session=s).output()

    assert str(info.value) == (
        f"{MISSING_FIELD} The client 'glow' from github.com/eunomie/glow at 4f1c9e "
        "is out of date. Run `dagger generate`."
    )
    assert info.value.__cause__ is error


async def test_stale_message_names_each_client_and_its_address():
    s = session()
    s.session.fail["output"] = QueryError(
        [QueryErrorValue(MISSING_FIELD, extensions=VALIDATION)], "query"
    )
    receiver = client_root(Glow, LINTER, "linter", [], session=s)

    with pytest.raises(StaleClientError) as info:
        await Glow(client_select(receiver, GLOW, "asGlow", [])).output()

    assert str(info.value).endswith(
        "The clients 'glow' from github.com/eunomie/glow at 4f1c9e, "
        "'linter' from ./.dagger/modules/linter are out of date. Run `dagger generate`."
    )


async def test_missing_field_without_target_stays_a_query_error():
    s = session()
    s.session.fail["version"] = QueryError(
        [QueryErrorValue(MISSING_FIELD, extensions=VALIDATION)], "query"
    )
    root = client_root(Query, None, None, [], session=s)

    with pytest.raises(QueryError) as info:
        await root._select("version", []).execute(str)

    assert not isinstance(info.value, StaleClientError)


async def test_other_query_errors_pass_through():
    s = session()
    error = QueryError([QueryErrorValue("boom")], "query")
    s.session.fail["output"] = error

    with pytest.raises(QueryError) as info:
        await glow(session=s).output()

    assert info.value is error


async def test_missing_field_in_a_later_error_is_stale():
    s = session()
    error = QueryError(
        [
            QueryErrorValue("boom"),
            QueryErrorValue(MISSING_FIELD, extensions=VALIDATION),
        ],
        "query",
    )
    s.session.fail["output"] = error

    with pytest.raises(StaleClientError) as info:
        await glow(session=s).output()

    assert MISSING_FIELD in str(info.value)


async def test_resolver_error_with_the_phrase_stays_a_query_error():
    s = session()
    error = QueryError(
        [QueryErrorValue(MISSING_FIELD, path=["glow"], extensions=VALIDATION)],
        "query",
    )
    s.session.fail["output"] = error

    with pytest.raises(QueryError) as info:
        await glow(session=s).output()

    assert info.value is error


async def test_internal_error_with_the_phrase_stays_a_query_error():
    s = session()
    internal = {"code": "INTERNAL_SERVER_ERROR"}
    error = QueryError([QueryErrorValue(MISSING_FIELD, extensions=internal)], "query")
    s.session.fail["output"] = error

    with pytest.raises(QueryError) as info:
        await glow(session=s).output()

    assert not isinstance(info.value, StaleClientError)


async def test_phrase_inside_a_message_stays_a_query_error():
    s = session()
    error = QueryError([QueryErrorValue(f"stdout: {MISSING_FIELD}")], "query")
    s.session.fail["output"] = error

    with pytest.raises(QueryError) as info:
        await glow(session=s).output()

    assert info.value is error


def test_check_core_accepts_a_matching_digest():
    check_core("glow", "sha256:aa", "sha256:aa")


def test_check_core_refuses_a_stale_client():
    with pytest.raises(StaleClientError, match="dagger generate") as info:
        check_core("glow", "sha256:aa", "sha256:bb")

    assert isinstance(info.value, ImportError)
    assert isinstance(info.value, dagger.StaleClientError)
    assert "glow" in str(info.value)
    assert "sha256:aa" in str(info.value)
    assert "sha256:bb" in str(info.value)


def test_check_core_warns_while_registering(caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.WARNING), registering_types():
        check_core("glow", "sha256:aa", "sha256:bb")

    assert len(caplog.records) == 1
    assert "sha256:aa" in caplog.text
    assert "sha256:bb" in caplog.text
    assert "dagger generate" in caplog.text


def test_registering_ends_with_its_block():
    with registering_types():
        pass

    with pytest.raises(StaleClientError):
        check_core("glow", "sha256:aa", "sha256:bb")


async def test_registering_ends_for_tasks_started_inside_it():
    started, left = anyio.Event(), anyio.Event()

    async def child():
        started.set()
        await left.wait()
        with pytest.raises(StaleClientError):
            check_core("glow", "sha256:aa", "sha256:bb")

    async with anyio.create_task_group() as tg:
        with registering_types():
            tg.start_soon(child)
            await started.wait()
        left.set()


def test_registering_covers_threads_started_inside_it(
    caplog: pytest.LogCaptureFixture,
):
    with caplog.at_level(logging.WARNING), registering_types():
        thread = threading.Thread(
            target=check_core, args=("glow", "sha256:aa", "sha256:bb")
        )
        thread.start()
        thread.join()

    assert "sha256:bb" in caplog.text


async def test_session_close_forgets_what_was_loaded():
    s = session()

    await glow(session=s).output()
    await s.close()
    await glow(session=s).output()

    assert len(s.session.loads) == 2


async def test_connection_yields_the_default_session(monkeypatch):
    from dagger.provisioning import _connection

    class Engine:
        def get_shared_client_connection(self):
            return default_session().connection

        async def setup_client(self, conn):
            return conn

    class provision_engine:  # noqa: N801
        def __init__(self, cfg):
            pass

        async def __aenter__(self):
            return Engine()

        async def __aexit__(self, *_):
            pass

    monkeypatch.setattr(_connection, "provision_engine", provision_engine)

    async with dagger.connection() as s:
        assert s is default_session()


def test_session_with_no_connection_is_over_the_shared_one():
    from dagger.client._session import SharedConnection

    assert Session().connection is SharedConnection()
    assert as_session(SharedConnection()) is default_session()


async def test_legacy_connection_yields_an_isolated_session(monkeypatch):
    from dagger.provisioning import _connection

    class Engine:
        def __init__(self, cfg, stack):
            pass

        async def provision(self):
            return self

        def get_client_connection(self):
            return FakeConnection()

        async def setup_client(self, conn):
            return conn

    monkeypatch.setattr(_connection, "Engine", Engine)

    async with dagger.Connection() as s:
        assert isinstance(s, Session)
        assert s is not default_session()
        assert isinstance(s.connection, FakeConnection)
