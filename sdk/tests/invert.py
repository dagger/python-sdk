"""Invert each new assertion once and confirm that its test fails.

Run from ``sdk/``::

    uv run --frozen python tests/invert.py

Each entry names a test, the exact text of one of its assertions and the
inverted text. The test runs as written, then with the inversion applied,
and the file is restored either way. The exit code is non-zero if a test
fails as written, still passes when inverted, or if the text to invert is
not found exactly once.
"""

# ruff: noqa: T201

import dataclasses
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).parent


@dataclasses.dataclass(frozen=True)
class Inversion:
    test: str
    old: str
    new: str

    @property
    def path(self) -> pathlib.Path:
        return HERE.parent / self.test.partition("::")[0]


PACKAGE = "tests/test_dagger_package.py::"
CLIENTS = "tests/client/test_clients.py::"
ISOLATION = "tests/client/test_sdk_isolation.py::"
PACKAGES = "tests/codegen/test_packages.py::"

INVERSIONS = [
    # dagger.Container names its new home.
    Inversion(
        PACKAGE + "test_core_name_points_to_its_new_home",
        'dagger_clients.core: from dagger_clients.core import Container"',
        'dagger_clients.core: from dagger_clients.core import Directory"',
    ),
    Inversion(
        PACKAGE + "test_legacy_client_type_points_to_session",
        "and the API is on core() from dagger_clients.core.",
        "and the API is on core() from dagger.client.gen.",
    ),
    # dag.container() names core().container() and the flag, and the same
    # message holds for a client.
    Inversion(
        PACKAGE + "test_session_field_points_to_core",
        '"clients now: core().container() for a core field, or container() from "',
        '"clients now: core().directory() for a core field, or container() from "',
    ),
    Inversion(
        PACKAGE + "test_session_field_points_to_core",
        '"while migrating, set global-client = true under [tool.dagger] and run "',
        '"while migrating, set global-client = false under [tool.dagger] and run "',
    ),
    Inversion(
        PACKAGE + "test_session_field_points_to_a_client_too",
        '"or linter() from its package in dagger_clients for a client."',
        '"or linter() from dagger_clients.core for a client."',
    ),
    # dag is the default Session.
    Inversion(
        PACKAGE + "test_dag_is_the_default_session",
        "assert type(dagger.dag) is Session\n"
        "    assert dagger.dag is default_session()",
        "assert type(dagger.dag) is Session\n"
        "    assert dagger.dag is not default_session()",
    ),
    # The package imports with no generated code present.
    Inversion(
        PACKAGE + "test_package_imports_with_no_generated_code",
        'assert _run(NO_GENERATED_CODE) == "ok\\n"',
        'assert _run(NO_GENERATED_CODE) == "ko\\n"',
    ),
    # With dagger_global installed, dag.container() is a core Container and
    # old and new calls mix.
    Inversion(
        PACKAGE + "test_global_client_makes_dag_the_client",
        "assert type(ctr) is core.Container, type(ctr)",
        "assert type(ctr) is core.Directory, type(ctr)",
    ),
    Inversion(
        PACKAGE + "test_global_client_makes_dag_the_client",
        "assert type(linter.linter(old)) is linter.Linter",
        "assert type(linter.linter(old)) is core.Directory",
    ),
    Inversion(
        PACKAGE + "test_global_client_makes_dag_the_client",
        "assert isinstance(dagger.dag, Session)\n"
        "    assert dagger.dag is default_session()",
        "assert isinstance(dagger.dag, Session)\n"
        "    assert dagger.dag is not default_session()",
    ),
    Inversion(
        PACKAGE + "test_connection_yields_the_global_client",
        "assert session is dagger.dag",
        "assert session is not dagger.dag",
    ),
    Inversion(
        PACKAGE + "test_mypy_types_dag_as_the_global_client",
        "assert 'Revealed type is \"dagger_global.Client\"' in out, out",
        "assert 'Revealed type is \"dagger.client._session.Session\"' in out, out",
    ),
    # dagger.Connection yields a Session.
    Inversion(
        CLIENTS + "test_legacy_connection_yields_an_isolated_session",
        "assert isinstance(s, Session)\n        assert s is not default_session()",
        "assert not isinstance(s, Session)\n        assert s is not default_session()",
    ),
    Inversion(
        CLIENTS + "test_session_with_no_connection_is_over_the_shared_one",
        "assert Session().connection is SharedConnection()",
        "assert Session().connection is not SharedConnection()",
    ),
    # serveModule is the load, for a git and a local target alike.
    Inversion(
        CLIENTS + "test_load_is_one_call_for_a_git_target",
        '\'  serveModule(address: "github.com/eunomie/glow", refPin: "4f1c9e")\\n\'',
        '\'  serveModule(address: "github.com/eunomie/glow", refPin: "9b2d7a")\\n\'',
    ),
    Inversion(
        CLIENTS + "test_load_is_the_same_call_for_a_local_target",
        "assert load == 'query {\\n  serveModule(address: \"./clients/linter\")\\n}'",
        "assert load == 'query {\\n  serveModule(address: \"./linter\")\\n}'",
    ),
    Inversion(
        CLIENTS + "test_engine_without_serve_module_gets_the_old_chain",
        'assert "serveModule" in attempt',
        'assert "serveModule" not in attempt',
    ),
    Inversion(
        CLIENTS + "test_other_load_errors_do_not_fall_back",
        "assert len(s.session.loads) == 1\n    assert info.value.__cause__",
        "assert len(s.session.loads) == 2\n    assert info.value.__cause__",
    ),
    # The staleness message names the client and its address.
    Inversion(
        CLIENTS + "test_missing_field_becomes_stale_client_error",
        "The client 'glow' from github.com/eunomie/glow at 4f1c9e ",
        "The client 'glow' from github.com/eunomie/glow at 9b2d7a ",
    ),
    Inversion(
        CLIENTS + "test_stale_message_names_each_client_and_its_address",
        "'linter' from ./clients/linter are out of date.",
        "'linter' from ./linter are out of date.",
    ),
    # The SDK files import nothing generated; the init names it once.
    Inversion(
        ISOLATION + "test_sdk_files_import_without_generated_code",
        "assert proc.returncode == 0, proc.stderr",
        "assert proc.returncode != 0, proc.stderr",
    ),
    Inversion(
        ISOLATION + "test_package_init_names_generated_code_once",
        'assert found == ["from dagger_global import *"]',
        "assert found == []",
    ),
    # The generated global client.
    Inversion(
        PACKAGES + "test_global_client_delegates_root_fields_to_core",
        'assert "class Client(_Session):" in code',
        'assert "class Client(_Root):" in code',
    ),
    Inversion(
        PACKAGES + "test_global_client_has_one_method_per_client",
        'assert "import dagger_clients.glow as _glow" in code',
        'assert "import dagger_clients.glow as _glow" not in code',
    ),
    Inversion(
        PACKAGES + "test_global_client_puts_contributed_fields_on_the_core_classes",
        'assert "Env.as_linter = _linter.as_linter  # type: ignore',
        'assert "Env.as_glow = _linter.as_linter  # type: ignore',
    ),
    Inversion(
        PACKAGES + "test_global_client_is_temporary_and_says_so",
        'assert "global-client = true" in code',
        'assert "global-client = true" not in code',
    ),
    Inversion(
        PACKAGES + "test_global_dag_returns_the_core_classes",
        "assert type(directory) is core.Directory",
        "assert type(directory) is core.File",
    ),
    Inversion(
        PACKAGES + "test_old_and_new_calls_mix",
        "assert type(new_with_old) is type(old_with_new) is linter.Linter",
        "assert type(new_with_old) is type(old_with_new) is linter.LinterReport",
    ),
    Inversion(
        PACKAGES + "test_contributed_field_is_a_method_at_run_time",
        "assert type(result) is linter.Linter\n",
        "assert type(result) is linter.LinterReport\n",
    ),
    Inversion(
        PACKAGES + "test_cli_generates_the_global_client_from_one_schema_per_client",
        'assert "def glow(self) -> Glow:" in code',
        'assert "def glow(self) -> Glow:" not in code',
    ),
    # The one-file path still works.
    Inversion(
        PACKAGES + "test_cli_still_generates_one_file",
        'assert "dag = Client()" in code',
        'assert "dag = Client()" not in code',
    ),
]


def run(test: str) -> bool:
    """Whether the test passes."""
    # No bytecode: pytest keys its rewritten test modules on mtime and size,
    # so an inversion of the same length, restored within the same second,
    # would otherwise run the inverted code again as the original.
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", test],
        capture_output=True,
        text=True,
        check=False,
        cwd=HERE.parent,
    )
    return proc.returncode == 0


def check(inversion: Inversion) -> str | None:
    """The problem with an inversion, or None when it behaves."""
    source = inversion.path.read_text()
    if (count := source.count(inversion.old)) != 1:
        return f"text to invert found {count} times, not once"
    if not run(inversion.test):
        return "fails as written"
    inversion.path.write_text(source.replace(inversion.old, inversion.new))
    try:
        if run(inversion.test):
            return "still passes when inverted"
    finally:
        inversion.path.write_text(source)
    return None


def main() -> int:
    problems = 0
    for inversion in INVERSIONS:
        problem = check(inversion)
        problems += problem is not None
        print(f"{problem or 'fails when inverted'}: {inversion.test}")
    print(f"{len(INVERSIONS)} inversions, {len(INVERSIONS) - problems} confirmed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
