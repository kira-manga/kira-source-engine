# Kira Source Engine

Platform-neutral Kotlin Multiplatform contracts and execution for Kira’s config-driven manga sources.

## Current single-owner release policy

The owner-authorized policy uses real PRs and required `verify` checks from GitHub
Actions (app15368), but **zero mandatory human source approvals** and no distinct
last-push approval. Both `main` and the integration branch enforce these checks for
administrators, resolve conversations and disallow bypass, force pushes and deletion.
CI accepts PRs to either branch; only the integration push can produce a release cohort.
GitHub cannot approve one's own PR; no fake approval is created or required.

Publication still requires an actual approval by the named owner in `source-publication`.
Initiator approval is allowed (`prevent_self_review=false`); admin bypass remains disabled.
The protected caller below is configured for the observed environment/branch-rule identities.
This source wiring is **not evidence of successful remote publication**. Only a completed
native upload, complete remote byte readback and real App/Backend consumer acceptance establish
that result. Earlier Stage A/B/held-slice descriptions below are historical implementation
boundaries; the current connected workflow and native attachment sections supersede their
blanket "publisher disabled" statements, not their remaining safety requirements.

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
publication components and Kotlin/API levels are unchanged. The committed
`VERSION_NAME=0.2.6` is a **prospective engineering candidate, not a reserved or published
version**; no tag or consumer pin changes with it. Historical version tags must not be moved
to admit changed helper bytes. CI's candidate/noncandidate distinction
is described below.

## Remote publication: current-source containment only

**Remote package publication is disabled in this source revision. Engine6 remains open.**

- `publish.yml` has no tag trigger. Its held manual workflow admits only this repository's
  numeric identity and the campaign integration ref, with read-only permissions. Separate
  control/producer checkouts and an explicit policy-read secret support fresh intake before
  and after the selected native environment boundary. The trusted policy is currently
  unconfigured, so it refuses before HTTP or environment selection; even after configuration,
  the caller refuses before executing the constructed native publication command. No package
  write permission or write credential is supplied. This is not exclusion of other workflows
  or out-of-band writers, and does not remove the independent Gradle publication holds.
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
operation, no configuration cache and a canonical numeric version. An explicit `VERSION_NAME`
override is only for separately admitted owned-local qualification; SNAPSHOT is ineligible.
Missing SDK37/cached prerequisites are a stop,
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

## Stage B: check-only PRs and prospective candidate provenance, not a publisher

**The committed prospective version is `0.2.6`, not reserved or published.** PRs remain
check-only: ordinary `check` coverage and the stdlib policy tests report **no candidate
produced** and skip export, candidate artifact upload and attestation. Diagnostics are
still retained. A green PR is not a producer receipt. An exact eligible integration push
can produce candidate bytes under the recipe below; this does not grant remote publication,
prove unused registry coordinates, or claim a successful current-source package qualification.

`ci.yml` accepts pushes and PRs targeting only
`remediation/production-readiness-2026-09-04`. There is no main/tag/dispatch/alternate-ref
candidate route. PRs are read-only and ineligible. Checkout does not persist credentials.
Candidate eligibility additionally requires the exact repository
`kira-manga/kira-source-engine`, trusted integration push, matching checked-out source and
workflow SHA/ref, and one canonical `X.Y.Z` in **committed** `gradle.properties`. Duplicate,
escaped or ambiguous properties, prereleases and ambient version/JVM overrides refuse.
No tag, CLI argument or environment value supplies a candidate version.

For an admitted canonical integration commit, the candidate-only recipe is deliberately linear:

1. Bind the full committed source SHA/tree, workflow bytes and every tracked file/mode.
   Reject dirty/deleted inputs, staged changes and unexpected additions, including ignored
   build scripts. Only the explicit root `.gradle`, `.kotlin`, `build` and three module
   `build` output directories are exempt **after** fresh-workspace admission, not arbitrary
   `**/build` paths. Recheck at the phase boundary, after export and during archive creation.
