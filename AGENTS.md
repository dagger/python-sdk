# AGENTS.md

## Run the checks locally

The checks need an engine that serves `WorkspaceSDK.scopeSetting`,
v1.0.0-beta.26 or later. The CLI runs on the engine it ships with; `--engine`
or `DAGGER_ENGINE` selects another (`dagger help engine`):

```sh
export DAGGER_ENGINE=container://<running-engine>
```

Run the e2e checks from the root of this checkout, by name or all of them:

```sh
dagger check -m .dagger/modules/e2e -l
dagger check -m .dagger/modules/e2e runtime-call-check find-client-root-check
dagger check -m .dagger/modules/e2e 'e2e:*'
```

Without a pattern, `dagger check` runs the same e2e checks, which is what CI
runs.

Judge a check by `dagger check`, never by `dagger call <check>`: on
v1.0.0-beta.18, `dagger call` exits 0 when the check fails.
