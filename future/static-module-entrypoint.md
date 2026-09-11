# Static module entrypoints for Python modules

author: yves
created: 2026-09-11
status: approved 2026-09-12, in implementation
related: `dagger/dagger#14038` (manifest v2 entrypoints, draft, head
`75c777223ccc4baaf5819a04d70d060034a94dbb`); `dagger/dagger#13992` (SDK
interface, merged 2026-09-09 as `908d48ebda4b3dd1c33c8bebba49b90abb4f2953`,
released in `v1.0.0-beta.12`); `dagger/dagger#11803`, `#13095`, `#13251`
and `#13235` (the Python AST analyzer shipped in v0.20.7, its fixes, its
removal in v0.21.1, and the follow-up design that this document adopts);
`dagger/java-sdk#19` (head `12a2682d2976c4eb59f8d6c6a501c446ce54a499`) and
`dagger/go-sdk#36` (head `4dfd447d58344835a0d4692ec0c8e5683c18bd6f`), two
prototypes of the same adaptation; the first attempt at this design,
`future/manifest-v2-entrypoint.md` at commit `691de2b` on branch
`python-sdk-manifest-v2-sdk-ux-lead-9a51fff5` of the fork
`eunomie/python-sdk` (never merged); `future/done/self-contained-python-sdk.md`;
`hack/designs/2026-08-25-python-module-performance-ideas.md`;
`dagger/sdk-helpers` (`dagger.io/sdk/helpers@v1`, locked at v1.0.2).

## Summary

A Python module is loaded and called through code that runs at call time.
The engine starts the module's Python interpreter to learn which types the
module exposes, and starts it again to run a function. The first start
exists only to discover types. This document moves that discovery to
`dagger generate`: the SDK imports the module once, in the module's own
container, reads what its decorators registered, and writes the result into
a generated module entrypoint. The engine then reads the types from the
entrypoint and never boots Python to discover them.

The extraction imports the module instead of parsing its source. A static
`ast` analyzer was measured (5 ms against about 1 s) and rejected on the
evidence of `dagger/dagger#11803`: an analyzer of that kind shipped in
v0.20.7, needed five fix releases, and was removed in v0.21.1 because it
could not see what Python computes at import time. Importing is exact by
construction and is about 150 lines.

The change is gated by one SDK setting, `staticEntrypoint`, in two phases.
In phase A the setting defaults to `false` and nothing changes for a module
that does not set it. In phase B the default becomes `true`, and a module
that needs the dynamic path sets it to `false`. The dynamic path is today's
path. It is not removed, and this document plans no date for its removal.

The static path depends on an engine change that is still a draft,
`dagger/dagger#14038`. Everything that does not depend on it, which is the
extraction, the rendering, the manifest and their tests, lands and runs in
this repository's CI. Everything that does is verified against a
development engine and recorded here.

## Terms

| Term | Meaning |
|---|---|
| dynamic path | What every Python module does today: a version 1 `dagger-module.toml` with `[runtime]`, a runtime module that builds the module's container, and types discovered by running the module once per session. Selected by `staticEntrypoint = false`. |
| static path | A version 2 `dagger-module.toml` with `[entrypoint]`, a generated Dang entrypoint under `sdk/entrypoint/`, and types written into it at `dagger generate`. Selected by `staticEntrypoint = true`. |
| runtime registry | The `dagger.mod.Module` instance that the module's decorators populate when the module is imported. It holds the object, interface and enum definitions and dispatches calls. |
| description | The plain-data form of what the registry holds: `ModuleDescription` in `dagger.mod._describe`. Both paths derive their type definitions from it. |
| renderer | The code that turns a description into Dang source. |
| the first attempt | `future/manifest-v2-entrypoint.md` at `691de2b` on `eunomie/python-sdk`. |
| the v0.20.7 analyzer | The `ast`-based analyzer of `dagger/dagger#11803`, removed by `#13251`. |

"Legacy" appears below only in two proper names that other code owns: the
`legacy` starter template of this repository, and the `withLegacy*` functions
of `dagger/sdk-helpers`.

## Requirements

The repository owner set these requirements on 2026-09-11 and confirmed the
approach on 2026-09-12.

1. The types and function signatures a module exposes must be computed once
   at `dagger generate` and written down, so that the entrypoint can be
   generated from them. Discovering them by loading the module at call time
   is no longer acceptable as the default.
2. The rollout has two phases behind one SDK flag. First the static path is
   opt-in. Then the default inverts and the dynamic path is opt-out. The
   dynamic path stays supported for as long as users want it. There is no
   hard cutover.
3. The scope is the extraction of types and function signatures. The
   module's functions are never executed to extract them. Execution and
   dispatch are in scope only as far as the generated entrypoint needs to
   call a function.
4. Static analysis of the source was the owner's first idea, not a mandate.
   Alternatives were evaluated on correctness for the type-hint surface the
   SDK accepts, on implementation complexity, and on analysis speed. The
   owner chose importing at generate time on that evaluation.
5. Implementation started after the owner approved this design.

## Problem

### What the dynamic path costs

On the dynamic path the engine calls the module's runtime module, gets a
`Container`, execs it once with an empty function call to learn the module's
types, then once per constructor or function call. Each exec is a fresh
Python interpreter. `hack/designs/2026-08-25-python-module-performance-ideas.md`
measured a warm `dagger call` on the default template at about 4.4 s, of
which about 3.1 s is three interpreter boots; the type-discovery boot is one
of the three, and the SDK's import chain inside a boot costs 665 to 684 ms.
Discovery itself, once the module is imported, costs about 4 ms.

### What the engine changes

`dagger/dagger#14038` defines manifest version 2 and the module entrypoint.
A version 2 `dagger-module.toml` has exactly three keys, and the engine
rejects any other (`core/modules/config_format.go` at `75c77722`,
`validateModuleManifestV2TOML`):

```toml
manifestVersion = 2
name = "hello"

[entrypoint]
kind = "dang"
source = "./sdk/entrypoint"
```

The engine loads every `.dang` file in `entrypoint.source` as one program.
That program must define exactly one type that implements this interface,
constructible with no arguments (`core/sdk/dang/v2/entrypoint.go` at
`75c77722`, `findModuleEntrypoint`):

```graphql
interface ModuleEntrypoint {
  types(workspace: Workspace!): [TypeDef!]!
  call(
    workspace: Workspace!
    receiverType: String!
    receiverValue: JSON
    fnName: String!
    fnArgs: JSON!
  ): JSON!
}
```

