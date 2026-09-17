import enum

import pytest

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
