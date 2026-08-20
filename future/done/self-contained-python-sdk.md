# Self-contained Python SDK

author: yves
created: 2026-08-17
status: done (PR 1 of 2 landed as a draft; PR 2 flips targetRuntime)
related: `github.com/dagger/dagger` `future/spin-out-generated-clients.md`,
`future/sdk-tests.md`; `github.com/dagger/java-sdk` (prior art)

## Problem

`dagger/python-sdk` is not the Python SDK. It is only the *authoring* half of
it: `initModule`, `mod … config`, `generateAll`, module discovery. Everything
that actually makes a Python module run — the client library (`src/dagger`),
the code generator (`codegen/`), and the module runtime — still lives in
`dagger/dagger` under `sdk/python/`, is baked into the engine container, and is
resolved by a pinned digest.

Verified on `dagger/dagger@501b57e0476dee5881b99a064c3c04173134ecc7`
(2026-08-14), and, where the design depends on the *released* engine the checks
run against, also on `v1.0.0-beta.9`:

- `core/sdk/loader.go:141` → `namedSDK`: `case sdkPython: return
  l.loadBuiltinSDK(ctx, root, sdk, digest.Digest(os.Getenv(distconsts.PythonSDKManifestDigestEnvName)))`.
  `namedSDK` is tried before any ref and only `errUnknownBuiltinSDK` falls
  through (`core/sdk/loader.go:56-61`), and `python@x` is rejected outright
  (`:276-278`). The short runtime name `python` has exactly one resolution
  target: the engine-baked `sdk/python/runtime` module.
- The workspace `[modules.<n>.as-sdk]` registry is an **authoring** registry.
  `installedSDKSource`'s only callers are `workspace_module_init.go`,
  `workspace_builders.go`, `workspace_client.go` and `workspace_sdk.go`; module
  loading explicitly does not use it — "the runtime itself resolves in-engine
  when a consuming module loads" (`engine/server/session_workspaces.go:539-546`).
- `core/sdk/workspace_module.go` → `WorkspaceModuleForRuntime` has no non-test
  callers. Its static name→ref table is a superseded pattern; the CLI's
  `internal/cmd/dagger/sdks.json` + `setupResolveMigratedSDKs` own migration
  name resolution now. It must not be revived as a module-loading hook.

The consequence that matters is **release-cadence ownership**, not behaviour:
the Python SDK cannot ship a runtime, library or codegen fix without an engine
release, and this repo's checks can only exercise the parts of the SDK it
actually contains.

(Two things this doc previously overstated, corrected: the e2e suite does
already execute the builtin runtime's codegen in a container via
`e2e:generate-check`; and modern modules already avoid runtime codegen today —
`v1.0.0-beta.9:sdk/python/runtime/main.go:195` has `moduleRuntimeTrusted`. So
goal 2 below is about *where the code lives and who releases it*, plus a
genuinely simpler implementation — not a new user-visible capability.)

## Goals

1. `dagger/python-sdk` contains the Python client library, the code generator,
   and a module runtime — enough to make a Python module work end to end,
   released on this repo's cadence.
2. New and migrated (`dagger-module.toml`) modules run on **python-sdk's own**
   runtime, which is no-codegen-at-runtime and materially simpler than the
   current combined runtime.
3. Legacy (`dagger.json`) modules keep working **exactly** as today, served by
   `dagger/dagger`'s in-tree `sdk/python` codegen + runtime, with codegen at
   runtime. No behaviour change, no migration required.
4. Keep the runtime name `python` wherever the engine allows it, and document
   precisely where it does not and what would change that.

## Non-goals (YAGNI)

- Deleting or modifying `dagger/dagger`'s `sdk/python`. Legacy modules depend
  on it; it stays as-is. This work is additive from `dagger/dagger`'s side.
- Any change to `dagger/dagger` in this workstream. See *Residual engine work*.
- Publishing `dagger-io` to PyPI from this repo, or changing the released
  library's packaging/versioning story.
- Porting `sdk/python`'s Sphinx docs, changelog history, or its client/
  provisioning test suites.
- Supporting `dagger init --sdk python` / `dagger develop` scaffolding from the
  new runtime. Template scaffolding is `initModule`'s job in this repo.
- A `pip`-only (non-`uv`) code path beyond what the copied code already
  provides. Lock-file semantics are copied verbatim, not redesigned.

