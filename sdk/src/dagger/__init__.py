import contextlib

# Make sure to place exceptions first as they're dependencies of other imports.
from dagger._exceptions import *

# Engine provisioning (doesn't make sense in modules)
with contextlib.suppress(ModuleNotFoundError):
    from dagger.provisioning import *

# Client connection
from dagger.client import Session as Session
from dagger.client._config import Retry as Retry
from dagger.client._config import Timeout as Timeout
from dagger.client._connection import connect as connect
from dagger.client._connection import close as close

# The API is generated per scope, into dagger_clients, and the SDK files
# never import it. The one exception is the temporary global client, which
# keeps dag.container() and dagger.Container working while a module
# migrates. Its dag is a Session, so it becomes the default one.
from dagger.client._session import default_session as _default_session
from dagger.client._session import install_default_session as _install

try:
    from dagger_global import *
except ModuleNotFoundError as _e:
    # Only its absence means no flag: a global client that cannot import one
    # of its clients is broken, not off.
    if _e.name != "dagger_global":
        raise
    # With the global client, a type checker sees dag as its Client.
    dag = _default_session()  # type: ignore[assignment, unused-ignore]
# A no-op for the default session itself.
_install(dag)
del _default_session, _install

# Module support (only makes sense in a module runtime container)
with contextlib.suppress(ModuleNotFoundError):
    from dagger.mod import *


def __getattr__(name: str):
    """Say where a name of the legacy bindings went."""
    msg = f"module {__name__!r} has no attribute {name!r}"
    if name == "Client":
        msg += (
            ". dagger.Connection now yields a dagger.Session, "
            "and the API is on core() from dagger_clients.core."
        )
    elif name[:1].isupper():
        msg += (
            f". Core types moved to dagger_clients.core: "
            f"from dagger_clients.core import {name}"
        )
    raise AttributeError(msg)


# Re-export imports so they look like they live directly in this package.
for _value in list(locals().values()):
    if getattr(_value, "__module__", "").startswith("dagger."):
        with contextlib.suppress(AttributeError):
            _value.__module__ = __name__
