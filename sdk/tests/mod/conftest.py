import enum
import json
import re
import typing

import pytest

from dagger.client._session import SharedConnection
from dagger.client.base import Type


@pytest.fixture
def selections():
    """Reduce a binding to the fields it selects.

    A hand-written binding and a generated one never compare equal, even
    when they send the same query. Their selections do.
    """

    def _value(value):
        if isinstance(value, Type):
            return _selections(value)
        if isinstance(value, enum.Enum):
            return value.name
        if isinstance(value, list):
            return [_value(v) for v in value]
        return value

    def _selections(obj: Type):
        return [
            (f.type_name, f.name, {k: _value(v) for k, v in f.args.items()})
            for f in obj._ctx.selections
        ]

    return _selections


_NAMING_FIELD = re.compile(
    r"(typeNames|fieldNames|enumMemberNames)\(names: (\[.*?\])\)"
)


class FakeEngine:
    """Answers CurrentModule.naming from a table of the engine's names.

    A name the table lacks fails the query, as a name the engine can't name
    does. Any other query gets the canned data. It keeps what it was asked.
    """

    def __init__(self, names: typing.Mapping[str, typing.Mapping[str, str]]):
        self.names = names
        self.data: typing.Any = None
        self.queries: list[str] = []

    @property
    def naming_queries(self) -> list[str]:
        return [q for q in self.queries if q.startswith("query ModuleNaming")]

    async def execute(self, query: str):
        self.queries.append(query)
        if not query.startswith("query ModuleNaming"):
            return self.data
        naming = {}
        for field, names in _NAMING_FIELD.findall(query):
            table = self.names.get(field, {})
            naming[field] = [table[name] for name in json.loads(names)]
        return {"currentModule": {"naming": naming}}


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """A fake engine behind the default session.

    Parametrize indirectly with the naming table, or set `names` on it.
    """
    fake = FakeEngine(getattr(request, "param", {}))

    async def ready(_):
        return fake

    monkeypatch.setattr(SharedConnection, "_ready", ready)
    return fake