2. In **one macOS workspace**, create and boot one owned simulator using an already installed
   available iOS runtime (bounded420s readiness; no runtime download). The same online `check`
   invocation includes all three unfiltered `iosSimulatorArm64Test` task roots with that exact
   `--device` UUID and, only for candidates, thirty native POM/GMM generator roots
   (three modules × two generators × five publications).
   This invocation has no `kiraPublicationMode`. It is followed, on success only, by the same
   workspace's offline `:exportKiraPublicationBytes` with explicit export mode. No Maven-local,
   second producer, target sharding, arbitrary exclusions, test rerun in export or promotion.
3. Use fresh job-owned Gradle/Native homes, no dependency/project-output cache restore, no
   build/configuration cache, one worker, in-process Kotlin compilation, bounded heap and a
   job timeout. Stop the owned Gradle workers between phases and after export. Hosted tool
   and resource admission remains separate. After production's Gradle stop, immediately shut down
   and delete the exact owned simulator, including after failed production. Final always-cleanup
   retains reservation-based recovery for partial setup/deletion, verifies absence, and preserves
   its result alongside existing evidence.
   `--offline` is not a network sandbox and cache
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

The macOS producer now admits the reviewed Kotlin 2.2.21 authenticated-source libffi
bootstrap on an **actual Darwin ARM64** host. `ci-bootstrap` first checks fresh committed
inputs, all five reviewed helper hashes and the exact run-owned private runtime, with no
ambient native overrides. After prepare, `ci-start`, `ci-check` and both `pack` boundaries
admit only the exact derived native-home/cache-off `GRADLE_OPTS` and re-run the helper's
local profile/isolation verification. Stable provenance is included in before/after equality;
only the helper's increasing verification counter is excluded. General environment and Linux
intake guards remain unchanged. Owned helper cleanup preserves its provenance in diagnostics,
including failed production. This adds no SDK install/alias, prebuilt checksum waiver, cache
restore, release authority or remote writer; hosted canonical production is still a separate gate.

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

The native adapter selects only these official CLI v2.100.0 assets by the actual host:

