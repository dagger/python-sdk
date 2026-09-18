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
DEFAULT_ENGINE = "tests/client/test_default_engine.py::"
DISPATCH = "tests/mod/test_dispatch.py::"

INVERSIONS = [
    # The default session provisions an engine only in a plain program, once,
    # and ends it at exit or on dagger.close().
    Inversion(
        DEFAULT_ENGINE + "test_a_plain_program_provisions_and_ends_the_engine_at_exit",
        'assert (marks / "ended").exists()',
        'assert not (marks / "ended").exists()',
    ),
    Inversion(
        DEFAULT_ENGINE + "test_a_session_in_the_environment_is_never_provisioned",
        'assert proc.stdout.strip() == "5151"',
        'assert proc.stdout.strip() == "4242"',
    ),
    Inversion(
        DEFAULT_ENGINE + "test_without_provisioning_there_is_no_session",
        'assert "No active engine session to connect to" in proc.stderr',
        'assert "No active engine session to connect to" not in proc.stderr',
    ),
    Inversion(
        DEFAULT_ENGINE + "test_concurrent_first_queries_provision_once",
        'assert (marks / "started").read_text().count("session") == 1',
        'assert (marks / "started").read_text().count("session") > 1',
    ),
    Inversion(
        DEFAULT_ENGINE + "test_close_ends_the_engine_before_it_returns",
        'assert proc.stdout.strip() == "True"',
        'assert proc.stdout.strip() == "False"',
    ),
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
    # dag.container() names core().container(), where core comes from and
    # the flag, and the same message holds for a client.
    Inversion(
        PACKAGE + "test_session_field_points_to_core",
        '"clients now: core().container() for a core field "',
        '"clients now: core().directory() for a core field "',
    ),
    Inversion(
        PACKAGE + "test_session_field_points_to_core",
        '"(from dagger_clients.core import core), or container() from "',
        '"(from dagger_clients.glow import core), or container() from "',
    ),
    Inversion(
        PACKAGE + "test_session_field_points_to_core",
        '"migrating, set global-client = true under [tool.dagger] and run "',
        '"migrating, set global-client = false under [tool.dagger] and run "',
    ),
    Inversion(
        PACKAGE + "test_session_field_points_to_a_client_too",
        """"or linter() from the client's package for a client." in""",
        """"or linter() from the core package for a client." in""",
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
    # The global client loads on first use, whatever was imported first, and
    # a client used before dag gets it too.
    Inversion(
        PACKAGE + "test_generated_code_imported_before_dagger",
        "        assert type(dagger.dag) is dagger_global.Client, type(dagger.dag)",
        "        assert type(dagger.dag) is not dagger_global.Client, type(dagger.dag)",
    ),
    Inversion(
        PACKAGE + "test_client_called_before_dag_gets_the_global_session",
        "assert root._ctx.conn is dagger_global.dag, type(root._ctx.conn)",
        "assert root._ctx.conn is not dagger_global.dag, type(root._ctx.conn)",
    ),
    # dir() and a star import see the lazy names.
    Inversion(
        PACKAGE + "test_dir_lists_dag_with_the_sdk_names",
        'assert {"dag", "Session", "connection", "function"} <= set(names)',
        'assert {"dag", "Session", "connection", "function"} > set(names)',
    ),
    Inversion(
        PACKAGE + "test_star_import_without_the_global_client",
        'assert namespace["dag"] is default_session()',
        'assert namespace["dag"] is not default_session()',
    ),
    Inversion(
        PACKAGE + "test_global_client_names_are_listed_and_star_imported",
        'assert namespace["Container"] is core.Container',
        'assert namespace["Container"] is not core.Container',
    ),
    Inversion(
        PACKAGE + "test_global_client_names_are_listed_and_star_imported",
        'assert {"dag", "Container", "Linter", "Client", "Session"} <= set(listed)',
        'assert {"dag", "Container", "Linter", "Client", "Session"} > set(listed)',
    ),
    # A global client that cannot import is an error, not an absent one.
    Inversion(
        PACKAGE + "test_global_client_that_cannot_import_is_not_skipped",
        'assert _run(BROKEN_GLOBAL_CLIENT, tmp_path) == "dagger_clients.gone\\n"',
        'assert _run(BROKEN_GLOBAL_CLIENT, tmp_path) == "Session\\n"',
    ),
    # Bindings an earlier version left in dagger_gen are named, not loaded.
    Inversion(
        PACKAGE + "test_legacy_bindings_are_named_not_loaded",
        '"it, `dagger generate` removes it; if you wrote it, delete or rename it.\\n"',
        '"it, `dagger generate` removes it.\\n"',
    ),
    Inversion(
        PACKAGE + "test_no_legacy_bindings_no_warning",
        'assert _run(IMPORT_WARNINGS) == ""',
        'assert _run(IMPORT_WARNINGS) != ""',
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
        CLIENTS + "test_git_target_loads_before_its_query",
        "assert GLOW.pin in load",
        "assert GLOW.pin not in load",
    ),
    Inversion(
        CLIENTS + "test_local_target_loads_before_its_query",
        "assert is_load(load)\n    assert LINTER.ref in load",
        "assert is_load(load)\n    assert LINTER.ref not in load",
    ),
    # Under a module entrypoint, a local target loads through the source the
    # entrypoint handed over for it, by name, and nothing else changes query.
    Inversion(
        CLIENTS + "test_handed_client_serves_a_local_target_by_name",
        '"    ... on ModuleSource {\\n"',
        '"    ... on Workspace {\\n"',
    ),
    Inversion(
        CLIENTS + "test_handed_clients_leave_a_git_target_to_serve_module",
        "assert handed_clients not in load",
        "assert handed_clients in load",
    ),
    Inversion(
        CLIENTS + "test_undeclared_local_client_fails_naming_it",
        "assert not s.session.queries",
        "assert s.session.queries",
    ),
    Inversion(
        CLIENTS + "test_entrypoint_without_a_handover_fails_a_local_target",
        'assert "handed over no clients" in str(info.value)',
        'assert "handed over no clients" not in str(info.value)',
    ),
    Inversion(
        CLIENTS + "test_handed_client_failure_does_not_fall_back",
        'assert "serveModule" not in load',
        'assert "serveModule" in load',
    ),
    Inversion(
        CLIENTS + "test_outside_an_entrypoint_a_local_target_uses_serve_module",
        'assert "node(" not in load',
        'assert "node(" in load',
    ),
    Inversion(
        DISPATCH + "test_command_hands_the_clients_to_the_load",
        "assert got == \"{'linter': 'bW9kdWxlU291cmNl'}\"",
        'assert got == "None"',
    ),
    Inversion(
        DISPATCH + "test_command_without_clients_is_still_under_an_entrypoint",
        'read_text()) == "None"',
        'read_text()) != "None"',
    ),
    Inversion(
        DISPATCH + "test_command_refuses_malformed_clients",
        'assert "clients the entrypoint handed over" in proc.stderr',
        'assert "clients the entrypoint handed over" not in proc.stderr',
    ),
    # An engine below the floor fails the load; it is no stale client.
    Inversion(
        CLIENTS + "test_engine_without_serve_module_fails_the_load_not_as_stale",
        "assert not isinstance(info.value, StaleClientError)\n"
        "    assert info.value.__cause__ is NO_SERVE_MODULE",
        "assert isinstance(info.value, StaleClientError)\n"
        "    assert info.value.__cause__ is NO_SERVE_MODULE",
    ),
    # Only a validation error is a missing field, for the stale check too.
    Inversion(
        CLIENTS + "test_internal_error_with_the_phrase_stays_a_query_error",
        'internal = {"code": "INTERNAL_SERVER_ERROR"}',
        'internal = {"code": "GRAPHQL_VALIDATION_FAILED"}',
    ),
    # The staleness message names the client and its address.
    Inversion(
        CLIENTS + "test_missing_field_becomes_stale_client_error",
        "The client 'glow' from github.com/eunomie/glow at 4f1c9e ",
        "The client 'glow' from github.com/eunomie/glow at 9b2d7a ",
    ),
    Inversion(
        CLIENTS + "test_stale_message_names_each_client_and_its_address",
        "'linter' from ./.dagger/modules/linter are out of date.",
        "'linter' from ./linter are out of date.",
    ),
    # The SDK files import nothing generated; the init names it once.
    Inversion(
        ISOLATION + "test_sdk_files_import_without_generated_code",
        "assert proc.returncode == 0, proc.stderr",
        "assert proc.returncode != 0, proc.stderr",
    ),
    # The text scan lets through the one help string, exactly.
    Inversion(
        ISOLATION + "test_sdk_file_text_names_no_generated_package[client/_session.py]",
        '"(from dagger_clients.core import core)",',
        '"(from dagger_clients.core import Container)",',
    ),
    Inversion(
        ISOLATION + "test_package_init_names_only_the_global_client",
        "\"import_module('dagger_global')\"]",
        "\"import_module('dagger_gen')\"]",
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
    # A method of the global client never hides the Session it is.
    Inversion(
        PACKAGES + "test_global_client_refuses_a_core_field_that_hides_the_session",
        'f"it would hide Session.{field}"',
        'f"it would hide Query.{field}"',
    ),
    Inversion(
        PACKAGES + "test_global_client_refuses_a_client_that_hides_the_session",
        """'client "connect": it would hide Session.connect'""",
        """'client "connect": it would hide Session.close'""",
    ),
    Inversion(
        PACKAGES + "test_global_client_knows_every_name_of_a_session",
        'if not n.startswith("__")} == SESSION_NAMES',
        'if not n.startswith("__")} != SESSION_NAMES',
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
