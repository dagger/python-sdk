import argparse
import json
import pathlib
import sys
from typing import Any

import graphql

from codegen import ast, generator, partition, version

parser = argparse.ArgumentParser(
    prog="python -m codegen", description="Dagger Python SDK"
)


def main():
    subparsers = parser.add_subparsers(
        title="additional commands",
        required=True,
    )

    gen_parser = subparsers.add_parser(
        "generate",
        help="generate the core API, or one module's client, from a schema",
    )
    gen_parser.add_argument(
        "-i",
        "--introspection",
        type=pathlib.Path,
        required=True,
        help="path to a .json file holding the introspection result",
    )
    gen_parser.add_argument(
        "-o",
        "--output",
        type=pathlib.Path,
        help=(
            "path to save the generated python module "
            "(defaults to printing it to stdout)"
        ),
    )
    gen_parser.add_argument(
        "--mode",
        choices=[generator.CORE_MODE, generator.CLIENT_MODE],
        default=generator.CORE_MODE,
        help=(
            "core: every type no module owns, plus Client and dag; "
            "client: the types and functions one module owns (default: core)"
        ),
    )
    gen_parser.add_argument(
        "--module",
        default="",
        help="client mode: the bound module's final name",
    )
    gen_parser.add_argument(
        "--binding",
        default="",
        help=(
            "client mode: the bound module's identity as JSON "
            '({"name", "kind", "ref", "pin"})'
        ),
    )
    gen_parser.add_argument(
        "--engine-version",
        default="",
        help="client mode: the bound module's declared engine version",
    )
    gen_parser.set_defaults(func=run_generate)

    name_parser = subparsers.add_parser(
        "client-name",
        help="print the Python module name of each bound module's client",
    )
    name_parser.add_argument(
        "module", nargs="+", help="the bound modules' final names, one per line out"
    )
    name_parser.set_defaults(func=run_client_name)

    version_parser = subparsers.add_parser(
        "cli-version",
        help="print the CLI release a client should provision for an engine",
    )
    version_parser.add_argument("engine_version", help="as reported by Query.version")
    version_parser.set_defaults(func=run_cli_version)

    args = parser.parse_args()
    args.func(args)


def run_generate(args: argparse.Namespace):
    result = json.loads(args.introspection.read_text())
    binding = json.loads(args.binding) if args.binding else None
    code = render(
        result,
        mode=args.mode,
        module=args.module,
        binding=binding,
        engine_version=args.engine_version,
    )

    if args.output:
        args.output.write_text(code)
        sys.stdout.write(f"Client generated successfully to {args.output}\n")
    else:
        sys.stdout.write(f"{code}\n")


def run_client_name(args: argparse.Namespace):
    try:
        names = partition.client_module_names(args.module)
    except partition.PartitionError as e:
        parser.exit(2, f"{parser.prog}: error: {e}\n")
    sys.stdout.writelines(f"{name}\n" for name in names)


def run_cli_version(args: argparse.Namespace):
    sys.stdout.write(f"{version.cli_version(args.engine_version)}\n")


def render(
    result: dict[str, Any],
    *,
    mode: str = generator.CORE_MODE,
    module: str = "",
    binding: dict[str, str] | None = None,
    engine_version: str = "",
) -> str:
    """Generate one Python module from an introspection result."""
    schema_version = result.get("__schemaVersion", "")

    if mode == generator.CORE_MODE:
        narrowed = partition.narrow_to_core(result["__schema"])
        schema = graphql.build_client_schema({"__schema": narrowed})
        ast.insert_stubs(narrowed, schema)
        return generator.generate(schema, schema_version=schema_version)

    if not module or binding is None:
        msg = "client mode needs --module and --binding"
        raise ValueError(msg)
    version.check_bound_module_version(engine_version)

    owned = partition.ownership(result["__schema"])
    root = partition.root_type_for(result["__schema"], module)
    if root not in owned.types:
        msg = (
            f"module {module!r} has root type {root!r}, which is a core type: "
            "its client cannot both define it and refer to core's"
        )
        raise partition.PartitionError(msg)
    schema = graphql.build_client_schema(result)
    ast.insert_stubs(result["__schema"], schema)
    return generator.generate(
        schema,
        schema_version=schema_version,
        mode=generator.CLIENT_MODE,
        module=module,
        binding=generator.Binding(**binding),
        owned_types=owned.owned_types(module),
        owned_fields=owned.owned_fields(module),
    )


def codegen(introspection: pathlib.Path, output: pathlib.Path | None):
    """Generate the core API from an introspection file (kept for callers)."""
    code = render(json.loads(introspection.read_text()))
    if output:
        output.write_text(code)
        sys.stdout.write(f"Client generated successfully to {output}\n")
    else:
        sys.stdout.write(f"{code}\n")
