# Capture carrier only — never merge into app or Engine product branches

This branch is one reviewed control commit on Engine integration
`686fb6e0b7d4f09914471860963a61c77e7d4f01`. It executes **no Engine build or
publication**. Its new, exact-branch push trigger does not match the existing
`main` / `feature/**` CI trigger. No tag, deployment, store workflow, manual
rerun, or package write permission is supplied.

The job anonymously checks out public app
`14918702e814c7c62e0615a9727a152706bd9768`, verifies its inputs, and copies the
nine previously reviewed App61 files from `overlay/` into **that disposable app
checkout only**. `binding.json` pins each original and replacement byte identity.
All twenty Gradle inputs then match the frozen scaffold, as do its four wrapper
inputs. Public149 includes later application changes: this is **not whole-tree
equivalence to c6**, an integration merge, or acceptance of the overlaid older
workflow/document versions. Those app workflow files are inert here.

Only `:app:bundleRelease :app:lintRelease` run, once, with native SHA256/lock
generation, strict verification selection, no build/configuration cache, no
Maven-local/composite override, and no Java/SDK auto-download. The selected
Temurin21.0.12.1+1 launcher comes from the pinned setup-java action; SDK37 must
already exist. Installed runtime-byte authority, JDK17/Native and host-image
gaps remain separate. No additional installer is hidden in the helper.

The repo's fresh `contents:read` / `packages:read` job token is explicitly mapped
to the declared registry credential for **only the unsigned Gradle subprocess**.
The launcher does not forward ambient credentials to any child. There is no
owner-token fallback, package republishing, Engine234 adoption, or local artifact
substitution. The public checkout, runtime check, immediate daemon stop and
collection receive no package token.

The example Firebase slot is exclusively created and removed only if its bytes
are unchanged. `allowUnconfiguredSourceRemote=true` is **NONSHIPPING capture-only**;
no signing, real Firebase, store, or deployment inputs are available. The source
scaffold's null metadata identity/empty lock map remain unchanged: the normal
release verifier is not faked into passing during bootstrap.

Native bootstrap eagerly attempts resolvable configurations; the small task list
is not a task-only resolver. Retained XML/locks are the actual emitted bytes,
always **unreviewed**, and partial on a failed execution. Only actual observed
Engine0.1.0 metadata and payload hashes are collected; the two known published
JVM identities are compared if present. The original Gradle result is retained
independently from stop/source/cleanup failures. No uncaptured lock, test
execution, Apple execution, complete graph, strict consumption, or App61 closure
is claimed.

The small runner follows the existing immediate-stop then owned-output cleanup
pattern. It uses a fresh run-specific home/cache/checkout, never a shared cache.
After ordinary daemon stop, bounded TERM then KILL stops only kernel-proven owned
children, pinned by pidfd and rechecked parent/start identity. The Linux subreaper
adopts detached descendants for the same bounded cleanup. Reaped process-group ids
never authorize signals. A nonzero stop or forced cleanup keeps the attempt FAILED
even when all owned workers are stopped; the original native exit is separate.
The final no-owned-child barrier gates both deletion and report upload. Changed
source or a changed Firebase slot also prevents deletion. Artifacts are limited to command logs,
source/runtime/results, native XML/locks and actual Engine descriptors; **no AAB,
APK, cache archive, credential file, environment dump, or private source archive**
is uploaded. Retention is three days. A timeout or nonzero native exit is not
relabelled success because some metadata was emitted.
