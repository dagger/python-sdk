# CWD-aware module discovery

The Python SDK asks
`currentModule.asSDK(workspace: ws).modules` for the registered modules
relevant to the caller's current directory. The engine owns both membership and
scope selection, so the SDK does not scan config files or reconstruct the cwd
policy.

Selection returns modules at or below the cwd and, when the cwd itself is not a
registered module, its nearest registered ancestor.

`mod` resolves a single module from the same list, re-anchoring the workspace at
the requested path so the engine scopes the selection there. A path that no
registered module contains is an error rather than a module this SDK does not
manage.

```console
dagger check -l
dagger call e-2-e mixed-config-lookup-check
dagger call e-2-e module-discovery-check
dagger call e-2-e unmanaged-lookup-check
```

The fixtures cover mixed nested config formats, modern and legacy modules,
non-Python exclusion, root selection, and selection from inside a module.