The engine calls `types` to install the module and `call` for every
constructor or function invocation. No SDK module is called at load or call
time. `types` can be a literal list of `TypeDef` expressions, which is what
the Go and Java prototypes and the engine's own reference module
(`.dagger/modules/tiny/entrypoint/main.dang` at `75c77722`) return.

### The v0.20.7 analyzer

`dagger/dagger#11803` (v0.20.7, 2026-04-22) replaced runtime registration
with a pure-`ast` analyzer, `dagger.mod._analyzer`, run at load time for
every module with no opt-out. It grew through `#13090`, `#13091`, `#13093`,
`#13095`, `#13162` and `#13171` to about 5,000 lines of analyzer and 6,700
lines of tests (a differential suite against `typing.get_type_hints`,
Hypothesis property tests, and a daggerverse corpus), and was removed by
`#13251` (v0.21.1, 2026-05-28). The owner's reasons on `#13251`: every
release found new edge cases; modules that worked stopped working; and "the
current AST based analysis can't find all the things people are doing in
python. Some can be solved at the price of breaking changes, some can't be
fixed currently, by design." The by-design cases named in `#13230` and
`#13234`: defaults that are runtime values (`logging.INFO` recorded as
`"INFO"` instead of `20`), members added by decorators at runtime, enums
built dynamically, `enum.auto()`.

The owner's follow-up design, `#13235` (`hack/designs/python-sdk-no-codegen-at-runtime.md`
on that branch), concluded: "Python can import it... use its richest model
(execution) at generate time, get real values, and still emit a fully
static runtime artifact", because a self-call such as `dag.hello().greet()`
lives in a function body that does not run at import. This document is that
conclusion applied to manifest version 2.

### What the first attempt concluded, and what changed

The first attempt designed a Python entrypoint whose `types()` execs the
module's container at load time to describe the types, cached by the
content of the module directory. It rejected computing the types at
generate time because "every signature edit would then need `dagger
generate`". Requirement 1 decides that the other way, as it is for the Go,
Java and TypeScript SDKs, and the staleness guard below turns a stale
entrypoint into an actionable error. Since then `dagger/dagger#13992`
merged (2026-09-09) and this repository adopted its SDK interface in
`dagger/python-sdk#25`, `#26` and `#27`; `#14038` did not move (head
`75c77722`, draft, no review, not on `main` at `4932411f` on 2026-09-11).

## Goals

1. `dagger generate` on a Python module with `staticEntrypoint = true`
   writes a version 2 `dagger-module.toml` and a generated Dang entrypoint
   under `sdk/entrypoint/`. The entrypoint's `types()` is a literal list of
   `TypeDef` expressions. `call()` runs the module's function.
2. The static path exposes exactly the types the dynamic path exposes for
   the same module, because both derive them from one description built by
   importing the module and reading the runtime registry.
3. The extraction is one interpreter boot in the module's container at
   `dagger generate`, and nothing at load time.
4. The dynamic path keeps working, unchanged, for every module that does not
   opt in during phase A and for every module that opts out during phase B.
   A module moves between the two paths by changing one setting and running
   `dagger generate`, in both directions, leaving no file of the other path
   behind. A module whose version 1 manifest holds fields version 2 cannot
   carry is refused on the static path, naming the fields.
5. The parts that do not depend on `dagger/dagger#14038` (extraction,
   rendering, manifest, the switch in both directions, and their checks) are
   verified in this repository's CI. The parts that do are verified on a
   development engine and recorded here.

### Accepted differences

These follow from the `ModuleEntrypoint` interface at `75c77722` and from
requirement 1. Each is either refused at `dagger generate` or documented in
the README.

- A change to the module's source is visible to the engine only after
  `dagger generate`. Until then `call()` refuses to run the module with a
  message that says to run `dagger generate` (the staleness guard).
- No cache policy other than the default can be honoured: the entrypoint's
  container exec is cached by the content of its inputs and receives no
  per-call nonce, so after the engine's own cache entry expires the
  identical exec still returns its cached result. The static path refuses
  every explicit `cache=` value and a version 1 manifest with
  `disableDefaultFunctionCaching = true`.
- A function error reaches the user as an exec failure carrying the
  process's stderr. The structured values the dynamic path attaches to a
  `dagger.Error` are lost: `Query.currentFunctionCall` is reachable inside
  the Dang program but not inside the exec it starts (first attempt, probe
  1; unchanged at this head).
- The module docstring (`Module.withDescription` on the dynamic path) is not
  exposed. `types()` has no place for it.
- The `debug` setting of the dynamic path has no equivalent: a version 2
  manifest carries no SDK configuration.
- A module with `staticEntrypoint = true` cannot depend on other modules.
  Manifest version 2 has no dependency list
  (`future/module-manifest-v2/compat-bridge.md` at `75c77722`,
  *Dependencies*). `generateScope` refuses a non-empty `clients` list on the
  static path.
- Only the module directory is mounted into the module's container, and
  version 2 has no `include` list. A Python dependency outside the module
  directory fails the container build, at generate time and at call time
  alike, with the installer's error.
- A version 1 manifest field that version 2 cannot carry is refused rather
  than dropped: `include`, a `source` other than `.`, a runtime other than
  `python`, `disableDefaultFunctionCaching`, `codegen` and `clients` tables,
  and dependencies. Such a module stays on the dynamic path or removes the
  field first. On the way back to the dynamic path the manifest is rebuilt
  with the generating engine's `engineVersion`.
- The static path runs the module in the container this repository's
  `runtime/` builds. A module on the dynamic path with `[runtime] source =
  "python"` runs in the engine's built-in Python runtime instead. The two
  builds differ in base image pin, `uv` version and install flow.
- Two behaviours inside the nested exec are verified first thing after
  approval (*Testing*, dev-engine step 1): `DefaultPath` and
  `DefaultAddress` arguments, which the engine resolves before it calls
  `call` (`future/module-manifest-v2/spec.md`, *Call rules*), and
  `dag.current_module()`, which is an open question because
  `currentFunctionCall` does not reach the exec. If one does not hold, it
  becomes an entry in this list.

## Non-goals (YAGNI)

- Generating a static Python dispatcher (a switch over receiver and function
  names, as the Go, Java and TypeScript prototypes do). Importing the module
  is unavoidable to run a function, the import populates the registry, and
  the registry's lookup costs about 1 ms after it. The description holds
  every name a dispatcher would need, so one can be generated later if the
  lean-boot work changes the balance.
- Making the call-time boot faster. That is idea 3 (lean boot) and idea 2
  (warm worker) of the performance-ideas document.
- Removing the dynamic path, `runtime/`, or any function of the authoring
  module. Requirement 2.
- Loading a static module from a git ref or from another workspace. The
  entrypoint receives the caller's workspace and finds its module by a path
  baked at generate time (*Verified constraints*).
