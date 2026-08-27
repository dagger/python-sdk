import pytest

from codegen.version import (
    UnsupportedEngineVersionError,
    check_bound_module_version,
    cli_version,
)
from dagger.provisioning._download import Downloader, Platform


@pytest.mark.parametrize(
    ("engine", "expected"),
    [
        ("v1.0.0-beta.11+a4e1e4ff", "1.0.0-beta.11"),
        ("v1.0.0-beta.11", "1.0.0-beta.11"),
        ("1.0.0", "1.0.0"),
        ("v1.2.3+abc", "1.2.3"),
        ("  v1.0.0-rc.1\n", "1.0.0-rc.1"),
    ],
)
def test_cli_version_is_the_bare_release_tag(engine, expected):
    assert cli_version(engine) == expected


def test_stamped_version_builds_the_release_url():
    downloader = Downloader(
        cli_version("v1.0.0-beta.11+a4e1e4ff"),
        platform=Platform("linux", "amd64"),
    )
    assert str(downloader.archive_url) == (
        "https://dl.dagger.io/dagger/releases/1.0.0-beta.11/"
        "dagger_v1.0.0-beta.11_linux_amd64.tar.gz"
    )


@pytest.mark.parametrize("engine", ["v1.0.0-beta.11", "v1.0.0-0", "v1.0.0", "2.3.4"])
def test_modern_bound_modules_pass_the_floor(engine):
    check_bound_module_version(engine)


@pytest.mark.parametrize("engine", ["", "latest", "main", "dev"])
def test_unparsable_versions_resolve_to_the_engine_and_pass(engine):
    check_bound_module_version(engine)


@pytest.mark.parametrize("engine", ["v0.20.8", "0.21.0", "v0.99.99"])
def test_legacy_bound_modules_are_rejected(engine):
    with pytest.raises(UnsupportedEngineVersionError, match=engine):
        check_bound_module_version(engine)
