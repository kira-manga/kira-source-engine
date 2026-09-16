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
  or remote publisher requires a separately reviewed source change, not a property/secret
  toggle. The explicit modes below add only one fixed owned-file destination. Forbidden
  publication graphs are rejected before task execution, not merely by `publish.doFirst`.

This removes the present tag-to-package-write route; it is **not** an effective package-write
boundary against repository writers, historical/alternate workflows, init scripts or other
out-of-band credentials. Do not use old commits or ad-hoc routes to bypass containment.
No package access, protected environment, signed provenance, proven outgoing-byte gate or
registry immutability follows from this source. The Stage A addition below still requires
independent actual-diff review and separately admitted product validation; static inspection
is not a native/package publication result.

## Stage A: unqualified owned-file byte export and promotion

**Source implementation only: no real KMP export/promotion pass is claimed. These command
shapes do not authorize execution.** No workflow, toolchain, target, module build, runtime
contract or consumer changes accompany this gate. Ordinary builds without
`kiraPublicationMode` add no publishing repository and retain Maven-local behavior.

After separate admission, `gradle/publication-gates.gradle.kts` has two local modes. Both
require Gradle9.6.1, the actually applied KGP2.2.21 plugin in every module, Java21, offline
operation, no configuration cache and a numeric local `VERSION_NAME` override; the committed
SNAPSHOT default is deliberately ineligible. Missing SDK37/cached prerequisites are a stop,
not permission to download them. A complete macOS
producer and a fresh compatible promotion workspace are the initial qualification target.

The pinned read-only model adapter reads `PublicationInternal.component`,
`SoftwareComponentInternal.usages` and `MavenPomInternal.dependencies`; it never runs an
internal publisher, descriptor generator or XML action. Expected variant membership,
typed attributes, per-variant project edges and child destinations come from the actual
components, not agreement between supplied descriptors. Unsupported interfaces, attribute
types, project selectors, capabilities, global excludes or cohort constraints stop the gate.

Native POM tuples supply cohort dependency scopes/optional/type/classifier/exclusions and
separate dependency-management expectations **before KGP's XML coordinate rewrite**. Final
project GAVs use only the admitted homogeneous-cohort rule: match corresponding actual KGP
targets by name/platform/typed attributes and identical usage shapes, then use their real
publication coordinates. This is not a general variant resolver or a post-XML POM model.
Rewrite-property customization, ambiguous targets, inherited/profile dependencies and
unsupported filtering fail rather than trigger generation or a supplied-metadata fallback.
`kotlin.mpp.keepMppDependenciesIntactInPoms` must be absent from extras, Gradle properties
and root `local.properties`; even explicit false or a present-null extra is refused. The
local file uses UTF-8 Java Properties syntax. Its private contents, values, paths and digests
are neither logged nor sealed; only the successfully checked default policy enters `model.tsv`.
Android/native mapping, provider resolution and fresh no-generation promotion remain unproven.
These checks cover cohort project semantics, not all third-party dependency metadata.

1. **Export/build separately.** `:exportKiraPublicationBytes` depends on the actual artifact,
   POM and Gradle-module producers for all three modules and their five existing publications
   (`kotlinMultiplatform`, `android`, `jvm`, `iosArm64`, `iosSimulatorArm64`). It copies their
   raw outputs without repackaging or rewriting them to `build/publication-bytes/bundle/`.
   The bundle must not already exist. No native repository writer may run in this mode.

   ```bash
   # LOCAL_TEST_VERSION is a separately chosen numeric owned-local test value, not a release.
   ./gradlew --offline --no-configuration-cache \
     -PkiraPublicationMode=export "-PVERSION_NAME=$LOCAL_TEST_VERSION" \
     :exportKiraPublicationBytes
   ```

   `payloads/` contains only raw binaries, sources, POMs and `.module` files. `inventory.tsv`
   records publication/GAV, role, Maven-relative path, length and SHA256 in nine columns,
   including deterministic checksum-sidecar expectations. `model.tsv` binds native output
   paths/producers, native component/variant/project-edge/POM expectations, selected build/tool
   files, module `src` bytes and Gradle/KGP/Java/OS identity;
   `producers.txt` lists exact fully qualified producer tasks. `seal.sha256` hashes those
   three metadata files. Independently retain the SHA256 of **`seal.sha256` itself**, printed
   by export. None of these hashes proves source provenance, CI identity or human approval.

   For a separately admitted configuration-only observation, add `--dry-run --info` to that
   export command. The eager read-only census logs `Kira publication model:` rows for all
   model seams. It runs no producer or byte-validation actions and earns no export/promotion
   or negative-case credit. The model is read again at export, intake and each writer guard.