- Dependencies for version 2 modules, and a dynamic version 2 entrypoint.
  Both wait on the engine (*Rollout*).
- Supporting the `legacy` template on the static path. It scaffolds a
  `dagger.json`-era module whose `.gitignore` ignores `/sdk`, which a static
  module must commit.
- Self-calls (bindings that include the module's own types). The description
  is what they need; wiring them is `#13235`'s steps 4 and 5 and a separate
  change.
- Writing the version 2 manifest through `dagger/sdk-helpers`. Its
  `tomlContents` writes no `manifestVersion` at v1.0.2, v1.0.4 or `main`
  (*Verified constraints*), so this change writes the five lines itself.
  Moving to the helper once it supports version 2 is a one-function change.
- Source maps. The registry emits none.

## Measurements

All numbers are from one shared, noisy x86-64 Linux host: Python 3.14.7,
`uv` 0.11.16, the `dagger-io` library at `dagger/python-sdk` commit
`d551f327ae111b5213b4462186e04ed35817b9a2` installed in a virtual
environment. Each number is the middle of three runs.

**Fixture.** A module of 131 lines in two files using the surface
`dagger.mod` accepts: two object types, one interface, two enums with
member docstrings, 16 functions and 2 constructors; `from __future__ import
annotations`; a `create` classmethod; `field()` with `name=`, `default=`,
`default=list`, an enum default and an `InitVar`; `Annotated` metadata of
every kind; `str | None`, `list[str] | None`, `list[list[str] | None]`,
`Self`, `list[Self]`; a `function()(Other)` attribute; core object, scalar
and enum types; a relative import.

| Extraction | Time |
|---|---|
| Import the module in a process that already has the SDK loaded: decorators register 3 classes, 16 signatures resolved | 4.3 ms |
| `import dagger.mod` before that | 200 ms warm, 552 ms cold bytecode cache; 665 to 684 ms inside a fresh module container |
| A 400-line `ast` prototype: parse, resolve, serialize | 4.8 ms in process, 20 ms wall |
| `ty check` 0.0.80 | 100 to 110 ms |
| `mypy` 2.3.1 | 270 to 300 ms warm cache, 15.5 s first run |
| `pyright` 1.1.414 | 720 to 820 ms |
| `libcst` parse plus scope metadata | about 130 ms |

**Dang builder chain.** A scratch Dang module with every `TypeDef` and
`Function` call the renderer emits type-checked and evaluated on engine
`v1.0.0-beta.12` (commit `4932411f`) through `dagger call`. Two spellings
matter: the `JSON` scalar must be written `Dagger.JSON` in a Dang module
(bare `JSON` is Dang's own JSON namespace), and the cache policy members are
`FunctionCachePolicy.Never` and `.Default`.

**Digests.** On `v1.0.0-beta.12`, `Directory.digest` changes when only a
file's permissions change. `File.digest(excludeMetadata: true)` depends only
on the file's bytes (`core/file.go` at `4932411f`, `File.Digest`): it is
`"sha256:" + hex(sha256(sha256(bytes)))`, `sha256` over the content and
then `digest.FromBytes` over that 32-byte hash. The guard uses this field.

## Extraction approaches evaluated

| # | Approach | Speed | Correctness | Size | Operational cost |
|---|---|---|---|---|---|
| 1 | Import the module at generate time in its own container and read the registry | about 1 s per module (one boot), plus the dependency install the call path needs anyway | Exact: the producer is the registry the dynamic path uses | about 150 lines plus a container exec in Dang | The module's dependencies must install at generate time; module-level code runs at generate time |
| 2 | Standard library `ast` with an import and name resolver | 20 ms | A subset; the v0.20.7 analyzer shows the tail (aliases, constants, inheritance, runtime-valued defaults) is long and some of it unfixable | about 5,000 lines to reach v0.20.7's coverage, plus refusals | None |
| 3 | A type checker (`ty`, `mypy`, or `pyright`'s type server) | 100 ms to 800 ms | Resolves aliases; still needs the walker over decorated classes; cannot see runtime values | Large glue over unstable APIs | The module's dependencies must be importable anyway |
| 4 | `libcst` | about 130 ms | Same reach as 2 | as 2 plus a dependency | A dependency |
| 5 | A parser in Rust or Go | estimate: 5 ms | Same reach as 2 | as 2 plus a toolchain | A second language for Python semantics |

**Decision: approach 1.** It is exact, it is the smallest, and it is what
the owner concluded after shipping approach 2. Its cost is one interpreter
boot per module at `dagger generate`, which is the boot the dynamic path
pays at every session load today, moved to a development-time command.
Approach 2 wins on generate-time speed alone, and would strand every module
outside its subset when the default flips.

### Where the extraction runs

In the module's own container, built by this repository's runtime module
(`runtime/`, `moduleRuntime`), which the authoring module gains as a
dependency. `dagger generate` first vendors the library and the bindings
into `sdk/`, then builds the container from the module with that `sdk/` and
execs:

```text
python -m dagger.mod entrypoint --name hello --path .dagger/modules/hello --output /dagger/entrypoint
```

The subcommand imports the module through the runtime's own loader
(`dagger.mod.cli.load_module`), builds the description, renders `types.dang`
and `main.dang`, and writes them to the output directory. It opens no
engine connection.

## Verified constraints

Engine references are to `dagger/dagger` at `75c77722` unless marked
`main`, in which case they are to `4932411ff7a9b0771dd53779d9a085406cefb531`
(2026-09-11, tagged `v1.0.0-beta.12`).

**How the engine drives an entrypoint** (`core/sdk/dang/v2/entrypoint.go`).

- `runEntrypointDir` copies every `.dang` file in `entrypoint.source` into
  one temporary directory, appends the interface as
  `__module_entrypoint.dang` (that name is reserved), and runs the whole
  program on every `types` and every `call`.
- `findModuleEntrypoint` requires exactly one public type implementing
  `ModuleEntrypoint` with a zero-argument constructor. `let` fields with
  defaults are not constructor arguments.
- `types` results are `TypeDef` values the program built, loaded by ID.
  `validateEntrypointConstructors` rejects more than one object with a
  constructor.
- `call` receives `receiverValue` and `fnArgs` as `JSON` scalars. When the
  program encodes a record holding them with `JSON.encode`, each is written
  as a JSON text string (first attempt, probe 1). The result must be a
  `JSON` scalar or null.
- The `workspace` argument is `Query.currentWorkspace`; its `cwd` is `/`
  from the root and from inside the module (probe 1). The entrypoint finds
  its module by a path baked at generate time.
- Inside a container exec started by the program, `Query.currentFunctionCall`
  fails; inside the Dang program it resolves. An exec whose inputs are
  unchanged is reported `CACHED` by a later CLI invocation.
- `resolveEntrypointSourceDirectory` reads `entrypoint.source` relative to
  the manifest's directory and rejects absolute paths and paths that escape
  the module directory.
- `ModuleSource.introspectionSchemaJSON` loads the module's dependencies and
  asks the schema builder for the module-facing schema; it does not run the
  module.

**Version 1 manifests on the `#14038` engine.** `parseCurrentModuleConfigTOML`
reads `manifestVersion` first; when absent, the version 1 parser runs, and
`SDKForModule` takes the entrypoint path only when `Entrypoint != nil`. The
specification text says the opposite. The code is what runs; *Rollout*
makes it a condition.

**What the dynamic path runs on** (`main`). `core/sdk/loader.go` resolves
the runtime name `python` to the engine-baked Python SDK. A module can
instead name this repository's `runtime/` by ref.

**Where CI runs.** This repository's `dagger.toml` registers one module,
`engine-e2e`. Its `devSdkCheck` builds an engine from `dagger/dagger` at
`0d031c08ef3e379c6f4eb7f8f5cad4638a168863` (after `#13992`, without
`#14038`), mounts this checkout as the `python` SDK in a scratch workspace
whose `dagger.toml` is `.dagger/modules/engine-e2e/workspace.toml`, and runs
`dagger check` inside it, which is where every `e2e:*` check runs. The
checks on `dagger/python-sdk#27` are `engine-e-2-e:dev-sdk-check` and
`load`. "CI" below means that development engine.

**How SDK settings reach the SDK** (`main`). `PythonSdk`'s constructor
fields are the SDK's settings, exposed as kebab-case flags of `dagger module
init python` and persisted on the scope in `dagger.toml`
(`SDKScope.Settings`). `effectiveSDKModuleSettings`
(`core/schema/workspace_sdk_init.go`) applies, highest first, the scope's
own settings, then the SDK module's `[modules.<sdk>.settings]` as seen
through user and environment overlays. `withInitModule` on an existing scope
merges new explicit settings in, and an explicit `false` is stored. `dagger
module init` already owns an `--entrypoint` flag, so the setting is not
named `entrypoint`.

**Manifest builder** (`dagger/sdk-helpers` v1.0.2). `withDangEntrypoint`
records an entrypoint, `withoutLegacyFields` drops the runtime fields, but
`tomlContents` never writes `manifestVersion` and preserves `$schema` and
`disableDefaultFunctionCaching`, which version 2 rejects. A manifest it
writes for an entrypoint parses as version 1 on the `#14038` engine and
reaches `errMissingSDKRef`.

**Schema view for a version 2 manifest.** On the `#14038` engine a version
2 module gets the current schema view. On an engine without `#14038` the
version 2 keys are unknown, no `engineVersion` is present, and the engine
serves its oldest view (`v0.9.9`, whose `Workspace` has no `cwd`). Bindings
must never be generated from that view; the static path reads the schema
from a version 1 manifest.

**The runtime registry** (`sdk/src/dagger/mod/` at `d551f32`). Decorators
populate `Module._objects` and `Module._enums` at import;
`Module._typedefs()` turns them into `TypeDef` selections through
`_converter.to_typedef`, `Function.parameters` and `Parameter`. The main
object is the class named `DAGGER_MAIN_OBJECT`. Two of its behaviours are
worth naming because the description preserves them: a field's description
is `get_doc(field.type)` on the raw annotation (a class's docstring when the
annotation is a class, and nothing under `from __future__ import
annotations`), and a nullable argument's `TypeDef` gets `withOptional(true)`
twice.

## Proposed approach

### 0. One setting, two phases

`PythonSdk` gains one constructor field:

```dang
"""
Generate a static entrypoint that carries the module's types, so the
engine loads them without running the module.
"""
pub staticEntrypoint: Boolean! = false
```

`dagger module init python --static-entrypoint` sets it; the engine persists
it on the scope and passes it back on every `dagger generate`. A workspace
sets it for every Python module through `[modules.python-sdk.settings]`,
which a scope's own setting overrides.

| | Phase A (this change) | Phase B (a later change) |
|---|---|---|
| Default | `false` | `true` |
| Opt in / out | `--static-entrypoint` selects the static path | `--static-entrypoint=false` selects the dynamic path |
| Existing module, setting unset, next `dagger generate` | unchanged | migrates to the static path, or fails with an actionable error if refused |
| Engine floor of this SDK module | `v1.0.0-beta.11`, as today | the first release that loads manifest version 2 |

Silent fallback to the dynamic path was rejected: requirement 2 makes the
dynamic path a stated choice. A boolean is what "invert the flag" means; a
string setting was considered and rejected as having no third value.

### 1. What each path writes

For a module scope named `hello` at `.dagger/modules/hello`:

| | dynamic path | static path |
|---|---|---|
| `dagger-module.toml` | version 1: `name`, `engineVersion`, `[runtime] source = "python"`, dependencies (as today) | version 2: `manifestVersion = 2`, `name`, `[entrypoint] kind = "dang" source = "./sdk/entrypoint"` |
| `sdk/` | client library and `src/dagger/client/gen.py` (as today) | the same, plus `sdk/entrypoint/` |
| `sdk/entrypoint/main.dang` | absent, removed if present | `type Entrypoint implements ModuleEntrypoint`: `types()`, `call()`, the staleness guard |
| `sdk/entrypoint/types.dang` | absent, removed if present | `type ModuleTypes`: the literal `TypeDef` list |
| `sdk/entrypoint/build.dang` | absent, removed if present | `type PythonModuleBuild`: the module container build, copied from `runtime/build.dang` with its externals inlined |
| Module load | engine calls the runtime module, execs Python once for types | engine evaluates `types.dang`; no exec |
| Function call | engine execs the runtime container | `call()` checks the source digests, builds the container from `build.dang`, execs `python -m dagger.mod call` |

**Who decides the path.** `generateScope` reads the setting and stages the
version 1 manifest the schema is read from. `Mod.generated` writes the
version 2 manifest, next to the entrypoint it generated, so generating a
single module directly writes the same files. `mod(...)` reads the setting
too, and also treats a module whose manifest has `[entrypoint]` as static, so
`dagger generate` through either entry regenerates a static module
consistently.

`generateScope(ws, isModule, name, clients)`:

1. As today: refuse standalone clients; return `ws` when `isModule` is
   false; render the template when the scope has no config.
2. Static path: refuse a non-empty `clients` list and the `legacy` template;
   refuse an existing version 1 manifest that carries a field version 2
   cannot (*Accepted differences*), naming the fields.
3. Write the version 1 manifest as today (for a version 2 manifest on the
   way back to the dynamic path, build it fresh from the name and the
   clients, because a version 2 manifest holds nothing else). This is the
   staging manifest: `ws.moduleSource(path).introspectionSchemaJSON` needs a
   manifest the engine understands, and a version 1 one yields the current
   schema view on every engine.
4. `Mod.generated`: vendor `sdk/` from the library and the bindings as
   today. Static path: build the module's container from the staging
   workspace with the fresh `sdk/` through the runtime module, run the
   `entrypoint` subcommand, add `build.dang`, place the result at
   `sdk/entrypoint/`, and replace the manifest with the version 2 text.
   `name` is written as a TOML basic string with `"` and `\` escaped; a name
   with a control character is refused. Dynamic path: remove
   `sdk/entrypoint/` when present (`Workspace.withoutDirectory`).
5. Return the workspace. `cwd` is untouched; `dagger.toml` is never written.

### 2. The description

`dagger.mod._describe` holds plain dataclasses (`ModuleDescription`,
`ObjectDescription`, `FunctionDescription`, `ArgumentDescription`,
`FieldDescription`, `EnumDescription`, `EnumMemberDescription`, `TypeRef`)
and two functions:

- `describe_type(annotation, context)` is the decision procedure of
  `_converter.to_typedef` as data: kind, name, description for scalars and
  enums, optionality, element for lists. `to_typedef` becomes a
  materialisation of it into a `TypeDef` selection in the same call order,
  so the existing tests that compare `TypeDef` chains pass unchanged.
- `describe_module(module)` is the decision procedure of
  `Module._typedefs()` as data: the main-object check, the module docstring,
  each object with its fields, functions and constructor, each enum with its
  members. `_typedefs()` becomes a materialisation of it into `dag.module()`.

Both paths therefore build their type definitions from one description.
There is no static subset, no rejection table and no differential test: what
the dynamic path registers is, by construction, what the static path writes.

The `entrypoint` subcommand refuses a description that the static path
cannot honour: any function with a `cache=` value. The refusal names the
function and the setting.

### 3. The generated entrypoint

`dagger.mod._entrypoint` renders a description to `types.dang` and
`main.dang`. For the module `hello` at `.dagger/modules/hello` with the
default template:

`types.dang`:

```dang
# Code generated by dagger. DO NOT EDIT.

type ModuleTypes {
  pub all: [TypeDef!]! {
    [
      typeDef
        .withObject("Hello")
        .withFunction(
          function("container", typeDef.withObject("Container"))
            .withDescription("A container with the workspace source, ready to build.")
        )
        .withConstructor(
          function("", typeDef.withObject("Hello"))
            .withArg("ws", typeDef.withObject("Workspace"))
            .withArg("baseImageAddress", typeDef.withKind(TypeDefKind.STRING_KIND), defaultValue: ("\"alpine:3.24\"" :: Dagger.JSON!))
        ),
    ]
  }
}
```

`main.dang`:

```dang
# Code generated by dagger. DO NOT EDIT.

type Entrypoint implements ModuleEntrypoint {
  let moduleName: String! = "hello"
  let modulePath: String! = ".dagger/modules/hello"
  let skippedDirs: [String!]! = [".venv", "__pycache__", "sdk"]
  let sourceFiles: [SourceFile!]! = [
    SourceFile(path: ".python-version", digest: ""),
    SourceFile(path: "pyproject.toml", digest: "sha256:..."),
    SourceFile(path: "requirements.lock", digest: ""),
    SourceFile(path: "uv.lock", digest: ""),
    SourceFile(path: "src/hello/__init__.py", digest: "sha256:..."),
  ]

  pub types(workspace: Workspace!): [TypeDef!]! {
    ModuleTypes().all
  }

  pub call(
    workspace: Workspace!,
    receiverType: String!,
    receiverValue: JSON,
    fnName: String!,
    fnArgs: JSON!,
  ): JSON! {
    let request = JSON.encode({{
      receiverType: receiverType,
      receiverValue: receiverValue,
      fnName: fnName,
      fnArgs: fnArgs,
    }})
    let result = runtime(workspace)
      .withExec(["python", "-m", "dagger.mod", "call", "--output", "/dagger/result.json"], stdin: request, experimentalPrivilegedNesting: true)
      .file("/dagger/result.json")
      .contents
    (result :: JSON!)
  }

  let runtime(workspace: Workspace!): Container! {
    let module = workspace.directory("/" + modulePath)
    if (module.exists("pyproject.toml") == false) {
      raise "module \"" + moduleName + "\" was generated at \"" + modulePath + "\" and is not there; run `dagger generate` after moving it"
    } else {
      let changed = sourceFiles.filter { f =>
        if (f.digest == "") {
          module.exists(f.path)
        } else {
          module.exists(f.path) == false or module.file(f.path).digest(excludeMetadata: true) != f.digest
        }
      }.map { f => f.path }
      let added = module.glob("**/*.py").filter { p =>
        isSource(p) and sourceFiles.filter { f => f.path == p }.length == 0
      }
      if ((changed + added).length > 0) {
        raise "module \"" + moduleName + "\" changed since its entrypoint was generated (" + (changed + added).join(", ") + "); run `dagger generate`"
      } else {
        PythonModuleBuild(
          contextDir: workspace.directory("/", include: [modulePath + "/**"], exclude: ["**/.venv", "**/__pycache__"]),
          subPath: modulePath,
          moduleName: moduleName,
        ).installed
      }
    }
  }

  let isSource(path: String!): Boolean! {
    path.split("/").dropLast(1).filter { segment =>
      segment.hasPrefix(".") or skippedDirs.contains(segment)
    }.length == 0
  }
}

type SourceFile {
  pub path: String!
  pub digest: String!

  new(path: String!, digest: String!) {
    self.path = path
    self.digest = digest
    self
  }
}
```

Rules of the renderer:

- **One builder call per description member, in `_typedefs()`'s order**, so
  the engine sees the same `TypeDef` on both paths: `withOptional(true)`
  before the kind; on a function `withDescription`, `withDeprecated`,
  `withCheck`, `withGenerator`, `withUp`, `withAgent`, then `withArg` per
  argument with a nullable argument's second `withOptional(true)`; on an
  object `withField` per field, `withFunction` per function, then
  `withConstructor`. `withCachePolicy` is never emitted.
- **The `JSON` scalar is written `Dagger.JSON`** (`defaultValue: ("..."
  :: Dagger.JSON!)`), verified in a Dang module; the `call` result uses the
  interface's own `JSON!`, as the engine's example does. Whether
  `Dagger.JSON` resolves inside an entrypoint program is dev-engine step 1;
  the contingency is one spelling in the renderer.
- **Every string goes through one quoting routine** that escapes `"`, `\`
  and control characters.
- **Only the main object has `withConstructor`.**
- **`types.dang` is a plain Dang type**, so that CI can load and evaluate it
  as a version 1 Dang module (*Testing*). `main.dang` is the only file that
  names `ModuleEntrypoint`.
- **The staleness guard.** The `entrypoint` subcommand hashes every file
  that can affect the description: `pyproject.toml`, `.python-version`,
  `requirements.lock`, `uv.lock`, and every `.py` file under the module
  directory outside `sdk/`, hidden directories and `__pycache__`. The digest
  is the engine's `File.digest(excludeMetadata: true)` value
  (*Measurements*). One of those manifests that is absent is recorded with an
  empty digest, so adding a lock file later, which changes what gets
  installed, is refused too. The skipped directory names are rendered into
  the entrypoint, so the guard's scan for an added `.py` file skips exactly
  what the hashing skipped. `call()` recomputes each digest and refuses to
  run when anything differs, naming the paths. It ignores permissions and
  timestamps. It costs one cached digest per file per call.

Why `call()` looks as it does: the request is one JSON object on stdin
whose two `JSON` members arrive as text strings; Python decodes each once.
The result is read from a file because user code prints. A non-zero exit
fails the exec, which fails `call()` with the process's stderr; a failure is
never cached. Only the module directory is mounted. `types()` ignores its
`workspace` argument.

### 4. The container build and the Python side

**`runtime/build.dang`** (new) holds `PythonModuleBuild(contextDir:
Directory!, subPath: String!, moduleName: String!)`, taken out of
`runtime/main.dang`: `base`, `install`, `pyConfig` and its TOML helpers,
image selection, `packageNameFor`, `mainObjectName`, `checkGeneratedFiles`,
`PyConfig`, `CamelState`. Its `installed` field is the installed container
with the `DAGGER_*` variables set and no entrypoint; a field named
`container` would shadow the global `container` constructor inside the
type and evaluate forever. The two reads of
`currentModule.source` (the image pins) are fenced between `#<externals>`
and `#</externals>` comment markers. `runtime/main.dang` keeps `type
PythonSdkRuntime` as a thin adapter that adds `runtime.py` and the
entrypoint after the install. Behaviour-neutral; the existing runtime checks
prove it.

The authoring module gains `runtime/` as a dependency, so `mod.dang` can ask
the runtime module for the module's container at generate time (the same
container `call()` builds later), and its `include` list admits `runtime/`
so `mod.dang` can read `runtime/build.dang` and the Dockerfiles to splice
`build.dang`. `mod.dang` refuses to emit a `build.dang` that still mentions
`currentModule`.

**`python -m dagger.mod call --output <path>`** (new `__main__.py`): read
one request object from stdin; decode `receiverValue` (null becomes `{}`)
and `fnArgs` once each; import the module through `cli.load_module()`;
dispatch through the existing `Module.get_result`; create the output
directory and write the JSON result. Telemetry is initialised and shut down
as `app()` does. No connection is opened up front. A `ModuleError` or API
error is logged as today and the process exits 2; an unexpected exception
exits 1; neither writes the result file. `Module.dispatch(request)` is the
unit-tested core.

### 5. What a call costs

| | dynamic path | static path |
|---|---|---|
| module load | runtime module evaluation and one Python boot per session | Dang evaluation of `types.dang`; no exec |
| constructor or function call | one Python boot each | Dang evaluation, one cached digest per source file, then one Python boot each |
| `dagger generate` | bindings exec | bindings exec plus the module's container build (cached by content) and one Python boot |
| source edit | visible on the next call | refused until `dagger generate` |

## Alternatives considered

**A static `ast` analyzer** (the owner's first idea, the doc's first
version, and v0.20.7). Rejected on the evidence of `#11803` to `#13251`;
see *Extraction approaches evaluated*.

**Dynamic `types()` in the entrypoint** (the first attempt). Keeps the boot
on first load after an edit. Rejected by requirement 1.

**A dynamic version 2 entrypoint as the opt-out**, instead of the dynamic
path. Needed only if the engine stops loading version 1 manifests.
*Rollout* makes that a condition; the first attempt is the fallback design.

**Emit a JSON description and decode it in Dang** at load time. Two
representations of one decision and a decoder shipped into every module.
Literal rendering has one representation and no runtime decoder.

**Render the entrypoint from Dang.** Dang has no model of the module's
types; the renderer needs escaping and recursion, which Python does in a few
hundred lines.

**Generate a static Python dispatcher.** See *Non-goals*.

**Refuse the static path on an engine that cannot load it**, by reading
`__schemaVersion`. Rejected: CI's engine cannot load version 2 either, so
every check would fail. The engine floor in phase B enforces it.

**Guard staleness with `Directory.digest`** over a pattern. It hashes
permissions, so a fresh checkout with another umask is "stale". Per-file
content digests do not.

**Pin CI's dev engine to an engine that carries `#14038`.** The commit
would have to be a merge pushed to a fork and fetched from the fork's URL,
and it moves every `e2e:*` check onto an unreviewed draft. Offered as an
optional patch, not assumed.

## Rollout

Phase B has these gates, all of which must hold:

1. A `dagger/dagger` release loads manifest version 2 with the
   `ModuleEntrypoint` interface at `75c77722` or a successor this SDK has
   been adapted to.
2. That release still loads a version 1 `dagger-module.toml` with
   `[runtime]`. If it does not, the dynamic path needs the dynamic version 2
   entrypoint first.
3. The engine defines how a version 2 module uses other modules, and this
   SDK implements it.
4. The engine passes a cache-policy signal to `call`, or the owner accepts
   that functions with any `cache=` value stay on the dynamic path.
5. This SDK module's `engineVersion` is raised to the release in gate 1, so
   that an older engine refuses to load the SDK instead of generating
   modules it cannot run. A workspace on an older engine then keeps the
   older SDK for its dynamic-path modules too.

## Affected components

- `future/static-module-entrypoint.md` (this document)
- `sdk/src/dagger/mod/_describe.py` (new); `_converter.py` and `_module.py`
  materialise the description; `_entrypoint.py` (new, the renderer and the
  digests); `__main__.py` (new, `entrypoint` and `call`)
- `sdk/tests/mod/test_describe.py`, `test_entrypoint.py`,
  `test_dispatch.py` (new)
- `runtime/build.dang` (new), `runtime/main.dang` (thin adapter)
- `python-sdk.dang`: the setting, the version 2 manifest, the refusals;
  `mod.dang`: the static path in `generated`
- `dagger.json`, `dagger-module.toml`, `dagger.lock`: `runtime/` included
  and depended on
- `.dagger/modules/e2e/main.dang` and fixtures: the static checks
- `README.md`

## Testing

### Unit tests (`sdk/tests/mod`)

- `test_describe.py`: `describe_module` on modules built with `Module()`
  covers objects, interfaces, enums with member docs, fields with the
  description quirk, constructors (`dataclass`, `create`, `__init__`,
  `InitVar`, `init=False`), `function()(Other)`, every `Annotated`
  metadata, `Self`, nested optional lists, name normalisation, the raw
  `cache` value; `to_typedef` still equals the expected `TypeDef` chains
  (the existing `test_registration.py` assertions).
- `test_entrypoint.py`: the rendered `types.dang` and `main.dang` compared
  to committed golden files for a module covering the surface; a description
  with a double quote, a backslash, a newline and a tab; exactly one
  `withConstructor` and no `withCachePolicy`; refusal of a `cache=`
  function; the digest of a file equals `"sha256:" +
  sha256(sha256(bytes))`; the file set excludes `sdk/`.
- `test_dispatch.py`: a request with `receiverValue: null` and `fnName: ""`
  calls the constructor; the two JSON members arrive as text and are
  decoded once; an omitted argument with a default is not passed; a null
  value is passed as `None`; the `call` subcommand run as a subprocess with
  `--output` under a temporary directory writes the file on success and
  exits 2 without a file when the function raises.

### e2e checks on CI's engine (`.dagger/modules/e2e`)

- `staticScopeInitCheck`: `generateScope` with `staticEntrypoint: true` on
  an empty scope: a manifest whose whole content is the five version 2
  lines; the template files; a `gen.py` that defines `cwd` and
  `with_new_file` on `Workspace` (the current schema view); a `main.dang`
  with `implements ModuleEntrypoint`, the name, the path, and source files
  whose digests equal `File.digest(excludeMetadata: true)` of the same
  files; a `types.dang` naming the template's class and its `container`
  function with no `withField`; a `build.dang` with both image pins and no
  `currentModule`.
- `staticScopeSwitchCheck`: on a committed dynamic-path fixture, generate
  with the setting on, then off, then on again: the version 2 manifest and
  `sdk/entrypoint/` appear, then a version 1 manifest equal to the fixture's
  except `engineVersion` and no `sdk/entrypoint/`, then the first result
  again. User files are untouched throughout.
- `staticScopeRefusalsCheck`: `clients` non-empty, the `legacy` template, a
  manifest with `include`, one with `disableDefaultFunctionCaching = true`,
  and a fixture with `cache="never"` each raise before any file is written.
- `staticTypesLoadCheck`: the rendered `types.dang` of a fixture, next to a
  one-type wrapper `main.dang` and a version 1 manifest with `[runtime]
  source = "dang"` and `engineVersion = "v1.0.0-0"`, is called through the
  `sdk-sdk` harness and returns the fixture's type count. This proves the
  rendered Dang type-checks against the engine's schema and evaluates.
- The existing checks guard the dynamic path, including the
  `runtime/main.dang` split.

### On the dev engine

The dev engine is `dagger/dagger` at `75c77722` merged onto
`0d031c08ef3e379c6f4eb7f8f5cad4638a168863`. The merge's only conflict is in
`dagger.toml` (`75c77722` adds `[modules.tiny]`, `0d031c08` moves
`[modules.tla-check]` and adds a comment); keeping both, `tiny` first,
gives tree `d8e9df6331fb3cbafbd3d1cb80a58c0f74c2c074`. The engine image and
CLI are built with `dagger/dagger`'s `.dagger/modules/dev` and
`.dagger/modules/cli-dev`.

1. Probe, inside a nested exec: `Dagger.JSON` in an entrypoint program,
   `DefaultPath` and `DefaultAddress` resolution, `dag.current_module()`
   and its `source()`. Record differences in *Accepted differences*.
2. Scratch workspace: install this repository as SDK `python`, `dagger
   module init python --name=hello --static-entrypoint`, inspect the files.
3. `dagger call hello container`; then the *Measurements* fixture as a
   module: `dagger functions` and the GraphQL `__type` of every object
   compared with the dynamic path on the same engine, with `[runtime]
   source = "python"` and with this repository's `runtime/`.
4. Edit a signature and call without `dagger generate`: refused, naming the
   file. Change only a file's permissions: the call runs. `dagger generate`:
   the new signature is served.
5. `dagger module init python --path <scope> --static-entrypoint=false`,
   then `dagger generate`: back on the dynamic path, `sdk/entrypoint/` gone,
   the module runs. Edit the setting in `dagger.toml` to `true` and
   generate: back. Set `[modules.python-sdk.settings] staticEntrypoint =
   true` with no scope setting: the same.
6. Wall clock and the engine's `loading type definitions` span for the
   fixture on both paths, three runs each.
7. A module with `cache="never"` fails `dagger generate` on the static
   path; the same module runs on the dynamic path.

## Risks

- **`dagger/dagger#14038` may change.** Insulated: the description, the
  renderer's `types.dang`, the manifest text. Exposed: `main.dang`'s
  template and the `call` subcommand's request decoding.
- **The engine may stop loading version 1 manifests.** Rollout gate 2; the
  first attempt's dynamic `types()` is the named contingency.
- **Version 2 modules cannot have dependencies, and cache policies cannot be
  honoured.** Both refused; both gate phase B; reported upstream.
- **Behaviours inside the nested exec are unverified** until dev-engine
  step 1, which runs before any code beyond this document.
- **Generate needs the module's dependencies.** A module whose dependencies
  cannot install at generate time cannot generate a static entrypoint. The
  same install is needed to run the module.
- **Phase B rewrites manifests on `dagger generate`.** The error path is
  explicit and the settings exist to pre-empt it.
- **The staleness guard adds a failure mode.** A source edit that does not
  change the types still needs `dagger generate` before the module can be
  called. `dagger generate` is the command users already run after editing.

## How the earlier conclusions held up

Held: every engine mechanic in the first attempt's *Verified constraints*,
its six upstream findings, the `call()` design (request on stdin, result in
a file, module directory baked in), the `runtime/build.dang` split, "do not
cut over".

Did not hold:

- "There is no static analyzer for Python modules." There was one, in
  v0.20.7, and it failed on the long tail the first attempt did not know
  about. The first attempt was right that types come from importing the
  classes, and wrong that this had to happen at load time.
- "Every signature edit would then need `dagger generate`" as a reason to
  keep discovery at load time. Decided the other way by requirement 1.
- "An engine with `#13992` but not `#14038` is transient." It is the
  released `v1.0.0-beta.12` and the engine CI runs.
- The first version of this document recommended a static `ast` analyzer at
  generate time. Rejected after the owner pointed at the v0.20.7 history.

Where `#14038` disagrees with itself or with what runs: the specification
says version 1 manifests are rejected, the code accepts them; the
specification says `fnArgs` values are embedded JSON, the request the
entrypoint builds carries them as text; the specification says the
entrypoint can read files above the module directory, but `cwd` is `/`.

## Findings to report

To the author of `dagger/dagger#14038`, restating the first attempt's
findings 1 to 6 (workspace `cwd` is `/`; no `include` list; no cache-policy
signal; no structured error channel; no ID loading from Dang; no module
description) and adding: a version 2 module has no dependency model; an
engine without version 2 support silently serves the oldest schema view for
a version 2 manifest; the specification and the code disagree on version 1
manifests.

To the author of `dagger/sdk-helpers`: `tomlContents` never writes
`manifestVersion = 2`, and carries `$schema` and
`disableDefaultFunctionCaching`, which version 2 rejects.

To the author of `dagger/java-sdk#19`: `defaultValue: JSON.decode("...")`
is likely a type error; `("..." :: Dagger.JSON!)` type-checks.

## Implementation plan

StGit patch series on top of `dagger/python-sdk`
`d551f327ae111b5213b4462186e04ed35817b9a2`. Each patch carries
`Signed-off-by: Yves Brissaud <yves@dagger.io>` and no other trailer, and
leaves `dagger check` and `uv run --frozen pytest` green.

1. `future: design static module entrypoints for Python modules` — this
   document.
2. `sdk: describe module types as data` — `_describe.py`; `to_typedef` and
   `_typedefs()` materialise it; `test_describe.py`.
3. `sdk: render a static Dang entrypoint from a module description` —
   `_entrypoint.py`; the `entrypoint` subcommand; `test_entrypoint.py` and
   golden files.
4. `sdk: dispatch a module call from a JSON request` — `Module.dispatch`;
   the `call` subcommand; `test_dispatch.py`.
5. `runtime: move the container build into a shared type` —
   `runtime/build.dang`; `runtime/main.dang` as the adapter.
6. `python-sdk: generate a static entrypoint behind the staticEntrypoint
   setting` — the setting, the manifest, the refusals, `Mod.generated`, the
   `runtime/` dependency and include, the four e2e checks.
7. `docs: describe the static path and the rollout` — `README.md`.
8. `future: record the dev-engine results` — *Progress* updated.
9. Optional: `engine-e2e` pinned to an engine that loads version 2, on the
   owner's decision.

## Progress

- Phase 0 — orientation: done 2026-09-11. Repository `dagger/python-sdk`,
  base `d551f327ae111b5213b4462186e04ed35817b9a2`; feature branch
  `python-sdk-static-entrypoint-lead-pythonsdk-080b6d74` on
  `eunomie/python-sdk`. Design home `future/`. StGit; sign-off
  `Signed-off-by: Yves Brissaud <yves@dagger.io>`; no AI attribution. CI:
  Dagger Cloud checks from `dagger.toml`.
- Phase 1/2 — feature doc and plan: `ff41fcd`, revised as `568b35b`,
  `c778c8c`, `ef38625` (a static `ast` analyzer at generate time).
- Phase 3 — three adversarial review rounds (a skeptic and a design/spec
  reviewer). Round 1: both "rework"; blockers fixed (sdk-helpers writes no
  `manifestVersion`; CI is the dev engine at `0d031c08`; the result file's
  directory). Rounds 2 and 3: "approve with changes"; all findings folded in
  (per-file content digests, cache policies refused, version 1 fields
  refused rather than dropped, the double-hash digest format).
- Plan gate — 2026-09-11: the owner asked whether the v0.20.7 analyzer had
  been considered; it had not. Re-evaluation with `#11803`, `#13095`,
  `#13251` and `#13235` in hand moved the recommendation to importing at
  generate time. Approved by the owner on 2026-09-12: import at generate,
  static `types()`, `call()` through the registry, no generated Python
  dispatcher, a boolean setting with the dynamic path kept.
- Phase 4 — implementation: done 2026-09-12, patches 2 to 7 of the plan.
  Found on the way: a `pub container` field in `PythonModuleBuild` shadowed
  the global `container` constructor that `base` starts from, and the
  evaluation never ended; the field is `installed`. Unit tests: 232 pass;
  ruff clean. e2e on a local `v1.0.0-beta.12` engine through the
  `engine-e2e` workspace: `runtime-call`, `runtime-requires-generated-files`,
  `toml-generate`, `generate-scope-init`, `generate`, `static-scope-init`,
  `static-scope-switch`, `static-scope-refusals` and `static-types-load`
  pass.
- Dev-engine results (a `dagger/dagger` build with the version 2 loader,
  `7ebd6da5`, 2026-09-03): its SDK interface predates `generateScope`, so
  `hello` was generated on the beta.12 engine and loaded on the dev engine.
  `dagger functions` and `dagger call hello --help` match the same module
  on the dynamic path; `dagger call hello container with-exec ... stdout`
  runs. An edited signature is refused naming `src/hello/__init__.py`; a
  permission-only change runs. Wall clock, three runs each, warm:

  | | dynamic (this `runtime/`) | static |
  |---|---|---|
  | `dagger functions` | 2.7 s to 3.1 s (7.7 s cold) | 1.2 s |
  | `call hello container with-exec echo stdout` | 5.5 s to 6.4 s | 5.8 s to 7.4 s |

  The load is the win; a call costs the same Python boot on both paths.
- Phase 5 — code review, 2026-09-12: two reviewers (correctness and
  safety; design and simplicity), both "approve with changes". Fixed: the
  guard records every manifest, absent ones with an empty digest, and
  `.python-version`; the rendered `added` scan skips the same directories
  the renderer skips; `Mod.generated` writes the version 2 manifest so a
  module generated directly matches one generated through the scope;
  `source = "."` is accepted; the install path is keyed on the module
  directory's digest. Dropped: digesting `dagger-module.toml` (the staging
  manifest at generate time is not the final one; the version 2 manifest
  carries only the name). Re-verified on the dev engine: a `.venv/` or a
  hidden directory with Python files does not trip the guard; an added
  `uv.lock` or `src/hello/extra.py` is refused naming the file.
