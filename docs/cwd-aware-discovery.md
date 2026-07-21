# CWD-aware module discovery

The Python SDK delegates config discovery to `github.com/dagger/polyfill`, then
intersects the discovered directories with `currentModule.asSDK.modules`. The
engine's managed-module list remains authoritative while the caller's current
directory determines scope.

Discovery returns modules at or below the cwd and, when the cwd has no module
config, its nearest enclosing module. Both `dagger-module.toml` and legacy
`dagger.json` are considered together, so the nearest config wins regardless of
filename. Virtual environments and installed packages are excluded.

```console
dagger check -l
dagger call e-2-e mixed-config-lookup-check
dagger call e-2-e module-discovery-check
```

The fixtures cover mixed nested config formats, modern and legacy configs,
non-Python exclusion, root discovery, and discovery from inside a module.
