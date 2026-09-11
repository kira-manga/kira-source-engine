# Kira Source Engine

Platform-neutral Kotlin Multiplatform contracts and execution for Kira’s config-driven manga sources.

## Modules

- `source-contract` — signed configuration models, transport ports, validation contracts, and neutral results.
- `source-engine` — deterministic generic request composition and extraction.
- `source-testkit` — reusable golden fixtures and parity assertions.

The mobile app and backend must pin the same reviewed immutable package version before
coordinated activation. Networking is supplied through a port, and the backend alone owns
canonical JSON, source-config publication, checksums, and signatures. The provisional Engine5
cohort is not activated; its accepted offline evidence does not prove new-package availability
or joint consumer-cutover acceptance.

The package is built with Kotlin 2.2 while its public API and language level are capped at Kotlin
2.1. This is intentional: the Spring backend uses Kotlin 2.1 and the mobile app uses Kotlin 2.4,
so both consumers can read the same published metadata without skipping compiler checks.

## Verification

Use Java21 and an installed Android SDK37, as required by `AGENTS.md`. Local verification
and Maven-local publication remain available; a numeric version override is for local
verification only, not release authorization. Prefer an owned local Maven destination:

```bash
ANDROID_HOME="$HOME/Library/Android/sdk" ./gradlew check
ANDROID_HOME="$HOME/Library/Android/sdk" ./gradlew -PVERSION_NAME=0.1.0 \
  -Dmaven.repo.local="$PWD/build/local-maven" publishToMavenLocal
```

These commands are not evidence that release gates passed. Ordinary CI is unchanged and
currently selects Java17; alignment with the Java21/SDK37 owner requirements remains a
separate CI-hygiene obligation. Targets, publication components, Kotlin/API levels and the
committed `VERSION_NAME=0.1.0-SNAPSHOT` default are unchanged.

## Remote publication: current-source containment only

**Remote package publication is disabled in this source revision. Engine6 remains open.**

- `publish.yml` has no tag trigger. Its only manual-dispatch job has empty permissions,
  no checkout/build/credentials/environment/activation input, and always fails with an
  explicit disabled notice. Dispatching this same workflow source from another ref does
  not activate publication.
- The three module builds no longer configure a remote publishing repository or its
  credentials. Their `maven-publish` plugins and local publication model are retained;
  the former GitHub-destination publication tasks are not configured by this source.
- Each module's aggregate `publish` task explicitly refuses, including with a numeric
  `-PVERSION_NAME` override. `publishToMavenLocal` is not refused. Re-enabling a destination
  or publisher requires a separately reviewed source change, not a property/secret toggle.

This removes the present tag-to-package-write route; it is **not** an effective package-write
boundary against repository writers, historical/alternate workflows, init scripts or other
out-of-band credentials. Do not use old commits or ad-hoc routes to bypass containment.
No package access, protected environment, signed provenance, outgoing-byte gate or registry
immutability has been established by this source change. Configuration-only Gradle verification
and independent review are still required to accept this limited slice; no native/package
publication result follows from static inspection.

## Gates still required before any remote activation

| Gate | Still open; not implemented or waived by containment |
|---|---|
| External write authority | Owner-established effective package-write exclusion, including historical/alternate workflows and other writers. Restricted token defaults or an environment-held credential alone are not that boundary. Historical protection failures and package403 are not current capability or absence proof; no settings/token workaround is authorized. |
| Approval and protected identity | Genuine private approval/attestation capability; a pre-existing protected environment with required reviewers, self-review prevention, no bypass and an explicit publisher-branch policy; protected main ancestry and distinct approved publisher/producer workflow revisions. Environment existence or manual dispatch alone is not approval. |
| Release version and provenance | Owner-approved committed canonical version matching tag/project/publications; exact peeled source SHA/tree, repository/workflow/run/attempt/artifact/digest identity, successful trusted CI and verified signed subject/signer/source identity. Recheck mutable facts after approval; author-written PASS JSON is not provenance. |
| Complete native outgoing bytes | A separately proven pre-first-write gate for the complete three-module root/Android/JVM/Apple publication set: binaries, sources, POMs, `.module` links and deterministic sidecars, with mutable Maven index semantics handled separately. Preserve native Gradle components and byte identity even for direct single-publication requests; no custom uploader, dropped targets, hash normalization or relabelled rebuilds. |
| Registry and partial releases | Authoritatively unused complete target GAV set, effective serialization against other writers and an explicit non-atomic multi-publication failure policy. HTTP401/403/errors do not prove absence. No overwrite, deletion, retagging, workflow/release-level retry/resume or partial-version reuse is authorized. |
| Completion and consumers | Same-manifest complete remote readback, both real reader contexts resolving exact new GAVs, reviewed final source/dependency delta and joint App/Backend parity acceptance. Missing, partial or unreadable evidence holds activation. |

Gradle9.6.1's native publisher retries individual PUTs and can merely warn on SHA256/SHA512
sidecar upload failures. A future activation decision must represent those semantics honestly:
there is no blanket no-transport-retry guarantee, and Gradle exit0 is not complete remote
readback. The native byte gate and the larger authorization/publication framework are deferred,
not silently accepted. Engine5's provisional cohort and raw failures remain unactivated and
retain their previously accepted scopes.