## Verified constraints

The engine decides whether a runtime may regenerate bindings **by config file
format alone**, independent of which SDK is loaded (`core/sdk/utils.go:20-25`,
same at `v1.0.0-beta.9`):

```go
// dagger.json always (legacy behavior), dagger-module.toml never
func useRuntimeCodegen(src dagql.ObjectResult[*core.ModuleSource]) bool {
	return src.Self().ConfigFilename != modules.Filename // "dagger-module.toml"
}
```

and it omits the introspection JSON from the `moduleRuntime` call when that is
false *and* the SDK opted in by declaring `introspectionJson` optional
(`core/sdk/module_runtime.go:50,90-91`, `core/sdk/module.go:75-88`).

So the legacy/modern split is expressed for us by the engine, at the
`moduleRuntime` call boundary. We do not sniff for `dagger.json` in the runtime.

Precisely: a module whose config is `dagger-module.toml` is never handed
introspection JSON. This is *not* the same as "the new runtime can never
receive it" — nothing stops a legacy `dagger.json` from naming
`github.com/dagger/python-sdk/runtime` explicitly, in which case introspection
*is* passed and ignored. That module then fails `requireGeneratedFiles` with an
actionable "run `dagger generate` and commit" error unless it has committed
them, in which case it simply works. Either way this is a
supported-configuration statement, not an engine-enforced invariant.

`codegen` is different: it is always called with introspection JSON
(`core/sdk/module_code_generator.go:36,50-61`). That is authoring time, not
module-load time, and it is where the copied code generator has to live.

## Proposed approach

Two additions to this repo, no removals from `dagger/dagger`.

### 1. `runtime/` — python-sdk's own module runtime

A copy of `dagger/dagger:sdk/python/runtime` (a Go Dagger module), then
simplified, renamed to `python-sdk-runtime` so it does not collide with this
repo's root module name.

### 2. `runtime/sdk/` — the vendored client library and code generator

A copy of `dagger/dagger:sdk/python`'s library surface (`pyproject.toml`,
`uv.lock`, `ruff.toml`, `LICENSE`, `README.md`, `src/dagger/**`, `codegen/**`,
`tests/conftest.py`, `tests/codegen/**`, `tests/mod/**`). This is what gets
vendored into modules and what runs codegen.

**It lives inside the runtime module, not at the repo root, and is read with
`dag.CurrentModule().Source().Directory("sdk")`.** That is a deliberate
correction to the obvious layout. Upstream's `New()` takes the library through
a contextual argument (`+defaultPath=".."`), which is dead code for the builtin
path — the engine passes the directory explicitly
(`core/sdk/loader.go:212-252` → `core/sdk/module.go:114-122`) — and is *unsafe*
for a ref-loaded SDK: when a Workspace is bound into the context, contextual
argument resolution is redirected to the **consuming** workspace, "unilaterally,
whether the module was loaded from Host, Git, or a Directory"
(`core/modulesource.go:1461-1472`, `workspaceContextDirPath` at `:1541-1547`),
and `dagger generate` / `dagger check` bind one. A `+defaultPath="../sdk"`
would then resolve to `/sdk` in the *user's* workspace.

`dag.CurrentModule().Source()` has no such ambiguity: `currentModuleSource`
(`core/schema/module.go:2951-3003`) builds from the module's own
`Source.ContextDirectory` and never consults `WorkspaceFromContext`. The
runtime already relies on it for its entrypoint script (`main.go:438-447`).
`sdkSourceDir` stays as an optional constructor argument so extension SDKs can
still inject their own, defaulting to the vendored copy when absent — which is
exactly the nil the engine passes for a ref-loaded module
(`core/sdk/loader.go:123` → `core/sdk/module.go:114-122`).

**Dropping `+defaultPath` also drops `+ignore`, and that was load-bearing.**
Upstream's contextual argument carries an allowlist (`main.go:52`) applied as
`CopyFilter{Exclude: arg.Ignore}` (`core/modfunc.go:1069-1071`), which is what
keeps `WithSDK` from vendoring junk into every user module. A bare
`CurrentModule().Source().Directory("sdk")` is unfiltered, so the same
allowlist must be re-applied explicitly with `Directory.Filter`, and
`runtime/dagger.json` gets an `include` list as a second line of defence. This
matters most for the local-path fixture, whose runtime source comes from the
working tree: without it, a developer's `runtime/sdk/.venv` or `__pycache__`
would leak into both the module-source digest and the vendored output, and
nothing would fail loudly.