2. **Restore/promote without regeneration.** In a fresh owned workspace with matching
   source/build/model/tool identity, copy the verified bundle to
   `build/publication-bytes/input/`, without build caches or task histories. Every native
   output location must be absent, and `build/publication-bytes/owned-maven/` absent or
   empty. There is no destination override, credential source or remote mode.

   Form literal `-x <task>` arguments from the sealed producer list; never `eval` its text.
   The gate recomputes and requires the exact exclusion set and final no-producer graph.
   Keep all producers, especially POM/GMM generators, **enabled but excluded**. For example
   in Bash, after verified intake and separate retention of `SEALED_DIGEST`:

   ```bash
   producer_args=()
   while IFS= read -r task; do
     [[ $task =~ ^(:[A-Za-z0-9_.-]+)+$ ]] || exit 1
     producer_args+=(-x "$task")
   done < build/publication-bytes/input/producers.txt
   ./gradlew --offline --no-configuration-cache \
     -PkiraPublicationMode=promote "-PVERSION_NAME=$LOCAL_TEST_VERSION" \
     "-PkiraPublicationSealSha256=$SEALED_DIGEST" \
     "${producer_args[@]}" :promoteKiraPublicationBytes
   ```

   Restore and fresh verification are mandatory dependencies, not standalone entrypoints.
   Every serialized native writer rechecks the entire 15-publication cohort, current
   graph/exclusions and action recipe immediately before its original native action. No
   compilation, packaging, POM or GMM generation is allowed in promotion. Ordinary `publish`
   aggregates, Maven-local, extra tasks and mixed export/promotion graphs are refused.
   A direct task such as `:source-engine:publishJvmPublicationToEngine6OwnedRepository` uses
   the same whole-cohort guard, but its required readback covers only selected publications;
   that is not a complete-cohort promotion result.

Gradle still computes checksum sidecars and artifact-level `maven-metadata.xml` indexes;
this is not zero computation. `:verifyKiraPublicationReadback` runs after native writers and
requires actual task success, the exact immutable path/byte set and strictly bounded index
semantics. Native exit0 alone is insufficient. Failed/partial bundles or destinations are
not overwritten, resealed or automatically retried; preserve the failure and use a separately
admitted fresh workspace. Do not relax a model/metadata gate, drop a target or relabel a
rebuild if the pinned KMP no-regeneration mechanism fails.

This is not isolation from arbitrary Gradle/init code or concurrent hostile filesystem
mutation. Excluded-but-enabled native descriptor publication and the actual Android/Apple
model still need product qualification. File hashes and owned-file readback establish neither
trusted production/approval nor remote immutability, external-writer exclusion or consumer
acceptance. The credential-absence check uses the separately reviewed, pinned Gradle9.6.1
internal presence API (never credential values); no internal/custom publisher is used.

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
readback. The owned-file source gate remains unqualified; remote wiring and the larger
authorization/publication framework remain deferred, not silently accepted. Engine6 remains
PARTIAL/open. Engine5's provisional cohort and raw failures remain unactivated and retain
their previously accepted scopes.

## Read-only release lineage observation

The product policy helper and its existing Stage B tests are retained without adopting
the separate candidate CI recipe. Both workflows and all remote-publication containment
remain unchanged. The `lineage` command adds only local Git tag/version/ancestry checks:

```bash
python3 -B .github/scripts/publication_policy.py --root "$RELEASE_REPOSITORY" lineage \
  --version "$SELECTED_VERSION" --tag "$SELECTED_TAG" \
  --source-sha "$SELECTED_SOURCE_SHA" --source-tree "$SELECTED_SOURCE_TREE" \
  --tag-object "$SELECTED_TAG_OBJECT" --branch-tip "$SELECTED_CAMPAIGN_TIP"
```

Supply independently selected full SHA-1 object IDs, not identities inferred from the
artifact being assessed. `--tag-object` is the annotated tag object or, for a lightweight
tag, its commit object. The only branch inspected is
`refs/heads/remediation/production-readiness-2026-09-04`. This campaign check does **not**
choose the eventual protected publisher branch or prove that the local ref is protected
or authentic. It never fetches, changes refs or registers a package destination.

The tag must peel to the selected source commit/tree, whose committed regular
`gradle.properties` must contain the matching canonical `X.Y.Z`. Working-tree files and
version environment variables cannot replace that blob; checkout cleanliness is not
claimed by this command. `0.1.0-SNAPSHOT` remains release-ineligible. Actual ancestry,
not equal trees, must connect the source to the exact selected campaign tip. Shallow,
missing-parent, grafted, replacement, alternate-object and partial/promisor history is
refused. Git environment overrides are discarded, network protocols are disabled, and
commit-graph caches are not used. Reads have individual 30-second timeouts; the complete
walk is limited to 4096 commits and version metadata to 8MiB.

Success returns a **local observation, not a reservation**. Tag/branch objects and history
guards are checked again before returning, but refs can move afterward. There is no
tag-signature, remote-origin, protection, approval or provenance claim. The unconditional
Stage C authority/live-recheck hold remains, as do separate committed-checkout/receipt
intake, live approval/freshness, publisher/readback and consumer requirements. No publisher
is activated and no release version is selected by this increment.

The new focused tests use disposable real Git repositories, not mocked ancestry:

```bash
PYTHONPATH=.github/scripts python3 -B -m unittest \
  test_publication_policy.ReleaseLineageGitTests -v
```

Their result cannot qualify GitHub protection or publication. The earlier 26 synthetic
policy cases and accepted Stage A/archive evidence retain their separate scopes; this
increment does not require replaying them or running Gradle/native builds.
