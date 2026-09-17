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

These commands are not evidence that release gates passed. CI selects Java21 and checks
the existing SDK's API37 metadata and `android.jar`, accepting real `android-37` and
`android-37.0` layouts. Conflicting SDK roots, missing prerequisites and wrong/preview APIs
fail; CI does not install, rename or alias a platform to pass this check. Targets,
publication components, Kotlin/API levels and the committed `VERSION_NAME=0.1.0-SNAPSHOT`
default are unchanged. CI's candidate/noncandidate distinction is described below.

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
shapes do not authorize execution.** Stage A changes no toolchain, target, module build, runtime
contract or consumer; the separate Stage B workflow recipe is described below. Ordinary builds without
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

## Stage B: check-only CI today; candidate provenance recipe, not a publisher

**The committed version is still `0.1.0-SNAPSHOT`.** That path runs the ordinary `check`
coverage and the small stdlib policy tests, reports **no candidate produced**, and skips
export, candidate artifact upload and attestation. Diagnostics are still retained.
A green check-only run is not a producer receipt.
This source recipe does not authorize a CI dispatch, acquisition, new release version or
publication, and does not claim a successful hosted macOS/Android/Apple producer run.

`ci.yml` accepts pushes and PRs targeting only
`remediation/production-readiness-2026-09-04`. There is no main/tag/dispatch/alternate-ref
candidate route. PRs are read-only and ineligible. Checkout does not persist credentials.
Candidate eligibility additionally requires the exact repository
`kira-manga/kira-source-engine`, trusted integration push, matching checked-out source and
workflow SHA/ref, and one canonical `X.Y.Z` in **committed** `gradle.properties`. Duplicate,
escaped or ambiguous properties, prereleases and ambient version/JVM overrides refuse.
No tag, CLI argument or environment value supplies a candidate version.

For an eventually owner-approved canonical commit, the recipe is deliberately linear:

1. Bind the full committed source SHA/tree, workflow bytes and every tracked file/mode.
   Reject dirty/deleted inputs, staged changes and unexpected additions, including ignored
   build scripts. Only the explicit root `.gradle`, `.kotlin`, `build` and three module
   `build` output directories are exempt **after** fresh-workspace admission, not arbitrary
   `**/build` paths. Recheck at the phase boundary, after export and during archive creation.
2. In **one macOS workspace**, run one ordinary online `check` invocation together with the
   thirty native POM/GMM generator roots (three modules × two generators × five publications).
   This invocation has no `kiraPublicationMode`. It is followed, on success only, by the same
   workspace's offline `:exportKiraPublicationBytes` with explicit export mode. No Maven-local,
   second producer, target sharding, arbitrary exclusions, test rerun in export or promotion.
3. Use fresh job-owned Gradle/Native homes, no dependency/project-output cache restore, no
   build/configuration cache, one worker, in-process Kotlin compilation, bounded heap and a
   job timeout. Stop the owned Gradle workers between phases and after export. Hosted tool
   and resource admission remains separate; `--offline` is not a network sandbox and cache
   presence is not provenance. Descriptor execution during export remains allowed; this is
   not the separate promotion's no-regeneration proof.
4. Validate the existing exact bundle and its source/model/seal relationships, then create
   one uncompressed USTAR archive without changing bundle contents. Intake rejects duplicate,
   missing/extra, traversal, link/device and extension entries and never extracts over source.
   All raw artifacts, including AAR/KLIB/source/POM/GMM bytes, are included. Checksum inventory
   rows remain **expected sidecars**, not fabricated archive payloads. The helper does not
   replace the Gradle native KMP/POM model or hardcode an observed payload count. Parser bounds
   are 8MiB per metadata/source file, 4096 entries/records and 512MiB per bundle/archive;
   exceeding them is a refusal requiring review, not truncation or target removal.
5. Upload the single archive using the pinned official artifact action, an exact
   `kira-publication-bytes-<run-id>-<attempt>` name, error-on-missing and no overwrite. Keep
   **four different SHA256 values** distinct: raw tar, `inventory.tsv` (manifest),
   `seal.sha256` itself, and GitHub's transport artifact digest. The immutable artifact ID
   and action outputs are retained outside the sealed tar, including the run step summary;
   the tar is never rewritten to contain its own receipt.

