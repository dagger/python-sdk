# Python SDK module performance

Status: in progress
Date: 2026-08-14

## Problem

`dagger/python-sdk` is the SDK-authoring module for Python Dagger modules: it
owns `initModule`, `generateAll`, module discovery, and per-module
configuration. Every Python module author pays its cost on `dagger module init
python`, on `dagger generate`, and — because the SDK is an installed workspace
module — indirectly on workspace load during `dagger call`.

Nobody had measured where that time actually goes against current `main`. This
doc records a trace-driven baseline and the improvements that follow from it.

## Baseline (measured, not assumed)

All numbers from a local engine `v1.0.0-beta.9` (`registry.dagger.io/engine`),
captured with `dagger --progress=plain` and replayed with `dagger trace`. The
host is shared, so wall times carry ~±0.5s of noise; span durations inside a
run are the reliable signal. Workspace: a scratch workspace with the SDK
checkout vendored at `sdk/python-sdk`, one generated Python module at
`.dagger/modules/app` (the `default` template, `dagger-module.toml` format).

### Calling a Python module — `dagger call app container`

Warm, n=4: **3.45s – 4.21s** wall.

| phase | span | cost |
| --- | --- | --- |
| CLI ↔ engine handshake | `connect` | 0.2s |
| workspace load | `loading type definitions` | 1.4 – 1.7s |
| ⤷ of which | `load module: app` → `asModule getModDef` → `exec.processRun` | **1.1s** |
| function call | `app(...)` → `exec.processRun` | **1.0 – 1.1s** |
| result | `App.container` | CACHED, 0.0s |

**≈2.1s of a ≈3.4s warm call — about 60% — is two Python interpreter boots
inside the module runtime container**: one to emit typedefs during workspace
load, one to run the function. `App.container` itself is a cache hit; the
module's own logic costs nothing measurable.

