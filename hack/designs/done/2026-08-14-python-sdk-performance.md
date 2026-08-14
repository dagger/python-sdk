# Python SDK module performance

Status: done
Date: 2026-08-14

## Problem

`dagger/python-sdk` is the SDK-authoring module for Python Dagger modules: it
owns `initModule`, `generateAll`, module discovery, and per-module
configuration. Every Python module author pays its cost on `dagger module init
python`, on `dagger generate`, and — because the SDK is an installed workspace
module — indirectly on workspace load during `dagger call`.

Nobody had measured where that time actually goes against current `main`. This
doc records a trace-driven baseline and the improvements that follow from it.

## How these numbers were taken

Local engine `registry.dagger.io/engine:v1.0.0-beta.9`, CLI v1.0.0-beta.9,
captured with `dagger --progress=plain` (and `-d` for child spans), replayed
with `dagger trace <id>`.

The host is shared with other work and is noisy: wall-clock runs vary by
±0.5s independent of anything measured here. Every warm A/B below therefore
**interleaves arms** (A,B,A,B) rather than batching them, and reports every
individual run. The one exception is the cold-`init` A/B, which was run
3-then-3: each of its runs gets a throwaway engine of its own, so there is no
shared warm cache for run order to bias. Where a claim rests on a cold cache,
the run used a throwaway engine container and volume (`docker run --rm --privileged
registry.dagger.io/engine:v1.0.0-beta.9` + a fresh `/var/lib/dagger` volume,
selected with `_EXPERIMENTAL_DAGGER_RUNNER_HOST`) so the shared engine's cache
was neither used nor disturbed.

Workspace under test: a scratch workspace with the SDK checkout vendored at
`sdk/python-sdk`, `[modules.python-sdk.as-sdk] name = "python"`, and a module
created by `dagger module init python app` from the `default` template.

Representative traces: `11bf8510513828aad96fb4bb46429b9f` (warm call),
`7201b38b2f0b534a77979fffddad4492` (first call after generate),
`f43f7c87be6469aabaecda4ba32ec7a0`, `5fc1fdb935b66408660e895505f7f631`.

## Baseline

### Calling a Python module — `dagger call app container`

Warm runs: 3.34, 3.35, 3.42, 3.45, 3.56, 3.75, 4.21 s wall (median 3.45).

| phase | span | cost |
| --- | --- | --- |
| CLI ↔ engine handshake | `connect` | 0.2s |
| workspace load | `loading type definitions` | 1.4 – 1.7s |
| ⤷ of which | `load module: app` → `asModule getModDef` → `exec.processRun` | **1.1s** |
| function call | `app(...)` → `exec.processRun` | **1.0 – 1.1s** |
| result | `App.container` | CACHED, 0.0s |

Two `exec.processRun` spans in the module's Python runtime container account
for roughly 2.1s. `App.container` itself is a cache hit, so the module's own
logic costs nothing measurable.

An `exec.processRun` span covers container start, interpreter initialisation,
imports, the user module's import, and typedef registration or dispatch, so
"interpreter boot" would be the wrong label. Instrumenting inside the real
runtime container (`/proc/self/stat` start time, `-X importtime`, and staged
subprocesses) splits one ~1.05s processRun as:

| component | cost |
| --- | --- |
| `from dagger.mod.cli import app` — whole import chain | 390 – 453 ms |
| ⤷ of which `dagger.client.gen` | 300 ms |
| `dagger.connect()`, *first* connection in a fresh container | 253 ms |
| container setup, result publish, teardown (outside Python) | 160 – 260 ms |
| recompiling the vendored SDK to bytecode — **every call** | ~60 ms |
| `telemetry.initialize()` | 38 ms |
| entry-point scan, user module import, `FunctionCall` name/parent | ~7 ms |
| **interpreter start** (`python -c pass`) | **~8 ms** |

Interpreter boot is **0.8%** of it. The bytecode line is its own small scandal:
the editable install points `dagger` at `/src/<digest>/sdk/src`, so
`__pycache__` lands in the throwaway container layer and is discarded — the
generated `gen.py` is recompiled on every processRun, ~120 ms per call across
the two.

All of that lives in `dagger/dagger`'s `sdk/python/runtime` and the `dagger-io`
package it vendors. The three real cost centres to hand upstream are the
253 ms first-connect handshake, the ~450 ms import chain (300 ms of it
generated client code), and the ~120 ms/call of thrown-away bytecode.

