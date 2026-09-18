# A unified client's target is not in its caller's cache key

*2026-09-18. For the engine and the specification, not for one SDK.*

## The question

A unified client loads its target module at run time, through
`Query.serveModule(address, refPin)`. What should that load contribute to the
cache key of the call that performed it?

Today it contributes nothing, so a caller keeps a stale result after its
client's target changes.

## What we saw

Two modules. `lib.greeting(name)` returns `hello, <name>`. `demo.hello(name)`
calls it through a generated client. Then `lib` is changed to return
`CHANGED, <name>`, and nothing else is touched.

```
$ dagger call -m demo hello --name probe        # before the change
hello, probe

# lib now returns "CHANGED, ..."

$ dagger call -m demo hello --name probe        # same arguments
hello, probe                                    # stale

$ dagger call -m lib  greeting --name probe     # the target itself
CHANGED, probe                                  # the change is there

$ dagger call -m demo hello --name other        # different argument
CHANGED, other                                  # a fresh key sees it
```

The third and fourth commands are what make the mechanism plain. The target's
change *is* visible to the engine. The caller is stale only where its own cache
key is unchanged.

Seen on a released `v1.0.0-beta.13` engine through the SDK's fallback load path,
and on an engine built from `dagger/dagger` at `284cd849` through `serveModule`
itself. Seen again on released `v1.0.0-beta.14`, with the module driven by a
Dang entrypoint rather than a runtime. It is not a property of the new field,
nor of how the module is driven; it is a property of loading a module at run
time.

## Why it used to work

`[[dependencies]]` in the caller's manifest made the target part of the
caller's module identity. Change the target, and the caller's digest changed
with it, so the caller's functions were re-evaluated.

This paragraph is an **inference from the behaviour**, not something read in
engine code. What is measured is below; the mechanism that used to prevent it is
the engine's to confirm.

Unified clients remove `[[dependencies]]`. A generated local client records only
a path:

```python
NAME = "lib"
REF = "/.dagger/modules/lib"
PIN = None
```

Nothing about the target's content reaches the caller.

## Why an SDK cannot fix it

The staleness is decided before any SDK code runs.

1. The engine evaluates `Demo.hello`. Its cache key comes from the caller's
   module identity and the arguments.
2. That key hits. The function body never runs.
3. So `serveModule` is never called, and the client's own subcall — which
   *would* be keyed correctly on the target — never happens.

An SDK can only act inside step 3. By then the answer has been returned. The
`--name other` run above confirms it: give the caller a key it has not seen, and
the body runs and reads the new target.

## Three answers

**(a) Record the client targets in the caller's own config.** The SDK already
knows them: the workspace file lists a scope's clients. Writing the target and
its pin into the module's manifest would put them back into the caller's
identity. Cheap, and it restores exactly the invariant that was lost.

The cost: it is `[[dependencies]]` again in everything but name, and the point
of unified clients is that the artifact is self-sufficient. It also cannot
express a local target's *content*, only its path, unless the manifest carries a
digest that generation refreshes — and then a stale manifest is a new way to be
wrong.

**(b) Make a served module part of the cache key of whatever served it.** The
engine records, for each cached function result, the modules served during its
evaluation, and invalidates that result when one of them changes. This is the
discovered-dependency problem that build systems already solve, and it is the
only answer that stays true when the set of clients a call uses depends on the
arguments.

The cost: it is engine work, and it needs a story for a git target (the pin
settles it) against a local one (content).

**(c) Document it.** A user learns that changing a client's target needs
`--no-cache`, or a touch of the caller. We consider this unacceptable: the first
time a user debugs a module by changing its dependency, the tool lies to them.

## Recommendation

(b) is the right answer, and it belongs to the field rather than to any
language. (a) would unblock end-to-end use sooner, at the price of re-creating
what this design set out to delete; take it only as a stopgap, and only if (b)
is far off.

## What it blocks

Nothing in generation, and nothing in the Python SDK's own checks. It blocks
using unified clients to replace real dependencies, because a module developer
who changes a dependency and re-runs the caller gets the previous answer.