### 3. What actually gets simplified

The runtime is reached only by modules that build from committed files, so the
branch disappears rather than being carried:

| | Today (dagger/dagger) | Here |
|---|---|---|
| `moduleRuntime` | branches on `introspectionJson == nil`: trusted path *or* vendor + codegen + template + lock-update + install | the trusted path, unconditionally |
| `TrustedSource` field | set only on the trusted path; gates two behaviours | gone as a *field*; both behaviours become unconditional (see below) |
| `Codegen` | vendor + codegen + template + lock-update | vendor + codegen + lock-update (authoring time) |
| `WithTemplate`, `template/{__init__,main,pyproject}` | scaffolds a new module | gone — `initModule` in this repo owns templates |
| `template/runtime.py` | shipped under `template/` | `runtime/runtime.py` — it is the entrypoint, not a template |

Two corrections to an earlier, wrong version of this table, both found in
review:

- **`TrustedSource` is load-bearing, not bookkeeping.** It gates keeping the
  committed `sdk/` instead of stripping and re-vendoring it
  (`discovery.go:292-305`) and adding `--locked` to `uv sync`
  (`main.go:565-572`). "Always true" therefore means two deliberate
  unconditional rewrites, not deleting a field and its `if`s.
- **`IsInit` and `MainObjectName` stay.** The trusted path reads `IsInit` to
  raise its "no source to trust" error (`main.go:221-223`) and
  `UseUvLock()` reads it (`discovery.go:153`); `MainObjectName` is exported as
  `DAGGER_MAIN_OBJECT` by `WithSource` (`main.go:517`), which the trusted path
  calls. Only the *template substitution* that used them goes away.

Lock-file selection semantics (`UseUvLock`, `WithUpdates`, the
`requirements.lock` fallback) are copied unchanged. Modules scaffolded by this
repo's templates ship no lock file and, since `IsInit` is false for a
`dagger-module.toml` module, take the pip-compatible install path — exactly as
they do today under the engine builtin. Changing that is a separate decision,
deliberately not bundled here.

### 4. `targetRuntime` points at this repo's runtime

`python-sdk.dang`'s `targetRuntime` changes from `"python"` to
`"github.com/dagger/python-sdk/runtime"`, so `dagger module init python`
writes that into the new module's `dagger-module.toml`
(`core/schema/workspace_module_init.go:119-128` writes it verbatim). The engine
resolves it through `externalSDKForModule`. This is what `dagger/java-sdk`
already does (`main.dang:15`).

