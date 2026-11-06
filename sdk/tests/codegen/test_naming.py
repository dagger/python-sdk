import pytest
from graphql import build_schema

from codegen import naming
from codegen.naming import NamingError, Session, SnakeNames, schema_names, snake


def test_one_call_up_front_then_one_per_miss():
    calls: list[list[str]] = []

    def fmt(names: list[str]) -> list[str]:
        calls.append(names)
        return [f"<{n}>" for n in names]

    names = SnakeNames(fmt)
    names.prefetch(["b", "a", "b"])
    assert calls == [["a", "b"]]
    assert names("a") == "<a>"
    assert names("c") == "<c>"
    assert names("c") == "<c>"
    assert calls == [["a", "b"], ["c"]]


def test_engine_errors_fail_the_generation():
    def fmt(_: list[str]) -> list[str]:
        msg = 'cannot parse "ü"'
        raise NamingError(msg)

    with pytest.raises(NamingError, match="cannot parse"):
        SnakeNames(fmt)("ü")


def test_schema_names():
    schema = build_schema("""
        type Query { gitRepo(withSSH: Boolean): Repo }
        type Repo { shortSHA: String! }
        input Opts { userIDs: [String!] }
        enum Kind { A }
    """)
    assert set(schema_names(schema)) >= {
        "Query",
        "gitRepo",
        "withSSH",
        "Repo",
        "shortSHA",
        "Opts",
        "userIDs",
        "Kind",
    }
    assert not any(n.startswith("__") for n in schema_names(schema))


def test_no_session(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DAGGER_SESSION_PORT", raising=False)
    monkeypatch.delenv("DAGGER_SESSION_TOKEN", raising=False)
    with pytest.raises(NamingError, match="nested client"):
        Session.from_env()


def test_no_names_to_style_by(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(naming, "_current", None)
    with pytest.raises(NamingError, match="no engine"):
        snake("withExec")