The no-codegen-at-runtime architecture (dagger/dagger#13593) is confirmed live:
the generated module carries committed `sdk/` sources and a
`dagger-module.toml` with `[runtime] source = "python"`, and no codegen runs on
the call path.

**That 2.1s is not addressable from this repository.** The Python module
runtime lives in `dagger/dagger` under `sdk/python/runtime` (plus the
`dagger-io` package under `sdk/python/src/dagger` that codegen vendors into
each module). This repo does not choose the base image, the interpreter, the
install strategy, or the runtime entrypoint. See "Out of scope / handed
upstream" below.

One per-call cost *is* ours: loading the `python-sdk` module during workspace
load runs

```
git ls-remote --symref https://github.com/dagger/polyfill   [0.4s, cache_hit=false]
```

on every single call, even though `dagger.json` pins the dependency by commit
(`github.com/dagger/polyfill@main` + `pin`). It resolves off the critical path
(concurrent with `load module: app`, so removing it did not move wall time in
an A/B), but it is a per-call network round-trip that makes every Python module
call depend on GitHub reachability.

### Initializing a module — `dagger module init python <name>`

| cache state | wall | `Workspace.withInitModule` |
| --- | --- | --- |
| cold engine (image pull + `go build`) | **11.1s** | — |
| `golang:1.25-alpine` present, Go build cache warm, helper source changed | **7.4s** | 5.2s |
| fully warm | 3.8s | 1.8s |

`PythonSdk.renderedTemplate` renders the starter template by spinning up a
`golang:1.25-alpine` container, `go build`-ing `helpers/render-template` from
source, and running it. `configuredTemplate` does the same a second time with
`helpers/pyproject` whenever any of `--python-version` / `--use-uv` /
`--base-image` is non-default.

The work being done is trivial: four `text/template` substitutions
(`.ModuleName`, `.ModuleType`, `.ModuleImport`, `.ModulePackage`), a `.tmpl`
suffix strip, and path templating, over a 2–5 file tree.

CI pays this cold every run. From PR #15's checks: `e-2-e:init-check` 39.3s and
`e-2-e:init-config-check` 38.9s, versus 17–19s for the checks that touch no
container (`module-lookup-check` 17.5s, `skip-generate-check` 18.3s,
`target-runtime-check` 18.0s). ~20s per init check is the Go toolchain.

### Generating — `dagger generate`

| cache state | wall | `PythonSdk.generateAll` |
| --- | --- | --- |
| cold | 12.2s | 5.3s |
| warm, 2 modules | 4.9 – 6.5s | 2.6s |

`generateAll` folds over discovered modules with `reduce`, and each step stages
the module's local dependency closure and then generates it. Both the polyfill
`PolyfillModuleSource.core` container (0.7s warm, per module) and the codegen
run are therefore serialized: the cost grows linearly with the number of Python
modules in the workspace, with no overlap.

## Goals

1. Remove the Go toolchain from the `init` path so a first-ever
   `dagger module init python` does not pull `golang:1.25-alpine` and compile a
   helper. Target: cold init dominated by workspace I/O, not by a build.
2. Stop the per-call `git ls-remote` against `github.com/dagger/polyfill`.
3. Re-measure everything with fresh traces and report honest before/after.

## Non-goals (YAGNI)

- Reintroducing runtime codegen, or anything that touches the
  no-codegen-at-runtime design. It shipped, it works, it is confirmed live.
- Fixing the 2.1s Python interpreter boot. Not in this repository — written up
  and handed upstream instead of half-solved here.
- Rewriting `helpers/pyproject` (TOML read/modify/write). It only runs when a
  non-default `init` flag is passed, its Go implementation is unit-tested, and
  a Dang reimplementation would have to re-marshal TOML. Left alone
  deliberately; see "Alternatives".
- Parallelizing `generateAll`. Dang's `map`/`reduce` are sequential in the
  interpreter, and the fold's `fork.merge` chain is a genuine data dependency.
  There is no cheap primitive to exploit here today; recorded as a finding
  rather than guessed at.

## Approach

### 1. Render templates in Dang, delete the Go helper from the init path

Replace `renderedTemplate`'s container with pure in-engine evaluation:

- `currentModule.source.directory("templates/" + name).glob("**")` to walk the
  template tree.
- For each file: read `.contents`, expand `{{ .Key }}` placeholders, strip a
  trailing `.tmpl`, expand placeholders in the destination path too, and
  `withNewFile` it onto an empty `directory`.
- Placeholder expansion splits on `{{` / `}}` so any interior spacing works,
  and raises on an unknown key rather than silently emitting `<no value>`.
- The four template variables need `strcase`-equivalent conversions. Implement
  word-splitting (on `-`, `_`, space, and case transitions) once, then
  `ModuleType` = words capitalized and joined, `ModulePackage` = lowercase
  joined by `_`, `ModuleImport` = `"dagger/"` + lowercase joined by `-`.

`helpers/render-template` is deleted along with its `go.mod`/`go.sum`.

### 2. Pin the polyfill dependency by commit

Change the `dagger.json` dependency source from `github.com/dagger/polyfill@main`
to the same commit the `pin` field already records, so ref resolution has
nothing to look up remotely. Verify from a trace that `git ls-remote` no longer
appears on the call path — and if the engine issues it regardless of how the
ref is written, say so and report it upstream rather than shipping a no-op.

### 3. Out of scope / handed upstream

The call-path finding (2.1s of interpreter boot, split across a typedef exec
and a function exec) belongs to `dagger/dagger`. It is documented here with
trace evidence so it can be raised there; this PR does not attempt it.

## Alternatives considered

- **Keep the Go helper, cache the built binary.** The `go build` layer is
  already cached by the engine — that is exactly why warm init is 1.8s. It does
  nothing for the cold/CI case, which is the case that hurts.
- **Ship a prebuilt helper binary.** Needs release infrastructure this repo
  does not have, and puts a binary in the tree.
- **Replace `golang:1.25-alpine` with a smaller image running `sed`.** Cheaper
  than Go but still a container pull and two execs on the init path, and shell
  quoting for template substitution is worse than doing it in Dang.
- **Reimplement `helpers/pyproject` in Dang too.** Rejected: TOML round-tripping
  in Dang would be a large, risky change on a path that only fires for
  non-default init flags.

## Affected components

- `python-sdk.dang` — `renderedTemplate`, plus new private string helpers.
- `helpers/render-template/` — deleted.
- `dagger.json` — polyfill dependency ref.
- `.dagger/modules/e2e/main.dang` — coverage for the rendering behavior that
  moves from Go to Dang.

## Testing

The existing e2e checks already pin the important behavior: `init-check` and
`template-check` assert the rendered class name, package directory, and that no
`{{` survives; `init-config-check` covers the flag path. Those must stay green
unchanged — they are the regression net for the rewrite.

Add explicit coverage for name conversion, since that is the part with real
behavior-drift risk: a kebab-case name, a snake_case name, and a camelCase
name, each asserting the rendered class name and the `src/<package>/` path.

## Risks

- **Name-conversion drift.** `iancoleman/strcase` has behavior for digits and
  runs of capitals (`HTTPServer` → `http_server`) that a straightforward Dang
  implementation gets wrong. Mitigated by the new tests over realistic module
  names; accepted for exotic ones. Called out explicitly rather than papered
  over.
- **`glob("**")` semantics.** Directory entries and whether hidden files
  (`templates/legacy/.gitignore`, `.gitattributes`) are returned must be
  verified empirically, not assumed.
- **Pinning polyfill by commit** loses the `@main` signal about intent. The
  `pin` field already made it commit-exact, so this is a notation change, but a
  reviewer may prefer the branch ref for readability.

## Implementation plan

Plain git commits (no `stg` patch stack in this repo), each with
`Signed-off-by: Yves Brissaud <yves@dagger.io>`, no AI attribution.

1. **`perf: render init templates without a Go toolchain`**
   - `python-sdk.dang`: add private `splitWords` / `camelName` / `snakeName` /
     `kebabName` / `expandTemplate` / `renderPath` helpers; rewrite
     `renderedTemplate` to use them.
   - Delete `helpers/render-template/`.
2. **`test(e2e): cover module name conversion in template rendering`**
   - `.dagger/modules/e2e/main.dang`: a check rendering kebab/snake/camel names
     and asserting class name + package path.
3. **`perf: pin the polyfill dependency by commit`**
   - `dagger.json` only. Dropped if the trace shows no change.

Each step is verified by running the e2e checks and re-measuring init with a
fresh trace before moving on.

## Progress

- Phase 0 (orient) — done.
  - Worktree branch was 36 commits behind; reset to `upstream/main` `6dca4e9`.
  - Design-doc home: no `future/`, no `hack/designs/`, no `design/` existed →
    created `hack/designs/`.
  - VCS: plain git. Host: GitHub (`upstream` = dagger/python-sdk, `origin` =
    eunomie/python-sdk). CI: Dagger Cloud checks (`dagger-dogfood`), driven by
    `@check` functions in `.dagger/modules/e2e`; no GitHub Actions workflows.
  - Sign-off trailer: `Signed-off-by:` (present on most commits).
- Phase 1 (feature doc) — this document.
- Phase 2 (implementation plan) — above.
