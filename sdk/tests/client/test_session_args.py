from dagger._engine._version import CLI_VERSION
from dagger.provisioning._config import Config
from dagger.provisioning._session import _build_session_args


def test_session_announces_the_targeted_release():
    args = _build_session_args(Config(), "dagger")
    assert args[:2] == ["dagger", "session"]
    assert args[args.index("--version") + 1] == f"v{CLI_VERSION}"
