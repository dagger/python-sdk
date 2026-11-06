# AGENTS.md

## Run the checks locally

The checks need an engine at this SDK's floor, v1.0.0-beta.26, or later. The
CLI runs on the engine it ships with; `--engine` or `DAGGER_ENGINE` selects
another (`dagger help engine`):

```sh
export DAGGER_ENGINE=container://<running-engine>
```

Run the e2e checks from the root of this checkout, by name or all of them:

```sh
dagger check -m .dagger/modules/e2e -l
dagger check -m .dagger/modules/e2e runtime-call-check find-client-root-check
dagger check -m .dagger/modules/e2e 'e2e:*'
```

Without a pattern, `dagger check` also selects the `engine-e2e` checks, which
build engines from dagger/dagger commits. CI runs them:

```sh
dagger check engine-e2e:runtime-call-check
dagger check engine-e2e:floor-coverage-check
```

Each e2e check has an `engine-e2e` check of the same name, which runs it on the
floor engine, built from its commit. `floor-coverage-check` fails when an e2e
check has no such check. To run e2e checks on the floor's released engine
instead:

```sh
dagger check --engine=image://registry.dagger.io/engine:v1.0.0-beta.26 \
  -m .dagger/modules/e2e runtime-call-check
```

The target behavior checks run on the naming-capable floor engine while
selecting the released beta.24 or older supported API view. The duplicate
checks on a physical beta.24 engine are removed: that engine cannot run this
naming SDK. `floorClient` verifies the installed source CLI's actual engine
version, ignoring only build metadata, rather than pulling a release image
for that verification. Checks that use the playground still need the
published image carrying their CLI and remain release-dependent.

Judge a check by `dagger check`, never by `dagger call <check>`: on
v1.0.0-beta.18, `dagger call` exits 0 when the check fails.