This is **not a one-line change**: `python-sdk.dang:6`'s `tomlConfigPattern`
only matches `source = "python"`, and `mod()` → `validateConfig`
(`python-sdk.dang:94-130`) raises "Dagger module does not use the Python SDK"
on a mismatch. Without widening it, every module this SDK creates is rejected
by its own `mod` API — including the documented `dagger call python-sdk mod
--path my-module generate`. The pattern is widened in PR 1 (it must accept the
fixture's local path anyway). It is the only behavioural hard-code of
`"python"`: `mod.dang`, `mod-config.dang` and `template.dang` contain none, and
`dagger.toml:20`'s `as-sdk name = "python"` is the SDK's *name*, correctly
untouched.

Widen it narrowly — `python`, this repo's runtime ref, and paths ending in
`/runtime` — rather than "any local path", which would make `mod()` accept
modules of any language and hollow out the error `moduleLookupCheck` covers.
The durable fix is to validate against the `as-sdk` managed list the way
`modules()` already does (`python-sdk.dang:26,32`) instead of regexing config
text; that is a follow-up, not PR 1.

Legacy modules are untouched: their `dagger.json` keeps `sdk.source: "python"`,
which keeps resolving to the engine builtin.

### Why not keep `targetRuntime = "python"`

Because `namedSDK` matches the builtin table before it ever tries a ref, and
nothing between a `dagger-module.toml`'s `[runtime] source` and `SDKForModule`
consults the workspace. `"python"` cannot mean two things, and the one thing it
means is the engine-baked module.

Goal 4 is met as far as this repo can meet it: `python` remains the name users
type (`dagger module init python`), the name in `sdks.json`, and the runtime
name for every legacy module. Only the value *written into a new module's*
`dagger-module.toml` differs.

## Delivery sequencing — two PRs, and why

This repo's CI runs `github.com/dagger/sdk-sdk`'s black-box checks, which
vendor the working tree, install it as a local path
(`sdk-target.dang:182-197,299-301`), scaffold a module with it, then run
`dagger generate` (`checks-generate.dang:8-12`) and `dagger api functions`
(`checks-module.dang:15-18`) through a **released** CLI
(`sdkSdk.daggerCliVersion = "1.0.0-beta.9"`). The SDK is local; the runtime ref
the scaffolded module records is not — it resolves from this repository's
`main` via `git.head`.

So flipping `targetRuntime` in the same change that introduces `runtime/` would
point CI at a path that does not exist yet. Hence:

- **PR 1 (this workstream)** — add `runtime/` (with `runtime/sdk/`), widen
  `tomlConfigPattern`, and prove the runtime end to end against an in-repo
  fixture that references it by *local path*. `targetRuntime` stays `"python"`.
- **PR 2 (immediately after PR 1 merges)** — flip `targetRuntime`, update
  `e2e:target-runtime-check`. Green because `runtime/` is by then on `main`.

**PR 1 is preparatory: no user-created module reaches the new runtime until PR
2.** That is a real property of the split and worth stating plainly rather than
dressing up. What makes PR 1 worth landing on its own is that the fixture
exercises the new runtime in CI on every subsequent commit — including PR 2,
which otherwise could not test the runtime it switches to, since
`sdk-sdk:module:loads` resolves the ref from `main` forever, not just once.

## Residual engine work (out of scope, report only)

Restoring a literal `runtime = "python"` for modern modules needs a
`dagger/dagger` change. An earlier draft called it "one line"; it is three
coordinated edits plus a policy decision:

1. `core/sdk/loader.go` — move `sdkPython` out of `loadBuiltinSDK` into the
   ref-resolving branch used by `sdkJava`/`sdkPHP`/`sdkElixir`.
2. `core/sdk/workspace_module.go:44-46` — repoint the table entry from
   `github.com/dagger/python-sdk` (this repo's *authoring* module, which
   implements no `moduleRuntime`) to `github.com/dagger/python-sdk/runtime`.
   Java only works because `sdk/java/dagger.json` sets `"source": "runtime"`.
3. `core/sdk/loader.go:276-283` — decide python's versioning. `parseSDKName`
   currently rejects `python@<version>` and assigns no default, whereas
   java/php/elixir default to `engine.Tag` with the commit fallback at
   `:160-189`. This inherits dagger/dagger#13755.

It would also route every legacy `dagger.json` module here, which this runtime
deliberately does not serve. Separate, explicitly-scoped decision — reported to
the Chief of Staff, not folded in.

## Affected components (PR 1)

- `future/done/self-contained-python-sdk.md` (this doc)
- `runtime/**` (new) — the simplified module runtime
- `runtime/sdk/**` (new) — vendored client library + code generator
- `python-sdk.dang` — `tomlConfigPattern` widened
- `dagger.json` — `include` list, so the authoring module's source does not
  grow by ~35k lines of vendored + generated code
- `.dagger/modules/e2e/main.dang` + `fixtures/runtime/**` — runtime e2e
- `README.md` — document the two paths

`targetRuntime` is PR 2's change.

## Testing

**Runtime execution, via the sdk-sdk harness.** The existing e2e checks are
pure-Dang workspace assertions; Dang has no dynamic function invocation and no
way to catch a failed call, so "assert the call returns X" and "assert this
error text" are not expressible there. They *are* expressible through
`sdkSdk.target(view, sourceRootPath).run([...])` →
`SdkRun.assertSuccess`/`assertFailure`/`stderr` (`sdk-run.dang:25-62`), already
installed in `dagger.toml`. New checks:

- `runtimeCall` — `dagger call` the fixture, assert the returned value. The
  fixture is addressed as a one-off module in a single command:
  `run(["call", "-m", "<fixture path>", "<fn>"])`; `-m/--load-module` accepts a
  local path at beta.9 (`internal/cmd/dagger/module.go:40`), and the fixture's
  six-`..` runtime path stays inside the harness's `git init`ed `/work`.
- `runtimeRequiresGeneratedFiles` — same fixture with its committed bindings
  removed must `assertFailure` with the "run `dagger generate` and commit"
  message, proving codegen really is gone from the runtime path rather than
  silently regenerating.

**Fixture.** `.dagger/modules/e2e/fixtures/runtime/app/` with
`[runtime] source = "../../../../../../runtime"` — six `..`, not five: the
fixture is six segments deep (`.dagger/modules/e2e/fixtures/runtime/app`). A
relative local path is a legal runtime source (`ResolveDepToSource`,
`core/modulesource.go:2016-2023`; the engine itself writes relative local refs
at `workspace_sdk.go:240-250`; dagger/dagger's own elixir testdata does it).
The fixture must commit the whole vendored `sdk/` because `requireGeneratedFiles`
demands `sdk/pyproject.toml` and `sdk/src/dagger/client/gen.py`
(`main.go:244-267`) — generated by running `initModule` + `dagger generate`, not
hand-written. It gets **no** skip-generate marker, since one of the checks
generates it (an earlier draft asked for both, which cancel out).

**Legacy path regression net.** `e2e:generate-check` and
`e2e:generate-all-check` run against the existing `fixtures/generate/app`
(`dagger.json`, `sdk.source: "python"`), still served by the engine builtin.
Captured before the change and re-run after.

**Codegen fidelity.** The earlier plan proposed byte-comparing the modern
fixture's `gen.py` against the legacy fixture's. That is invalid: output depends
on the module's dependency set via `SchemaIntrospectionJSONFileForModule`, and
`fixtures/generate/app` pins `engineVersion = "v0.20.8"`, which deliberately
selects a different codegen shape (`codegen/src/codegen/generator.py:368-374`
enables the legacy ID facade below v0.21). Instead: assert `runtime/sdk/codegen`
is tree-identical to `dagger/dagger@<pin>:sdk/python/codegen`, and carry over
`tests/codegen` + `tests/mod` from upstream (the only upstream tests covering
what this repo now owns) with a `dagger check` that runs them.

**Go unit tests.** `runtime/python_test.go` is carried over and trimmed; a
check runs `go test ./...` in `runtime/`, otherwise it is untested tree weight.

## Risks

- **Contextual-argument resolution.** Mitigated by design (see approach §2) but
  worth re-verifying empirically at implementation time: load the fixture
  through `dagger generate` from a scratch workspace and assert the vendored
  `gen.py` is correct, not silently sourced from the caller's workspace.
- **Unpinned runtime ref.** `targetRuntime` is written with no `@version` and
  no pin (`workspace_module_init.go:347-355`; `sourceWithPin` is bypassed on
  this branch), so after PR 2 every modern Python module resolves this repo's
  default branch through `git.head` (`core/modulerefs.go:180-203`). The
  runtime↔engine version coupling the builtin provided is gone; a module's
  committed vendored SDK can drift from the runtime that loads it. Consuming
  workspaces get a floating `dagger.lock` entry as partial mitigation.
  java-sdk has the same exposure — precedent, not correctness.
- **Copy drift.** `runtime/sdk/` starts diverging from
  `dagger/dagger:sdk/python` immediately. Mitigated by the provenance note and
  the codegen tree-identity check; the divergence is the point, but the legacy
  path depends on the `dagger/dagger` copy until the engine change lands.
- **Vendored library identity.** Modules vendor a `dagger-io` that is no longer
  the PyPI-released one. The distribution name and version stay identical, and
  the template's `[tool.uv.sources]` maps it to the vendored path, so there is
  no new collision — but a divergent library under a released name is a real
  hazard once it diverges.
- **Repo weight.** `runtime/sdk/src/dagger/client/gen.py` (~16.7k lines) plus
  the runtime's committed Go bindings (~18.8k lines). Contained to `runtime/`
  and kept out of the authoring module by `dagger.json`'s `include`.
- **The fixture's vendored SDK can go stale silently.** It was generated once
  and committed; nothing compares it to `runtime/sdk`, so `runtime/sdk` can
  change and the fixture keeps passing on its old copy. Closing the codegen gap
  above (regenerating the fixture in CI) is what would fix this properly.
- **`tomlConfigPattern`'s "durable fix" conflicts with the fixture.** The
  comment in `python-sdk.dang` suggests validating against
  `currentModule.asSDK.modules`. The runtime fixture is deliberately *not* a
  managed module, so that change would make `mod()` reject it. Whoever picks it
  up has to register the fixture — which needs the polyfill gap closed first.
- **Vendored client and provisioning code arrives without its tests.**
  `tests/client` and `tests/provisioning` were not copied, so
  `e2e:sdk-test-check` covers the code generator and module registration but
  not the connection/session/provisioning code beneath them.
- **CI is Dagger Cloud checks, not GitHub Actions.** New coverage is
  `dagger check` functions, and a runtime e2e is meaningfully slower than the
  existing authoring checks.

## Alternatives considered

**Rewrite the runtime in Dang** (what `dagger/java-sdk` did). Attractive: the
rest of this repo is Dang, and java-sdk's runtime is 146 lines. But java-sdk's
runtime `codegen` is a deliberate no-op (`runtime/main.dang:51-53`) because its
*authoring* module owns generation (`mod.dang:66-72`), whereas this repo
delegates generation to the engine (`mod.dang:57-64`) — which is what forces
codegen into the runtime, and the runtime into a language that can drive it.
Rejected for PR 1 also because `discovery.go`'s package-name normalization and
Python-version selection are exactly where a re-implementation breaks modules
subtly. A Dang rewrite is a good follow-up once the copy is proven in CI, and
it would pair naturally with moving generation into the authoring module.

**Teach the engine to route `python` here.** The only way to keep the literal
name. See *Residual engine work*.

**Sniff for `dagger.json` inside the runtime.** Unnecessary: the engine already
makes that decision (`core/sdk/utils.go:20-25`).

**One PR with a knowingly-red `sdk-sdk:module:loads`.** See *Delivery
sequencing*.

## Implementation plan (PR 1)

StGit patch series. Each patch carries `Signed-off-by: Yves Brissaud
<yves@dagger.io>`.

1. **`future: design doc for a self-contained Python SDK`** *(done)*

2. **`runtime: add the Python module runtime`**
   Copy `dagger/dagger@501b57e04:sdk/python/runtime/**` → `runtime/**`
   verbatim, then the minimum needed to make it live here:
   - rename the module to `python-sdk-runtime` (`dagger.json`, `go.mod`, the
     Go type, imports);
   - keep the legacy `dagger.json` with `sdk.source: "go"`, so the Go SDK
     regenerates `internal/dagger` at load rather than committing bindings
     generated against a different engine.
   Verbatim otherwise, so patch 4's diff *is* the simplification.

3. **`runtime: vendor the Python client library and code generator`**
   Copy `sdk/python/{pyproject.toml,uv.lock,ruff.toml,LICENSE,README.md,
   .python-version,.gitattributes,.gitignore,src/**,codegen/**,tests/conftest.py,
   tests/codegen/**,tests/mod/**}` → `runtime/sdk/` (`tests/conftest.py` is not
   optional: it holds the only `anyio_backend` fixture, without which the ported
   `tests/mod` cases error out). Provenance note naming the source commit. Trim
   `pyproject.toml`'s `testpaths` / `source-include` to the trees actually
   carried over. Rewire `New()`: `sdkSourceDir` becomes `+optional`, defaulting
   to `dag.CurrentModule().Source().Directory("sdk")` with upstream's `+ignore`
   allowlist re-applied via `Directory.Filter`; drop `+defaultPath`. Add an
   `include` list to `runtime/dagger.json`.

4. **`runtime: build modern modules from committed files only`**
   The simplification, exactly as scoped in *What actually gets simplified* —
   including the two corrections (keep `IsInit` / `MainObjectName`; make
   `TrustedSource`'s two behaviours unconditional). Trim `python_test.go`.

5. **`python-sdk: accept a module runtime other than the python builtin`**
   Widen `tomlConfigPattern` so `mod()` validates modules whose `[runtime]
   source` is `python`, this repo's runtime ref, or a local path.

6. **`dagger: keep vendored code out of the authoring module's source`**
   Add an `include` list to the root `dagger.json`.

7. **`e2e: run a module through the new runtime`**
   Fixture + `runtimeCall` / `runtimeRequiresGeneratedFiles` checks via the
   sdk-sdk harness; a check running `go test ./...` in `runtime/`; a check
   asserting `runtime/sdk/codegen` matches the pinned upstream tree.

8. **`docs: describe the legacy and modern runtime paths`**
   `README.md`: what lives where, which modules use which path, why
   `targetRuntime` is still `"python"` today and what PR 2 changes.

### Test strategy

- `dagger check -l`, then targeted `dagger call e-2-e <check>` for each new
  check against the dev CLI (`v1.0.0-beta.9`).
- Full `dagger check` before handoff, confirming the existing 35 checks are
  untouched — `e-2-e:generate-check`, `e-2-e:generate-all-check` and
  `sdk-sdk:module:loads` are the legacy path's regression net.

## What implementation changed about the plan

Three things only survived contact with a real engine in modified form.

**1. A local-path runtime source resolves on load, but not through generate.**
`dagger call -m <fixture>` loads the fixture from the workspace (a *local*
module source, context = repo root) and `../../../../../../runtime` resolves —
verified, the module builds and returns its value. `dagger generate` on the same
module fails with `invalid SDK`.

The cause is in the polyfill, not the engine. An earlier draft of this section
blamed `ResolveDepToSource`'s dir branch; that was wrong. `Workspace.moduleSource`
materializes the whole workspace tree
(`core/schema/workspace_module.go:140-159`), so `runtime/` *is* reachable. What
drops it is `dagger/polyfill`'s generate helper: at the pinned commit
(`16627066`) it builds a filtered view,
`workspace.Directory("/", Include: include).AsModuleSource(...)`
(`helpers/workspace-module-generate/main.go:213-220`), where `include` is
derived by `parseSourceConfigTOML` from `dependencies[].source` and `include`
only (`main.go:449-472`) — it never reads `[runtime] source`. A local-path
runtime is therefore filtered out of the view the module is generated from.

Consequences, all confined to PR 1:

- The fixture is **not** registered under `[[modules.python-sdk.as-sdk.modules]]`,
  because `generateAll` would fail on it.
- Its vendored `sdk/` was generated once through the engine builtin and
  committed, then verified by loading the module through *this* runtime.
- So PR 1 exercises this runtime's **module-load** path for real, but not its
  **codegen** path.

This is a gap, not a law: adding `[runtime] source` to the include set that
polyfill's helper computes would close it, and is worth raising against
`dagger/polyfill`. It also disappears on its own in PR 2, where modules
reference the runtime by git ref rather than by path.

**2. Codegen fidelity is checked by running tests, not by comparing trees.**
The plan called for asserting `runtime/sdk/codegen` is tree-identical to
upstream. That contradicts the design — divergence is the point of moving the
code here — and would have to be edited away on the first intentional change.
Replaced by running the vendored library's own suites (`tests/codegen`,
`tests/mod`, 169 tests) as `e2e:sdk-test-check`.

**3. The runtime's Go tests need a Dagger session, and one of them was wrong.**
The generated client's `init()` panics without `DAGGER_SESSION_PORT`, so the
check runs `go test` with `experimentalPrivilegedNesting`. With the tests
actually running, `TestPackageNameNormalization` failed — and it fails upstream
too: it is byte-identical to `dagger/dagger`'s and feeds raw module names to
`NormalizePackageName`, which documents that it takes an already-normalized
project name and only maps `-` to `_`. Corrected to test the documented
contract plus the composed pipeline discovery actually uses. Production
behaviour is untouched; `discovery.go` already passes it a normalized name.

## Follow-up: generation moved out of the runtime

Landed after the first review round, on Yves's call, before merge.

The runtime module implemented `codegen` because this SDK's `@generate` hook
did not generate anything itself: `generateAll` handed each module back to the
engine (`polyfill … moduleSource(path).generate` →
`GeneratedContextChangeset`), and the engine dispatches `codegen` to whatever
the module's `[runtime] source` names. So `dagger generate` on a Python module
ran the *engine's builtin* generator, and the code generator vendored here was
never reached — embedding it bought nothing.

Now `generateAll`/`Mod.generate` generate directly for `dagger-module.toml`
modules: take the module's dependency schema, run `sdk/`'s code generator
against it, vendor the result. `dagger.json` modules keep going through the
engine, so the pre-1.0 path is untouched.

Two things this depended on:

- The schema must come from `ModuleSource.introspectionSchemaJSON`
  (`core/schema/modulesource.go:257`), which loads only the *dependency*
  modules. `Module.introspectionSchemaJSON` goes through `asModule`, which
  builds the module's runtime — impossible before its bindings exist.
- A public Dang function cannot return a dependency's type, so the shared
  fork helper stays private and `generateAll` merges `Changeset`s instead.

Consequences: the runtime's `codegen` is a no-op (kept, because the engine
reads its presence as the SDK's code-generator capability), and everything that
served it is gone — SDK vendoring, `Common`/`WithSDK`/`WithUpdates`,
`SdkSourceDir` and its `dist/` probing, and `TrustedSource`, which now only ever
had one value. The client library moved from `runtime/sdk/` to `sdk/`, since the
authoring module is now its consumer and the runtime does not need it at all.

This also closes the codegen coverage gap recorded above: `e2e:toml-generate-check`
generates a `dagger-module.toml` module and asserts the result came from this
SDK rather than the builtin, which vendors its whole `sdk/python` tree.

## Progress

- Phase 0 — orientation: done.
  - Worktree: `…/python-sdk-runtime-consolidation-lead-ea131db2-e2b213c6`
  - Branch `python-sdk-runtime-consolidation-lead-ea131db2`, base `main`,
    remotes `origin=eunomie/python-sdk`, `upstream=dagger/python-sdk`.
  - Design-doc home: `future/` (created; repo had none, and `future/` is the
    convention in `dagger/dagger` and `dagger/go-sdk`).
  - VCS: StGit patch stack. Sign-off: `Signed-off-by: Yves Brissaud
    <yves@dagger.io>`. No AI attribution anywhere.
  - Host: GitHub. CI: Dagger Cloud checks driven by `dagger.toml` (no
    `.github/`).
- Phase 1/2 — feature doc + implementation plan: this document.
- Phases 6–8 — draft PR https://github.com/dagger/python-sdk/pull/17 at
  `49cb500`, **39/39 CI checks green**, doc archived here. PR 2 (the
  `targetRuntime` flip plus its check) is the remaining work, and is unblocked
  now that `runtime/` is on the default branch.
- Phase 5 — code review: **passed**. Two independent reviewers on the diff, no
  blockers. Fixes applied: the deviation-1 diagnosis was wrong and is corrected
  above (polyfill's generate helper, not an engine limit — and therefore
  fixable); `Codegen` now fails with an actionable error instead of letting
  `uv lock` fail on a module with nothing to generate from; the missing-files
  check no longer claims to be a behaviour difference from the builtin; the
  runtime checks use the harness's `runInstalled` rather than paying for an
  unrelated scaffold; a stale `WithoutDirectory("sdk/runtime")` that could have
  deleted a user's directory is gone; vendored-tests and fixture-drift coverage
  gaps are recorded in Risks; the two test checks moved out of the docs patch.
- Phase 4 — implemented. Seven patches; all 38 `dagger check` checks green
  locally, including the two new runtime checks, the two new test checks, and
  the legacy regression net (`e-2-e:generate-check`, `e-2-e:generate-all-check`,
  `sdk-sdk:module:loads`). See *What implementation changed about the plan*.
- Phase 3 — adversarial plan review: **passed** after two rounds.
  Round 2 independently verified the three load-bearing corrections
  (`CurrentModule().Source()` is immune to the workspace redirect —
  `core/schema/module.go:2951-3003`; `sdkSourceDir` really does arrive nil for a
  ref-loaded SDK; `tomlConfigPattern` is the only behavioural hard-code of
  `"python"`), and added four items now folded in: re-apply the `+ignore`
  allowlist lost with `+defaultPath`, copy `tests/conftest.py`, address the
  fixture with `dagger call -m <path>`, and widen `tomlConfigPattern` narrowly
  rather than to any local path.
  Round 1 detail: a design/spec reviewer and a skeptic reviewed
  independently. Both confirmed the central claims (the `python` name
  cannot route here; relative local paths are legal runtime sources; the
  two-PR constraint is real). Revisions folded in: the `+defaultPath` layout
  was unsafe and became `runtime/sdk/` + `dag.CurrentModule().Source()`;
  `tomlConfigPattern` is a blocking omission and moved into PR 1; `IsInit` /
  `MainObjectName` / `TrustedSource` are load-bearing and stay; the engine
  change is three edits, not one; the byte-identical codegen test was invalid
  and was replaced; module rename, `dagger.json` include, unpinned-ref risk,
  and the fixture path count all corrected.
