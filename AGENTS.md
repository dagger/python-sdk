# AGENTS.md

## Run the checks locally

The checks need an engine at this SDK's floor, v1.0.0-beta.18, or later. The
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
dagger check engine-e2e:floor-check
dagger check engine-e2e:below-floor-check
```

`floor-check` runs every e2e check on the floor engine, built from its commit.
To run e2e checks on the floor's released engine instead:

```sh
dagger check --engine=image://registry.dagger.io/engine:v1.0.0-beta.18 \
  -m .dagger/modules/e2e runtime-call-check
```

Judge a check by `dagger check`, never by `dagger call <check>`: on
v1.0.0-beta.18, `dagger call` exits 0 when the check fails.