A separate lightweight `attest` job is conditioned on successful eligible production and the
same exact trusted source/workflow/ref. It downloads **only the producer's artifact ID**,
rechecks the independently retained raw/manifest/seal digests and committed inputs, and uses
the pinned official attestation action on exactly `kira-publication-bytes.tar` and the original
`inventory.tsv` bytes. It does not build or promote. Only this job requests `actions: read`,
`contents: read`, `id-token: write` and `attestations: write`; these are explicit provenance
privileges, **not** package-write privileges or proof of private-repository capability.
Unsupported capability fails the route; no unsigned or hidden-PAT fallback exists.

### Later read-only completed-receipt intake

The in-progress producer/attester does **not** certify its workflow's own final success.
`.github/scripts/publication_policy.py receipt` is an offline saved-receipt comparison,
not proof of live freshness and not a release-authorizing command or remote writer.
The connected intake described below acquires and compares fresh official receipts. It requires a separately reviewed fresh checkout
and expected identity/digests, final official attempt/current-run/job/artifact REST receipts,
the exact archive, an attestation bundle, and a separately admitted native verifier asset.
Expected values must come from the reviewed caller, **never from the artifact under test**.

The expected document contains the literal repository/ref/workflow ref, source SHA/tree,
workflow SHA and SHA256, committed version, integer run/attempt/workflow/producer-job/
attestation-job/artifact IDs, and the four separate digests named above. The receipt document
has `attempt`, `current`, `jobs` and `artifact` objects from the corresponding official REST
endpoints. The caller must acquire/authenticate fresh receipts for the selected IDs, including
the attempt-specific jobs list; author-written JSON is not an API origin guarantee. Intake
rejects failed/in-progress or superseded attempts, pagination/missing jobs, expired/deleted/
replaced artifacts and wrong-run or wrong-byte substitution. Never select “latest successful”.

An artifact REST object alone cannot establish its run attempt. The adapter additionally runs
native signature/certificate verification and compares the **verified certificate's** signer,
source, workflow and `runInvocationURI` (including `/runs/<id>/attempts/<attempt>`), then the
exact archive and inventory subjects against independently measured bytes. It does not use
workflow-controlled predicate metadata as signer/source/invocation authority. Decoded DSSE,
`attested: true`, log text, supplied “verified” JSON or exit0 without those comparisons refuse.

