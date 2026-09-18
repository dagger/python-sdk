# A workspace SDK generates a module, and the builtin SDK runs it

*2026-09-18. For the engine, not for one SDK.*

## The question

A workspace names its own SDK:

```toml
[modules.python-sdk]
source = "python-sdk"

[sdks.python]
module = "python-sdk"
```

`dagger module init python` then generates the module with **that** SDK. The
generated manifest says:

```toml
[runtime]
source = "python"
```

At run time, `python` means the engine's **builtin** Python SDK, not the one the
workspace named. So one name resolves to two different SDKs, depending on the
phase. Should it?

## What we saw

```
$ dagger module init python -n my-module     # generates with the workspace SDK
$ dagger api functions my-module
! module "my-module": generated file "sdk/src/dagger/client/gen.py" is missing;
  run `dagger generate` and commit the generated files
```

The generated tree is correct for the SDK that wrote it. The error comes from a
different SDK — the builtin one — which expects the layout *it* generates.

Proof that the two SDKs differ, rather than the tree being wrong:

```toml
# the same module, with its runtime pointed at the workspace's SDK
[runtime]
source = "../../../python-sdk/runtime"
```

```
$ dagger api functions my-module
Name        Description
container   A container with the workspace source, ready to build.
```

## Why it surfaces now, and why it is not new

Before unified clients, the workspace SDK and the builtin SDK generated the
*same* layout, so running a module on the other one worked by accident. Unified
clients change the layout, so the mismatch becomes a hard error at the first
command a user types.

Nothing about this is specific to Python. Any SDK a workspace overrides has it.

## What the SDK can and cannot do

`Workspace.sdk(name:).ref` tells an SDK that it was named by the workspace, so
the SDK *could* write a path to itself into every module manifest it generates.
We have not done that, for two reasons:

- A manifest that names `../../../python-sdk/runtime` is bound to one directory
  layout. Move either directory and it breaks. A published SDK must keep writing
  `source = "python"`, so the manifest would mean different things depending on
  who generated it.
- The same workaround would have to be written once per SDK, in each language,
  for a mapping the engine already holds.

The engine cannot be helped from the module's side either: the error above comes
from the *released* SDK inside the engine, so no change on this branch can
improve that message.

## The fix we recommend

**Resolve `[runtime] source = "<name>"` through the workspace's SDK mapping,
the same way `dagger module init <name>` and `dagger generate` already do.**

Then `python` means "the Python SDK this workspace uses" in every phase, one
fix serves every language, and no path is baked into a user's manifest.

## Until then

Point the manifest at the SDK under development:

```toml
[runtime]
source = "<relative path>/python-sdk/runtime"
```

`hack/try-unified-clients.sh` does exactly this, which is why the walkthrough it
builds runs against the checkout rather than the builtin.