- Linux/x86_64: [`gh_2.100.0_linux_amd64.tar.gz`](https://github.com/cli/cli/releases/tag/v2.100.0),
  SHA256 `e4d4bb4498e8d007abe545b6568926793ace1b6447da598294a610018cb164be`, member
  `gh_2.100.0_linux_amd64/bin/gh` (unchanged).
- Darwin/arm64: [`gh_2.100.0_macOS_arm64.zip`](https://github.com/cli/cli/releases/tag/v2.100.0),
  SHA256 `45f9a62da2f6e641a7fad57e2ce39656dfd7ef331372d80a2a2aed65abb01642`, member
  `gh_2.100.0_macOS_arm64/bin/gh`. The official release API, checksum list and acquired archive
  agree on this pin; the ZIP's executable is a Unix regular file. No other ZIP member is extracted.

Unsupported hosts, wrong-platform assets and archive/member bounds fail closed; no PATH binary,
format autodetection or translated-platform fallback is used. Darwin support is not a claim of
actual macOS signature-verification qualification; that requires a separately admitted real candidate.
The adapter makes no acquisition, uses no ambient `gh` or token/config, and copies only the fixed
executable member into an owned temporary directory after checking that asset pin. Both archive
and executable bounds remain 64MiB, with at most 4096 entries. Later native verification may refresh
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

## Gates required for each remote publication

| Gate | Required evidence; not waived by single-owner policy |
|---|---|
| External write authority | Owner-established effective package-write exclusion, including historical/alternate workflows and other writers. Restricted token defaults or an environment-held credential alone are not that boundary. A bounded owner-controlled window disables other workflows and reobserves repository writers, runs and package state; the operator separately accounts for out-of-band credentials and org installations. Historical failures are not current capability or absence proof. |
| Approval and protected identity | Genuine GitHub approval/attestation capability; a pre-existing protected environment with required reviewers, owner-authorized self-approval (`prevent_self_review=false`), no admin bypass and an explicit publisher-branch policy; protected source ancestry and independently bound producer/publisher invocations. Source PR approvals are not required. Environment existence or manual dispatch alone is not approval. |
| Release version and provenance | Owner-approved committed canonical version matching tag/project/publications; exact peeled source SHA/tree, repository/workflow/run/attempt/artifact/digest identity, successful trusted CI and verified signed subject/signer/source identity. Recheck mutable facts after approval; author-written PASS JSON is not provenance. |
| Complete native outgoing bytes | A separately proven pre-first-write gate for the complete three-module root/Android/JVM/Apple publication set: binaries, sources, POMs, `.module` links and deterministic sidecars, with mutable Maven index semantics handled separately. Preserve native Gradle components and byte identity even for direct single-publication requests; no custom uploader, dropped targets, hash normalization or relabelled rebuilds. |
| Registry and partial releases | Authoritatively unused complete target GAV set, effective serialization against other writers and an explicit non-atomic multi-publication failure policy. HTTP401/403/errors do not prove absence. No overwrite, deletion, retagging, workflow/release-level retry/resume or partial-version reuse is authorized. |
| Completion and consumers | Same-manifest complete remote readback, both real reader contexts resolving exact new GAVs, reviewed final source/dependency delta and joint App/Backend parity acceptance. Missing, partial or unreadable evidence holds activation. |

Gradle9.6.1's native publisher retries individual PUTs and can merely warn on SHA256/SHA512
sidecar upload failures. The protected caller represents those semantics honestly:
there is no blanket no-transport-retry guarantee, and Gradle exit0 is not complete remote
readback. Remote qualification remains incomplete until actual hosted execution and complete
readback succeed. Engine6 remains PARTIAL/open; source changes alone do not close it.

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

### Dormant environment/branch-policy observation

The same adapter provides the programmatic-only
`acquire_environment_configuration(name, environment_id, expected_policies, *, token=None)`.
The caller independently selects the environment and complete policy records (`id`, `name`,
`type`, explicitly `branch` or `tag`). Inputs are copied before GET; 1–100 unique policies,
positive signed-64-bit IDs and bounded printable names are required. It reads the exact
URL-encoded environment, its one-page complete policy list, then the environment again.
Only explicit custom-policy mode is supported; missing/changed/ambiguous metadata, unsupported
modes, pagination and HTTP401/403/404 all refuse. Names/policy types match literally; no glob
interpretation or response-URL following occurs. The existing bounded TLS/GET/token controls
are reused. The result projects observed identity/mode/policies only, not raw response data.

A future StageC pre-first-write caller can reacquire this independently selected publisher
configuration alongside completed-byte intake; current producer receipts do not read it.
There is no new CLI, receipt format or caller, and no workflow consumes this dormant entry
point. Reviewer membership, the self-review setting, administrator bypass and actual run approval
are **not proved**; missing bypass metadata is not false. The policy list is read once:
the last environment read neither revalidates nor freezes that list. Matching observations
are not an atomic snapshot, reservation, authority or release permission. StageC's unconditional
hold, disabled publisher, all-writer/registry and consumer holds remain. The separately selected
prospective candidate version is not a release or registry reservation.
Focused synthetic tests are `test_github_publication_receipts.EnvironmentConfigurationAcquisitionTests`;
their source is not a live API or protection qualification.

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

### Connected protected publisher and read-only credential diagnostic

`publisher_publication_intake.publisher_preflight(...)` and `publisher_recheck(...)`
reuse actual connected candidate intake, acquire fresh publisher/policy observations,
and finally reread local source/archive bytes and existing tag/ancestry proof. Recheck
repeats native verification and official receipt/policy reads before comparing the earlier projection;
it cannot reuse a saved PASS. The independent selection contains `producer`, `publisher`,
`protection`, `app_parity_ref` and `backend_parity_ref`. The publisher is a **separate**
exact repository/run/workflow/ref/SHA tuple: dispatch, attempt 1, currently in progress,
not the completed producer. The trusted `publisher_workflow.py` supplies its own invocation.
Its fixed policy binds integration, environment24588601457, branch-policy63403649,
owner104788132, integration rule `BPR_kwDOTiA_Vs4FEN_x` and `verify`/app15368.
Dispatch supplies the independently selected producer, tag/tip, parity references and finite
`release_window`. Parity references are bounded bindings, not assertions of passing tests.
The selected window contains UTC epoch `not_before`/`expires_at` (at most four hours), the
sole owner ID/login, every other workflow ID to disable, and all15 package ID/name/repository
bindings. Observe these independently; never guess values from an artifact or accept an
`approved=true` field. Disable only the selected competing workflows after production and
consumer verification finish; verify no other nonterminal run, retain the window through
readback, then restore only the settings changed for it. An expired/changed window refuses.

The existing `publish.yml` also offers an explicit `access_check=true` dispatch with
`selection={}`. It exercises the fixed `source-publication` native approval boundary
and bounded read-only requests using explicitly provisioned `KIRA_PUBLICATION_API_READ_TOKEN`
and `KIRA_PACKAGES_READ_TOKEN` repository secrets, never a job-token fallback. A sparse
checkout of the exact workflow commit uses the existing API adapter to check Engine
metadata, administration, contents, run, attestation and environment read ports. The
package token's public account identity and the known `0.1.0` POM are checked with the
actual reader, requiring matching coordinates. Registry credentials never reach the
one permitted HTTPS storage hop. The classic token must explicitly grant `read:packages`,
not `write:packages` or `delete:packages`; the same adapter checks the15 package identities
and a bounded version sample. Only fixed result/error/status/stage facts, granted scope names
and public account/package/POM facts are reported; credential values are not persisted or printed.
Every distinct check is attempted; any failure fails the job. The disposable checkout
is always cleaned up. There are no artifacts, publisher outputs or write permissions,
and publisher jobs are skipped. Success proves these selected read checks passed,
**not** protected publication eligibility, an unused version or package-write authority.
Inspect the actual native approval metadata separately; this diagnostic does not
replace any normal publication gate or grant approval to a later invocation.

The separate `acquire_publisher_policy` API reuses the bounded fixed-host transport.
It requires explicitly selected User reviewer IDs, explicit `prevent_self_review=false` (owner-authorized),
admin-bypass denial, one literal publisher-branch policy and exact protected branch
tips/status-check app identities. The owner-authorized single-owner source policy requires
integer `approvals=0`, observed `required_approving_review_count=0` and literal
`require_last_push_approval=false`; stale-review dismissal remains required. This removes
mandatory external source approval, not legitimate PR/merged-commit lineage, required CI,
admin enforcement or zero-bypass checks. No source approval is fabricated; the separate
named owner deployment approval must still actually occur. Missing proof, unknown rules,
HTTP errors or changed tag/lineage facts refuse; no policy discovery is performed.

Branch selections retain `name`, `tip`, `approvals` and `checks`, with one optional
`bypass_rule_id`: an independently selected 1–256-character non-whitespace printable
ASCII opaque node ID. It is required **only if REST omits**
`bypass_pull_request_allowances`. An explicit REST object must still contain exactly
empty `users`, `teams` and `apps` lists; present null, malformed or nonempty values refuse
without fallback. The existing explicit-empty/anonymous GET path needs no new selection.
When absent, one fixed read-only query POSTs to `https://api.github.com/graphql` for
that exact selected qualified branch ref. Only the same explicitly supplied read token
is used; no ambient credential or saved proof is accepted. The query fixes the Engine
repository and selects its numeric ID, ref and effective protection rule. The response
must match the selected repository ID, full repository name, branch name, rule node ID
and literal branch rule pattern. Closed data, integer zero total count, an empty node
list and `hasNextPage=false` are all required. GraphQL errors (even with partial data),
null/extra fields, wildcard rules and incomplete lists refuse. Every selected branch
is checked independently; recheck reacquires the query rather than reusing a proof.
The same TLS, timeout, 8MiB response, cache/Age and cleanup controls apply, with no
redirects or retries. The integration rule ID is fixed by the current trusted policy; token field visibility
is verified by the actual reads. Synthetic tests are not evidence of live compatibility.

The old three-GET environment observation API is unchanged. These sequential observations
are neither atomic nor native approval: workflow placement behind genuine environment
approval, later writer-boundary rechecks, all-writer/unused-GAV authority, remote Maven
readback and consumer acceptance remain separate. Both full calls require a fresh checkout
before Gradle restoration. Neither API discovers credentials; explicit read tokens only
reach their fixed official read ports, never native verification or Git. The protected caller
runs fresh intake and window admission before authenticated bootstrap/help, and reobserves
the window before restoring the sealed native inputs. The late attachment below does not
replace either fresh call.
Focused synthetic classes are `test_publisher_publication_intake.PublisherIntakeTests` and
`test_github_publication_receipts.PublisherPolicyAcquisitionTests`. The new query boundary
is covered by `test_github_branch_bypass_proof.BranchBypassProofTests` and the caller's
`test_recheck_reacquires_each_bypass_proof_and_binds_selected_rule_ids` method; no live approval follows.

### Guarded native remote writer/readback attachment

`-PkiraPublicationMode=promote-remote` configures the **same fifteen original native writers**
and task identities at the literal `https://maven.pkg.github.com/kira-manga/kira-source-engine`.
Only `publish.yml`'s post-approval native publisher uploads packages. Its non-uploading
preflight/access-check and the isolated metadata probe below cannot invoke that publisher. The immediate
pre-action guard requires fresh window/policy/package observations as well as original source, graph, action and native-byte
checks. Ordinary `publish` containment and the historical `release_holds()` comparison helper
are unchanged; the latter is not an authorization path. Local `export` and `promote` retain
their owned-file, credential-free behavior. Source/model/native-output/action
checks are not relaxed: destination/authentication is separately audited, not inserted into or
stripped from the sealed producer model. A new same-source export is required after gate changes.

The trusted caller runs both fresh native-attested publisher APIs **before** generated roots
exist, then restores only admitted raw files into the fixed input directory. It reuses the
qualified authenticated libffi bootstrap/private native profile and original native Gradle
uploader; it does not rebuild/relabel the admitted binaries or introduce another uploader.
The whole selected package inventory, repository association, active/deleted version lists
and authenticated Maven observations must agree on an unused candidate. A Maven404 alone
is never absence authority. Backend preview or source tests do not establish real package
consumer parity; those qualified inputs remain a separate operational requirement.

The protected branch requires actual hosted Darwin/ARM64, existing Java21/Gradle9.6.1 admission,
no build/configuration cache, no `--continue` or dependency refresh, and the exact whole-cohort producer exclusions.
Only `promote-remote` requires an online Gradle invocation: Gradle's offline transport refuses native
remote uploads. Export and owned-file promotion remain offline. Restore/verify task bodies remain
local-only within the same remote invocation; splitting them would lose the required invocation-local state.
Before subproject evaluation, remote mode registers a fail-closed `incoming.beforeResolve` on every
named project configuration, including later-created configurations. This does not cover detached,
settings or buildscript/plugin resolution and is not a network sandbox. Existing credential-free
online scratch-tooling preparation, pinned plugins/source and exact graph/action/byte gates are unchanged.
The existing small builtin-only Gradle API check exercises online mode and named-configuration refusal
without publishing, dependency repositories or real credentials; actual remote execution remains a separate gate.
Keep the already admitted archive plus `{ "selection": ..., "preflight": ..., "window": ... }` comparison in
`$RUNNER_TEMP/kira-publisher-<publisher-run-id>-1/{kira-publication-bytes.tar,comparison.json}`:
same-owner directory0700/files0600, no symlinks, outside the checkout. Supply the independently
retained comparison-file hash as `kiraPublisherRecordSha256`, alongside the existing seal hash.
That file is comparison data, **not proof that the required fresh calls/approval occurred**.

Only explicit `KIRA_MAVEN_WRITE_USERNAME`/`KIRA_MAVEN_WRITE_TOKEN` populate native
PasswordCredentials/Basic. No repository-name credential discovery or token fallback exists.
Separate `KIRA_PUBLICATION_API_READ_TOKEN` and `KIRA_MAVEN_READ_USERNAME`/`KIRA_MAVEN_READ_TOKEN`
are sent to the fixed helper through bounded stdin, never argv or files. Read tokens equal to
the write token refuse. The helper uses fixed `/usr/bin/python3`, a cleared environment,
owned temporary HOME/TMPDIR, no user Python site, deadline/worker termination and owned cleanup.
On Darwin only, the CLI discards the observed system-launcher additions `CPATH`, `LIBRARY_PATH`,
`MANPATH`, `SDKROOT` and `__CF_USER_TEXT_ENCODING` after argument validation, before reading stdin.
Their values are never used. They are not added to the strict helper environment allowlist;
every other unexpected name still refuses, and these five names also refuse on other platforms.
Before handing over read custody, credential-free fixed `/usr/bin/git ls-tree` binds the five
helper/import files to the selected producer commit. Only the exact verified bytes run from a
private temporary snapshot; mutable checkout imports, Git replacement/lazy-fetch/protocols,
global/system configuration and inherited credentials are excluded. The copied policy still verifies
the original checkout; the fixed local Git lookup disables hooks and filesystem monitoring.
No write token reaches that helper, its Git subprocesses or its read-only HTTP adapters.
These names select custody channels, not operational credential authority or proven token scopes.

The controller separately requires `KIRA_PACKAGES_METADATA_AUDIT_TOKEN`, provisioned as
an **environment secret in `source-publication`**, not an alias of the API, payload or native
writer token. Both preflight and recheck sit behind that actual environment boundary;
only their controller steps receive this credential. The selected profile is a dedicated
classic token whose real response scopes include `read:packages` and `delete:packages`,
exclude `write:packages`, and remain identical across observations. No `repo` scope is
required by this validator; any additional reported scopes are retained honestly. Missing
scope metadata or a different token class refuses, with no fallback to a job/write token.
This credential is deletion-capable even though our adapter exposes only GET and no deletion
is authorized. It never enters Gradle/native/helper environment, stdin, argv, files or logs.

Classic read-only custody still performs package inventory, all fifteen active-version GETs
and every Maven payload/index read, with its unchanged no-write/no-delete scope validator.
The controller alone performs all fifteen fixed deleted-version GETs. Each registry receipt
retains `oauth_scopes` for the payload channel and separate `deleted_audit` profile/scopes;
both are validated and compared with the original window and previous writer boundary.
An injected observation is not an approval, and a missing audit never means an empty list.

During native execution the existing controller stdout selector also services one fixed
Unix-socket operation under the owned0700 publisher root (socket0600). The request contains
only the selection hash and a fresh nonce, not a credential, URL or package selector. The
copied native helper derives that socket from its already admitted archive location, checks
owner/type/mode and response binding, and validates the complete fifteen-package result.
There are at most sixteen native observations, with512-byte requests,512KiB responses and
bounded socket/API/native deadlines. No daemon/thread or new process is introduced; every
outcome closes/unlinks the owned socket and follows the existing native worker-stop path.
This is a same-UID private runner boundary, not protection from a compromised host. Existing
HTTPS DNS resolution still has no separate hard deadline. New helper bytes require a new
genuine producer/seal and actual platform qualification, not overlaying an old cohort.
The existing Darwin CI runs the focused IPC and native CLI classes before bootstrap, retaining their log;
the latter launches the real system interpreter with a clean environment and malformed `{}` input,
requiring request-schema refusal rather than startup refusal, with network/subprocess activity forbidden.
The IPC class's disposable test-only roots use resolved `/tmp` so the100-byte production socket bound
is not weakened to accommodate a long runner TMPDIR. This is not live credential proof.

At each writer the helper rereads source/archive, fresh official producer/publisher/protection
facts and local lineage, comparing the original preflight. It intentionally uses generated-root-
aware source comparison **only alongside** this invocation's restore/verify and the original
whole-output/model/graph/action checks, which repeat after HTTP immediately before the native action.
It also rechecks the finite window, competing workflows/runs and all15 package version lists.
First it observes all15 existing artifact indexes with mandatory MD5/SHA1 checksums and
SHA256/SHA512 checksums when present. Missing/ambiguous indexes
refuse; a new artifact without an existing index needs separately proved authority/handling.
At later boundaries only newly successful writers' immutable payloads/sidecars are read once;
all indexes remain fresh, previously verified indexes must be byte-identical, and pending indexes
must remain at their pre-write state. Progress lives only in the serialized invocation service,
not a saved receipt/cache. This avoids quadratic payload downloads while retaining the still-required
global writer exclusion. The finalizer deliberately ignores progress and rereads every immutable row.

Remote GETs use the fixed TLS host/path and at most one already-qualified credential-free
registry-storage hop; no retry/304/content decoding, bounded time/bytes
and content-free failures. Every payload and deterministic sidecar must match admitted size/SHA256.
Mutable index XML is separately bounded/strict: retain prior version order, append the candidate
once, check coordinates/latest/timestamp and any present release=candidate. A genuinely absent
release is recorded as None, never synthesized; an existing release cannot disappear. Only the
exact mutable-index SHA256/SHA512 paths may be absent at the authenticated registry's first-hop404;
storage-hop404, permission failures and malformed/mismatched checksums still refuse. Present
checksums retain the same qualified storage hop and exact-byte verification. MD5/SHA1 are
supplemental checks, not authentication; first-read trust remains fixed-host authenticated TLS
and the independent registry/window admission. SHA256 computed over **actual** XML bytes binds
pending/progress continuity; it is not an independent first-read attestation.
It is not a sealed or expected timestamp/index. Partial/failing writers still fail final completion;
once a native action started, the finalizer attempts bounded readback, never cleanup/overwrite/retry
of remote state. Native Gradle itself can retry individual PUTs or warn on some checksum failures;
its unchanged action is not atomic publication. Pinned native remote behavior and the fixed
Darwin interpreter/process boundary still require actual hosted qualification.

The caller captures bounded credential-redacted native logs, stops only its owned Gradle
workers immediately, then finalizes provenance before uploading diagnostics (three-day
retention). Only after evidence capture does receipt-gated cleanup remove its owned outputs.
Native output and the controller-held metadata audit share one fixed35-minute monotonic
deadline for all fifteen guarded writers and final readback. Existing per-helper/HTTP/IPC
bounds and the outer42-minute work step/55-minute job remain unchanged. Deadline checks
are cooperative during a bounded synchronous audit group; this is not a worst-case promise
that every slow request and cleanup fits. The selected finite release window remains required.
A failed/partial upload is never retried, overwritten, deleted or reported as no writes;
complete readback is required even when Gradle exits0. A finite window is not a claim that
an authorized administrator is technically unable to change their own repository.

Focused offline tests (synthetic Git/native/API/HTTP and source assertions, **not Gradle execution**):

```bash
PYTHONPATH=.github/scripts python3 -B -m unittest -v \
  test_publisher_publication_intake.PublisherWriterBoundaryTests \
  test_publisher_publication_intake.NativeAttachmentSourceTests \
  test_remote_maven_readback.RemoteMavenReadbackTests
```

### Isolated classic metadata-audit access check

The owner authorized a separate metadata-audit capability on 2026-10-18: GitHub's deleted-version
GET requires deletion authority, which the independent classic payload token must not receive.
`publish.yml`'s `metadata_audit_check=true` mode validates the production adapter using the
explicit **deletion-capable classic** `KIRA_PACKAGES_METADATA_AUDIT_TOKEN` environment secret,
distinct from the existing classic payload-read token. Its separate Ubuntu job sits behind
the same named `source-publication` approval (initiator approval allowed, admin bypass disabled).
The job token has no package-write permission and is never a credential fallback. The real
audit response must prove `read:packages` plus `delete:packages`, without `write:packages`;
effective access and token grants are not assumed from the secret's name or YAML.
It must not run concurrently with a publication window; shared workflow concurrency serializes
this workflow, but does not exclude other/out-of-band writers.

The probe is mutually exclusive with `access_check` and publication. It checks out only three
reviewed read helpers at the exact protected workflow SHA without persisting credentials, and
performs at most16 fixed-host GETs: the independent payload token reads the bounded package
inventory, then the separate audit token reads deleted versions for exactly the fifteen validated
Engine package names. Both secrets are removed from the process environment before helper imports;
no actor/account query is added. No producer checkout/build, custom action, mutation request,
approval receipt, output or artifact feeds the publisher. Failures
remain failures, including 403, pagination, incomplete or malformed data. Only sanitized status
and validated package/version identities are logged; the helper checkout is always removed.

This credential is **not read-only** merely because this code only calls GET. No deletion is
authorized. The classic payload-reader no-write/no-delete scope validator stays unchanged.
This diagnostic does not establish live production metadata isolation, full continuous
registry observations, writer exclusion, or publication eligibility. The separate controller
route above still requires a legitimate distinct credential, security review and actual
qualification; probe success cannot mint or substitute that credential. A one-time snapshot
must not substitute for the required fresh before/after-write observations.