The native adapter admits only the official Linux-amd64
[`gh_2.100.0_linux_amd64.tar.gz`](https://github.com/cli/cli/releases/tag/v2.100.0), SHA256
`e4d4bb4498e8d007abe545b6568926793ace1b6447da598294a610018cb164be`. It makes no acquisition,
uses no ambient `gh` or token/config, and copies only the fixed executable member into an
owned temporary directory after checking that asset pin. Later native verification may refresh
public trust roots; it is not claimed to be network-isolated. Its flags and strict output
adapter follow [CLI source `45437bc7`](https://github.com/cli/cli/blob/45437bc7eeeb3359bbfddd1742f79de7652fd3e2/pkg/cmd/attestation/verify/verify.go)
and [sigstore-go `22d3691c`](https://github.com/sigstore/sigstore-go/blob/22d3691c7b8e0c5530fae3c05577690bfef5cd00/pkg/fulcio/certificate/summarize.go)
(flat certificate extension fields, no guessed nested-schema fallback). Action pins and the
actual native adapter require independent source review before operational admission.

The helper's complete approval-tuple/readback comparisons are only finite refusal tests.
Even matching facts retain a **Stage C authority/live-recheck hold**. They mint neither
approval nor unused-GAV evidence; any used/partial cohort or rerun holds. Protected lineage,
publisher identity, immutable complete-GAV authority, all-writer serialization, remote readers
and final App/Backend parity remain external gates. The Stage A gate's exact Java runtime,
vendor, OS and architecture binding also remains; saying “Java21” on another publisher host
does not establish compatibility.

The focused nonrelease test command is:

```bash
python3 -B -m unittest discover -s .github/scripts -p 'test_publication_policy.py' -v
```

The original 26 synthetic policy tests exercise comparisons/serialization with mocked native
subprocesses and Git; the same command also includes nine real-Git lineage tests. Neither
proves production bytes, signatures, consumer parity or remote qualification. YAML
trigger/permission/action/phase review remains a separate obligation.

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

The `lineage` increment added only local Git tag/version/ancestry checks and did not change
workflows. The separate Stage B CI recipe is now wired as described above; the publisher
and remote-publication containment remain unchanged. The read-only command is:

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

### Read-only official producer receipt acquisition

`.github/scripts/github_publication_receipts.py` provides
`acquire_official_receipts(expected, token=None)`. It validates the independently
selected complete release tuple before any network access, then fetches only the
exact attempt, its jobs, its artifact, and finally the current run from the literal
GitHub API host. It uses system TLS validation, ignores proxy/token environment
discovery, refuses redirects/pagination/compression and bounds each JSON response
to 8MiB. Every GET requests cache revalidation and rejects positive/malformed `Age`;
absent/zero age is not an independent guarantee of origin consistency. The optional
read token is explicit and never included in returned data or
error messages. HTTP streams/connections close on success and failure. Socket
timeouts are 15 seconds; an elapsed 30-second budget is checked around streaming
reads. System DNS resolution does not have a separately enforced hard deadline.

Returned official metadata is an **observation, not approval or a reservation**.
Current-attempt readback happens last to reject already superseded producer runs;
a run or artifact can change afterward. Reacquire at each later decision boundary.
No artifact bytes, attestation, package availability, branch protection, approval,
or all-writer exclusion is proved by these metadata receipts alone. The connected
intake below additionally runs the existing byte/native checks. Stage C's unconditional
hold and the disabled publisher are unchanged. No remote publication is enabled.

Focused synthetic HTTP-boundary tests (no real API, credentials or provenance):

```bash
PYTHONPATH=.github/scripts python3 -B -m unittest test_github_publication_receipts -v
```

### Connected completed-candidate intake (read-only)

`connected_publication_intake.connected_intake(root, expected, archive, gh_asset,
attestation, *, token=None)` connects the existing byte/native and official-GET adapters.
It validates and copies the independently selected tuple before work, including the
explicit optional read token's bounds. It then checks the committed source/version,
archive/seal and pinned native attestation verification. Only after that expensive phase
does it acquire the exact official attempt/jobs/artifact/current observations, with the
current run last. Final source/archive rereads still run **after** the network phase to
refuse local mutations during acquisition; the native verifier is not run again.

The CLI is deliberately anonymous, with no token argument or token environment/config
discovery. It accepts neither saved receipts nor a caller's decoded verification result:

```bash
python3 -B .github/scripts/connected_publication_intake.py \
  --root "$RELEASE_REPOSITORY" --expected "$INDEPENDENT_SELECTION_JSON" \
  --archive "$SELECTED_ARCHIVE" --gh-asset "$PINNED_NATIVE_VERIFIER_ARCHIVE" \
  --attestation "$SELECTED_SIGNED_ATTESTATION_BUNDLE"
```

These command shapes do not authorize API access or intake execution. A separately
authorized private-reader caller may supply an explicit programmatic token; it is sent
only through the fixed-host GET adapter, never to the native verifier, stdout or errors.
The pinned verifier's own public trust-root refresh may require network, as before.
The existing `publication_policy.py receipt --receipts ...` command remains an explicitly
**offline saved-receipt comparison**, not live official acquisition. Both entry points
reuse the same byte/native and final-reread implementation.

Success returns no cached receipt or release permission. The sequential API reads and
filesystem rereads are **not an atomic snapshot or freshness lease**; metadata can change
during final local rereads or after return. A later writer must reacquire at its own
decision boundary. Protected approval, genuine hosted provenance interoperability,
unused-GAV authority, all-writer exclusion and remote readback/consumers remain open;
the unconditional Stage C hold and publisher containment are unchanged.

Focused connected-boundary tests use existing synthetic source/archive fixtures and a
synthetic HTTP boundary; Git/native verification are mocked, not real provenance or API
evidence. They do not replay the existing policy, lineage or GET suites:

```bash
PYTHONPATH=.github/scripts python3 -B -m unittest \
  test_connected_publication_intake.ConnectedIntakeTests -v
```