The no-codegen-at-runtime architecture (dagger/dagger#13593) is confirmed live:
the generated module carries committed `sdk/` sources and a
`dagger-module.toml` with `[runtime] source = "python"`, and no codegen runs on
the call path.

#### Does this repo have a lever on that 2.1s?

It has three, because `discovery.go` turns the module pyproject.toml's
`requires-python` into a `python:<version>-slim` base image, `[tool.dagger]
base-image` overrides the image outright, and `[tool.dagger] use-uv` selects
the install path — and this repo owns both the default template that seeds
those values and the `mod-config.dang` commands that edit them. So they were
measured: 7 runs per arm, two discarded warm-ups, arms interleaved, and the
whole thing repeated as an independent second batch ~40 minutes later. Medians,
wall clock:

| arm | batch 1 | batch 2 | Δ vs default |
| --- | --- | --- | --- |
| `requires-python = ">=3.14"` (template default) | 3460 ms | 3371 ms | — |
| + `from __future__ import annotations` | 3447 ms | 3381 ms | −13 / +10 ms |
| `>=3.13` | 3262 ms | — | −198 ms |
| `>=3.12` | 3250 ms | 3269 ms | −210 / −102 ms |
| `base-image = "python:3.14-alpine"` | 4253 ms | 4271 ms | **+793 / +900 ms** |
| `use-uv = false` | 3050 ms | 2968 ms | **−410 / −403 ms** |
| committed `uv.lock` (`uv sync` path) | 3364 ms | — | −96 ms |
| `>=3.12` + `use-uv = false` | — | 2859 ms | −512 ms |

In-process instrumentation agrees: the per-processRun deltas are −200 ms for
pip, +390 ms for alpine, −60/−80 ms for 3.13/3.12, and wall Δ ≈ 2 × per-process
Δ throughout, which locates every one of these inside the two processRun spans.

Conclusions:

- **`python:3.14-alpine` is the worst thing a user can set**: +24% per call,
  because musl CPython walks the same import chain in 607 ms instead of 453 ms.
  `mod-config.dang` offers `base-image` with no guidance; worth a doc warning,
  not a code change.
- **`use-uv = false` is a real −12%**, reproduced across both batches. It is
  *not* being adopted: every in-container micro-benchmark (interpreter start,
  `import dagger`, site-packages size, bytecode recompile cost, `.pyc` rewrite
  count) is identical between the two arms, so the 200 ms/process lives in
  first-touch cost of the freshly-mounted rootfs and could not be isolated.
  Flipping a default on an unexplained 200 ms — and trading away uv's install
  speed on the build path, which was not benchmarked — is a bad deal. Recorded
  for the runtime owners instead.
- **Lowering `requires-python` buys −0.1 to −0.2s** and loses the digest pin on
  the base image. Not worth a version regression.

The measurement did surface one thing worth shipping, though it is a
correctness bug rather than a performance win: arms below 3.14 could not run at
all until the template gained `from __future__ import annotations`. See
"Changes made".

One per-call cost is unambiguously ours: loading the `python-sdk` module during
workspace load runs

```
git ls-remote --symref https://github.com/dagger/polyfill   [0.4s, cache_hit=false]
```

on every single call. It resolves off the critical path — an A/B removing
`python-sdk` from the workspace entirely gave 3.96, 4.15, 4.29, 4.30 s against a
3.45 – 4.21 s baseline, i.e. no improvement, because it overlaps `load module:
app` — but it is a per-call network round-trip that makes every Python module
call depend on GitHub reachability.

### Initializing a module — `dagger module init python <name>`

Cold engine (fresh container + volume per run), `Workspace.withInitModule`:
12.4, 12.0, 12.7 s. Wall: 17.53, 15.24, 15.68 s.

Warm engine, first init in a fresh workspace, unique module names: 1.8, 1.8,
2.0, 2.0 s.

`PythonSdk.renderedTemplate` rendered the starter template by starting a
`golang:1.25-alpine` container, `go build`-ing `helpers/render-template` from
source, and running it. The work being done is trivial: three `text/template`
substitutions (`.ModuleName`, `.ModuleType`, `.ModulePackage`; `.ModuleImport`
existed but no Python template used it), a `.tmpl` suffix strip, and path
templating, over a 2–5 file tree.

The cold trace shows the real shape: the Go arm pulls **two** Go base images —
`golang:1.25-alpine` (1.3s) for our helper and `golang:1.26-alpine` (1.7s) for
the polyfill dependency's own Go runtime — and carries an extra 2.9s
`exec.processRun`.

`helpers/pyproject` is a second Go helper on the same image. It is **not**
limited to non-default init flags: `mod-config.dang` builds the same container
for `config get` (three execs) and `config set` (up to four), both
README-documented commands.

### Generating — `dagger generate`

Cold: 12.2s wall, `PythonSdk.generateAll` span 5.3s.
Warm, 2 modules: 6.17, 6.46, 4.85 s wall, `generateAll` span 2.6s.

`generateAll` folds over discovered modules with `reduce`, and each step stages
the module's local dependency closure and then generates it. The polyfill
`PolyfillModuleSource.core` container appears once per module at 0.7s warm.

## Goals

1. Remove *this module's* Go toolchain from the default `init` path. The
   polyfill dependency still pulls a Go image on the same path; the goal is to
   stop adding a second Go build of our own, not to make `init` Go-free.
2. Stop a cold engine from pulling two different Go base images.
3. Establish, by measurement, whether any default this repo controls changes
   warm call time — and act on it if so.
4. Report honest before/after from fresh traces, including where the answer is
   "no measurable change".

## Non-goals (YAGNI)

- Reintroducing runtime codegen, or anything touching the no-codegen-at-runtime
  design. It shipped, it works, it is confirmed live.
- Rewriting `helpers/pyproject` in Dang. It round-trips TOML, it is unit-tested
  in Go, and a Dang reimplementation would have to re-marshal TOML faithfully.
  `config get`/`config set` and a flag-bearing `init` therefore keep the Go
  toolchain; on a cold engine that is a Go build, now at least on an image the
  workspace already pulls. A cheaper middle ground exists and is recorded for
  later: the three `config get` reads are pure reads, and `File.search` is
  already used in this repo, so reads could go native and leave only writes in
  Go.
- Parallelizing `generateAll`. Not attempted this round — deliberately, for
  budget, not because it is impossible. An earlier draft of this doc claimed
  Dang has no parallel primitive; that was wrong. `.{{ }}` selection dispatches
  through `evalParallel`, this repo already uses that syntax, and the fold's
  `stagedWs` closes over the outer `ws` rather than the accumulator, so only
  `fork.merge` genuinely chains. The engine also already parallelizes
  `generateLocalDependencies` internally (limit 8). Anyone picking this up
  should first measure 1/2/4/8 independent modules and check span overlap: the
  two-module sample here does not establish that the per-module cost is
  additive.

## What was rejected after review, and why

**Pinning the polyfill dependency by commit to remove the per-call
`git ls-remote`.** Planned, then dropped: it does not work. `git` ref
resolution always selects `_remoteGitMirror` and constructs
`RemoteGitRepository`, and `NewGitRepository` unconditionally calls
`backend.Remote()`, whose cache-miss path runs `ls-remote`
(`core/git_remote.go`). `ParsedGitRefString.GitRef` already recognises the
existing `refPin` as a SHA and passes it as `git(commit:)`
(`core/modulerefs.go`), so writing the SHA into the source string only changes
the named-ref selector — the remote metadata load happens regardless. The
correct fix is in the engine: skip remote metadata when an exact commit is
supplied. Reported upstream rather than shipped here as a no-op.

Two further facts turned up while checking this and are worth recording:
root `dagger.lock` holds `ec3ea84a2351b4beb06ecece951f2e5ef66509ff` for
polyfill marked `float`, which is a *different* commit from the `pin` in
`dagger.json` (`16627066…`); and `.dagger/lock` carries the same `float` shape
for `sdk-sdk`.

## Changes made

### 1. Render init templates in the engine (`ec61085`)

`renderedTemplate` now walks the template with `glob("**")`, expands
`{{ .Var }}` actions in both paths and contents, and carries non-`.tmpl` files
over with `withFile` so their bytes and mode survive untouched. Unknown
variables raise rather than rendering empty. `helpers/render-template` is
deleted.

`camelName` and `splitWords` reproduce the two *different* conversions the Go
helper took from `strcase`: `ToCamel` splits on separators only and lower-cases
a letter following another upper-case letter, while `ToSnake` splits camelCase
humps and keeps runs of capitals together. That is why `HTTPServer` yields type
`Httpserver` but package `http_server`.

**Equivalence evidence.** Rendered output was diffed against the Go helper's
for `my-module`, `my_module`, `myModule`, `HTTPServer` and `simple`, across the
`default`, `empty` and `legacy` templates — 15 combinations, **byte-identical,
same file modes**. Modes match because every non-template file in the tree is
0644: the Go helper wrote 0644 unconditionally, while `withFile` preserves the
source mode, so a template file with any other mode would differ. The
strcase-drift risk an earlier draft flagged does not materialise for these
names; for names carrying a digit it does, deliberately — see fix 4.

**Result — honest.** Cold engine `withInitModule` 12.4 / 12.0 / 12.7 s →
**11.2 / 11.3 / 11.1 s**: about **1.2s (~10%)**, plus one fewer image pull and
one fewer Go compile. Wall 17.5 / 15.2 / 15.7 → 14.4 / 16.5 / 14.2, which at
this host's noise level is consistent with the span delta and not much more.
Warm engine: 1.8/1.8/2.0/2.0 → 1.9/1.8/1.8/1.8, i.e. **no measurable
difference** — the `go build` layer was already cached, which is exactly why
the warm case never hurt.

The saving is smaller than the removed work suggests because the helper's
build overlapped the polyfill Go runtime build that remains. An earlier draft
of this doc predicted a much larger win from CI check durations
(`init-check` 39.3s vs `module-lookup-check` 17.5s); that reasoning was wrong,
because the Go layers are shared across checks on one engine and so are paid
roughly once per run, and because `config-check` (39.4s) still pays it via
`helpers/pyproject`. Expect little or no CI wall-clock change.

The durable wins are qualitative and worth having on their own: the default
scaffolding path no longer depends on a Go toolchain or on Docker Hub for a Go
image, and one of two helper binaries is gone.

### 2. One Go image instead of two (`a6efef5`)

`configuredTemplate` and `mod-config.dang` pinned `golang:1.25-alpine` while
polyfill's Go runtime pulls `golang:1.26-alpine`. Aligned to `1.26-alpine` so
the flag-bearing init and `config get`/`set` paths reuse an image the workspace
has already pulled. `helpers/pyproject` declares `go 1.25.0`, which 1.26 builds.

### 3. The default template works below Python 3.14 again (`60f469a`)

Found while benchmarking the `requires-python` arms. The default template's
`create()` returns the class it is declared in, and deferred annotations only
became the default in 3.14 (PEP 649). On anything older the forward reference
is evaluated eagerly and the module fails to load outright:

```
dagger module init python app --python-version 3.13
dagger call app container
ModuleLoadError: name 'App' is not defined
```

A documented init flag produced a module that could not be loaded at all;
`config set --python-version` reached the same state. Fixed by importing
annotations from `__future__`. Verified by initializing, generating and calling
a module at 3.12, 3.13 and the 3.14 default. The import is free — within noise
across two interleaved benchmark batches (−13 ms, +10 ms).

### 4. Module names containing a digit produce a loadable module (`ec61085`)

The second correctness bug the rewrite shipped, found while pinning the name
conversions. `strcase.ToSnake` treats a digit as a word boundary, so on `main`

```
dagger module init python s3-bucket
```

renders the package as `src/s_3_bucket/`, while `uv_build` derives `s3_bucket`
from `name = "s3-bucket"` in the pyproject.toml the same template wrote. The
package is therefore never importable and the module cannot be loaded at all:

```
failed to call module "s3-bucket" to get functions: call constructor: exit code: 1
```

The Dang `splitWords` breaks on separators and on camelCase humps only, not on
digits, so the same command now renders `src/s3_bucket/`; `dagger generate` and
`dagger call s-3-bucket container` both succeed. (`s-3-bucket` is the engine's
own kebab-casing of the module name for the CLI, unrelated to this change.)

Pinned by the `s3-bucket` row in `initNamingCheck`.

### 5. Tests (`3b7e5b8`)

The rendering assertions were all contains-only, so an extra, missing, or
misnamed output file passed. `initCheck` now asserts the exact file set for the
`default` and `legacy` templates — which also covers the legacy template's
`.gitignore` and `.gitattributes`, non-template files no check referenced
before — and a new `initNamingCheck` pins the type and package names for
kebab, snake, camel, plain, digit-bearing, and `HTTPServer` spellings.

## Affected components

- `python-sdk.dang` — `renderedTemplate` and its string helpers; the file-scope
  `goImage` binding both Go helper containers build on.
- `helpers/render-template/` — deleted.
- `mod-config.dang` — `goImage`.
- `.dagger/lock` — the pinned Go image.
- `.dagger/modules/e2e/main.dang` — `assertPaths`, `initCheck`,
  `initNamingCheck`, `templateCheck`.
- `templates/default/src/{{.ModulePackage}}/__init__.py.tmpl` — future import.

## Risks

- **`glob("**")` returns directories and dotfiles.** Both confirmed empirically
  (dotfiles are why the legacy template still works); directories are filtered
  by their trailing `/`. The trailing-slash marker is engine-version gated
  (`v0.17.0`+), well below this repo's `engineVersion`.
- **Template language capability.** The renderer replaced Go `text/template`
  with a `{{ .Var }}`-only substituter. `{{ if }}`, `{{ range }}`, pipelines,
  `{{/* comments */}}` and the `{{"{{"}}` literal-brace escape are all gone: a
  template using any of them fails with "unknown template variable". No current
  template needs them, but the next person adding one is who finds out.
- **Cross-SDK consistency.** There was never one shared implementation to
  diverge from. `dagger/go-sdk`'s copy of the Go helper had already forked —
  no path templating, no `ModulePackage` — and `dagger/sdk-sdk`'s
  `helpers/render-init-template` is a different tool entirely: 45 lines of
  `strings.ReplaceAll` over `__SDK_NAME__`. So deleting ours removes a fork
  rather than breaking a contract. Naming output does now differ from
  `strcase`, and so from go-sdk, for digit-bearing names — deliberately,
  because `strcase`'s answer produced modules that could not load (fix 4);
  otherwise the output is byte-identical. The consolidation target is a Dang
  `renderTemplate` primitive in polyfill, with go-sdk as the second consumer —
  the code here shows native rendering is enough — not another shared Go
  helper. Not done here, and noted for whoever picks it up. This is consistent
  with go-sdk's own `future/helper-cleanup.md`, which states the shared
  direction: prefer native Dang/core calls, use helpers only where they are
  unavoidable.

### Accepted, not fixed

Review findings deliberately left alone:

- **Template symlinks are no longer rejected.** The Go helper errored on them;
  the renderer has no equivalent guard. Templates are repo-owned and contain
  none, so the guard would only ever fire on a change we are writing ourselves.
- **Empty template directories are dropped.** The renderer builds output from
  file paths, so a template directory containing no files does not survive. No
  template has one, the behaviour is documented at the call site, and adding one
  would fail visibly the first time that template was used.
- **The raise-on-unknown-variable path is untested.** Exercising it needs a
  fixture template carrying a bad action, which costs more than the one line of
  behaviour it would pin.

## Progress

- Phase 0 (orient) — done.
  - Worktree branch was 36 commits behind; reset to `upstream/main` `6dca4e9`.
  - Design-doc home: no `future/`, no `hack/designs/`, no `design/` existed →
    created `hack/designs/`.
  - VCS: plain git. Host: GitHub (`upstream` = dagger/python-sdk, `origin` =
    eunomie/python-sdk). CI: Dagger Cloud checks (`dagger-dogfood`), driven by
    `@check` functions in `.dagger/modules/e2e`; no GitHub Actions workflows.
  - Sign-off trailer: `Signed-off-by:`.
- Phase 1–2 (feature doc + plan) — `e9bcefe`.
- Phase 3 (adversarial review) — done; two independent reviewers. Their
  verified findings reshaped this doc: the polyfill pin was dropped as a
  proven no-op, the `helpers/pyproject` and CI claims were corrected, the
  `generateAll` non-goal was restated honestly, and the call-path section
  stopped claiming "interpreter boot".
- Phase 4 (implement) — `ec61085`, `3b7e5b8`, `a6efef5`, `60f469a`, and this
  doc at `b407e47`. All 13 e2e checks green locally. Call-path config sweep run
  and reported above; its only shipped outcome is the 3.14 template fix, by
  design.
- Phase 5 (code review + fix) — two independent code reviewers, findings
  curated, applied in `306d089`, `e95ab8c`, `146b66c`, `2d63359`. The reviewers'
  strongest shared finding — that digit-bearing names diverge from `strcase` —
  turned out on testing to be a bug this branch fixes rather than one it
  introduces.
- Phase 6–7 (ship) — draft PR dagger/python-sdk#16, head `2d63359`. All 36
  Dagger Cloud checks green.
- Phase 8 (archive) — this file.
