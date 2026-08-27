"""Engine version handling for generated clients."""

import re

from codegen.generator import parse_version

BOUND_MODULE_FLOOR = (1, 0, 0)
"""Bound modules declared below this version render a legacy core view."""

_BUILD_METADATA_RE = re.compile(r"\+.*$")


class UnsupportedEngineVersionError(ValueError):
    """A bound module declares an engine version this SDK cannot bind to."""


def check_bound_module_version(engine_version: str) -> None:
    """Reject a bound module whose declared engine version is below the floor.

    Only the numeric core is compared: a `v1.0.0-beta.11` prerelease is fine, a
    `v0.20.8` is not. An empty or unparsable value (`latest`) resolves to the
    running engine and is allowed.
    """
    version = parse_version(engine_version)
    if version is not None and version < BOUND_MODULE_FLOOR:
        msg = (
            f"cannot generate a client for a module declaring engine version "
            f"{engine_version!r}: it is rendered through a legacy core view; "
            "the module needs engineVersion v1.0.0 or later"
        )
        raise UnsupportedEngineVersionError(msg)


def cli_version(engine_version: str) -> str:
    """The CLI release tag provisioning downloads for a given engine version.

    `Query.version` reports `v1.0.0-beta.11+a4e1e4ff`; the downloader adds its
    own `v` and builds URLs and cache names from the bare tag.
    """
    return _BUILD_METADATA_RE.sub("", engine_version.strip()).removeprefix("v")
