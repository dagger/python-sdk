import re

import pytest
from graphql.pyutils import camel_to_snake

from codegen import naming


@pytest.fixture(scope="session", autouse=True)
def setup_telemetry():
    import dagger.telemetry

    dagger.telemetry.initialize()


@pytest.fixture(scope="session")
def anyio_backend():
    # TODO: remove when other backends can be supported
    # (i.e., HTTPX transport since it supports AnyIO)
    return "asyncio"


@pytest.fixture
def alpine_version():
    return "3.20.2"


@pytest.fixture
def alpine_image(alpine_version):
    return f"alpine:{alpine_version}"


_INITIALISM_RE = re.compile(r"([A-Z\d]+)(?=[A-Z\d]|$)")


def stand_in_snake(names: list[str]) -> list[str]:
    """A stand-in for the engine's formatIdentifiers(casing: SNAKE).

    The generator's tests render made-up schemas without an engine, so
    something has to spell their Python names; the e2e checks generate with
    the engine.
    """
    return [
        camel_to_snake(_INITIALISM_RE.sub(lambda m: m.group(0).title(), n))
        for n in names
    ]


@pytest.fixture(autouse=True)
def engine_names(monkeypatch: pytest.MonkeyPatch):
    """Style generated identifiers by the stand-in, as the CLI does too."""

    def stand_in(schemas, _version: str) -> naming.SnakeNames:
        names = naming.SnakeNames(stand_in_snake)
        names.prefetch(n for schema in schemas for n in naming.schema_names(schema))
        return names

    monkeypatch.setattr(naming, "engine_names", stand_in)
    with naming.styled_by(naming.SnakeNames(stand_in_snake)) as names:
        yield names
