# Module lookup

`mod` resolves a single module from the modules registered to this SDK in the
workspace configuration (`Workspace.sdk(name).modules`): the nearest
registered module containing the requested path. The engine owns membership,
so the SDK does not scan config files. A path that no registered module
contains is an error rather than a module this SDK does not manage.

```console
dagger check -l
dagger call e-2-e mixed-config-lookup-check
dagger call e-2-e module-lookup-check
dagger call e-2-e unmanaged-lookup-check
```

The fixtures cover mixed nested config formats, modern and legacy modules,
non-Python exclusion, and selection from inside a module.
