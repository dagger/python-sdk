import argparse
import json
import pathlib
import sys

import graphql

from codegen import ast, generator, packages, partition


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        prog="python -m codegen", description="Dagger Python SDK"
    )
    subparsers = parser.add_subparsers(
        title="additional commands",
        required=True,
    )
    gen_parser = subparsers.add_parser(
        "generate",
        help="generate a Python client for the API",
    )
    add_introspection_argument(gen_parser)
    gen_parser.add_argument(
        "-o",
        "--output",
        type=pathlib.Path,
        help=(
            "path to save the generated python module "
            "(defaults to printing it to stdout)"
        ),
    )
    gen_parser.set_defaults(run=lambda args: codegen(args.introspection, args.output))

    core_parser = subparsers.add_parser(
        "generate-core",
        help=f"generate the {packages.NAMESPACE}.core package",
    )
    add_introspection_argument(core_parser)
    add_package_output_argument(core_parser)
    core_parser.set_defaults(run=lambda args: core(args.introspection, args.output))

    client_parser = subparsers.add_parser(
        "generate-client",
        help=f"generate the {packages.NAMESPACE} package of one client",
    )
    add_introspection_argument(client_parser)
    add_package_output_argument(client_parser)
    client_parser.add_argument(
        "--name",
        required=True,
        type=non_empty,
        help="name of the client, which is the module's name in the schema",
    )
    client_parser.add_argument(
        "--ref",
        required=True,
        type=non_empty,
        help="where the client loads its module from: a workspace path or a git ref",
    )
    client_parser.add_argument("--pin", help="commit that a git ref is pinned to")
    client_parser.add_argument(
        "--core-digest",
        help=(
            "CORE_DIGEST of the generated core package, which the client must "
            "match to import (defaults to the digest of the given schema's core)"
        ),
    )
    client_parser.set_defaults(run=client)

    args = parser.parse_args(argv)

    # TODO: Add argument for module init.
    try:
        args.run(args)
    except partition.ClientError as e:
        parser.error(str(e))


def non_empty(value: str) -> str:
    if not value:
        msg = "must not be empty"
        raise argparse.ArgumentTypeError(msg)
    return value


def add_introspection_argument(subparser: argparse.ArgumentParser):
    subparser.add_argument(
        "-i",
        "--introspection",
        type=pathlib.Path,
        required=True,
        help="path to a .json file holding the introspection result",
    )


def add_package_output_argument(subparser: argparse.ArgumentParser):
    subparser.add_argument(
        "-o",
        "--output",
        type=pathlib.Path,
        required=True,
        help=f"directory to write the {packages.NAMESPACE} namespace package into",
    )


def read_schema(introspection: pathlib.Path) -> tuple[graphql.GraphQLSchema, str]:
    result = json.loads(introspection.read_text())
    schema = graphql.build_client_schema(result)
    ast.insert_stubs(result["__schema"], schema)
    return schema, result.get("__schemaVersion", "")


def codegen(introspection: pathlib.Path, output: pathlib.Path | None):
    schema, schema_version = read_schema(introspection)
    code = generator.generate(schema, schema_version=schema_version)

    if output:
        output.write_text(code)
        sys.stdout.write(f"Client generated successfully to {output}\n")
    else:
        sys.stdout.write(f"{code}\n")


def core(introspection: pathlib.Path, output: pathlib.Path):
    schema, schema_version = read_schema(introspection)
    files = packages.core_package(schema, schema_version)
    root = packages.write_package(output, partition.CORE, files)
    sys.stdout.write(f"Core generated successfully to {root}\n")


def client(args: argparse.Namespace):
    schema, schema_version = read_schema(args.introspection)
    package, files = packages.client_package(
        schema,
        args.name,
        args.ref,
        args.pin,
        core_digest=args.core_digest,
        schema_version=schema_version,
    )
    root = packages.write_package(args.output, package, files)
    sys.stdout.write(f"Client generated successfully to {root}\n")
