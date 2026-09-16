"""Synthetic policy fixtures plus tiny local Git lineage cases; no network, Gradle, gh or provenance."""

import copy
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

import publication_policy as p


SHA = "a" * 40
TREE = "b" * 40
RELEASE = "1.2.3"
NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)


def context():
    return {"GITHUB_REPOSITORY": p.REPOSITORY, "GITHUB_EVENT_NAME": "push", "GITHUB_REF": p.REF,
            "GITHUB_SHA": SHA, "GITHUB_WORKFLOW_SHA": SHA, "GITHUB_WORKFLOW_REF": p.WORKFLOW_REF,
            "GITHUB_RUN_ID": "31", "GITHUB_RUN_ATTEMPT": "2"}


def expected():
    return {"repository": p.REPOSITORY, "ref": p.REF, "source_sha": SHA, "source_tree": TREE,
            "workflow_ref": p.WORKFLOW_REF, "workflow_sha": SHA, "workflow_sha256": "c" * 64,
            "version": RELEASE, "run_id": 31, "run_attempt": 2, "workflow_id": 17,
            "artifact_id": 42, "producer_job_id": 51, "attestation_job_id": 52,
            "archive_sha256": "d" * 64, "inventory_sha256": "e" * 64, "seal_sha256": "f" * 64,
            "transport_sha256": "0" * 64}


def receipts():
    e = expected()
    run = {"id": 31, "run_attempt": 2, "workflow_id": 17, "head_sha": SHA, "head_branch": p.BRANCH,
           "event": "push", "path": p.WORKFLOW, "status": "completed", "conclusion": "success",
           "repository": {"full_name": p.REPOSITORY}, "head_repository": {"full_name": p.REPOSITORY}}
    jobs = [{"id": job_id, "name": name, "run_id": 31, "run_attempt": 2, "head_sha": SHA,
             "status": "completed", "conclusion": "success"} for job_id, name in ((51, "verify"), (52, "attest"))]
    artifact = {"id": 42, "name": p.artifact_name(31, 2), "digest": "sha256:" + e["transport_sha256"],
                "expired": False, "expires_at": "2099-01-01T00:00:00Z", "size_in_bytes": 123,
                "url": f"https://api.github.com/repos/{p.REPOSITORY}/actions/artifacts/42",
                "workflow_run": {"id": 31, "head_sha": SHA, "head_branch": p.BRANCH}}
    return {"attempt": copy.deepcopy(run), "current": copy.deepcopy(run),
            "jobs": {"total_count": 2, "jobs": jobs}, "artifact": artifact}


def native_output(e=None):
    e = e or expected()
    certificate = {"issuer": p.ISSUER, "subjectAlternativeName": p.SIGNER, "buildSignerURI": p.SIGNER,
                   "buildSignerDigest": SHA, "buildConfigURI": p.SIGNER, "buildConfigDigest": SHA,
                   "runnerEnvironment": "github-hosted", "sourceRepositoryURI": p.REPO_URL,
                   "sourceRepositoryDigest": SHA, "sourceRepositoryRef": p.REF,
                   "sourceRepositoryOwnerURI": "https://github.com/kira-manga", "buildTrigger": "push",
                   "runInvocationURI": f"{p.REPO_URL}/actions/runs/31/attempts/2"}
    return [{"attestation": {"bundle": {"fixture": "NOT a real signature"}}, "verificationResult": {
        "mediaType": "application/vnd.dev.sigstore.verificationresult+json;version=0.1",
        "signature": {"certificate": certificate},
        "verifiedTimestamps": [{"type": "Tlog", "uri": "https://example.invalid/test-only", "timestamp": "2026-09-13T00:00:00Z"}],
        "statement": {"_type": "https://in-toto.io/Statement/v1", "predicateType": p.PREDICATE,
                      "subject": [{"name": p.ARCHIVE, "digest": {"sha256": e["archive_sha256"]}},
                                  {"name": "inventory.tsv", "digest": {"sha256": e["inventory_sha256"]}}],
                      "predicate": {"fixture": "untrusted workflow-controlled metadata"}}}}]


def source_bytes(version=RELEASE):
    names = p.SOURCE_INPUTS | {p.WORKFLOW} | {m + "/src/commonMain/Fixture.kt" for m in p.MODULES}
    data = {n: ("test-only " + n).encode() for n in names}
    data["gradle.properties"] = f"VERSION_NAME={version}\n".encode()
    return data


def reseal(metadata):
    metadata["seal.sha256"] = "".join(f"{p.digest(metadata[n])}  {n}\n" for n in sorted(p.METADATA - {"seal.sha256"})).encode()


def bundle_fixture():
    inputs = source_bytes()
    files = {n: {"size": len(b), "sha256": p.digest(b), "mode": "100644"} for n, b in inputs.items()}
    model = [f"source\t{n}\t{files[n]['size']}\t{files[n]['sha256']}" for n in p.selected_inputs(files)]
    model.append("tool\tgradle\t9.6.1")
    payloads, inventory, producers = {}, [], set()
    for module in p.MODULES:
        for publication, suffix in p.PUBLICATIONS.items():
            artifact = module + suffix
            identity = [":" + module, publication, p.GROUP, artifact, RELEASE]
            binary = "klib" if publication.startswith("ios") else "aar" if publication == "android" else "jar"
            for kind, classifier, extension in (("artifact", "", binary), ("sources", "sources", "jar"),
                                                ("pom", "", "pom"), ("module", "", "module")):
                filename = f"{artifact}-{RELEASE}" + ("-" + classifier if classifier else "") + "." + extension
                path = p.GROUP.replace(".", "/") + f"/{artifact}/{RELEASE}/{filename}"
                data = f"TEST ONLY {module} {publication} {kind}\n".encode()
                payloads[path] = data
                task = f":{module}:fixture{publication}{kind}"
                producers.add(task)
                model.append("\t".join(["output", *identity, kind, classifier, extension, path,
                                        f"{module}/build/fixture/{filename}", task]))
                inventory.append(identity + [kind, path, str(len(data)), p.digest(data)])
                for algorithm in p.ALGORITHMS:
                    sidecar = hashlib.new(algorithm, data, usedforsecurity=False).hexdigest().encode()
                    inventory.append(identity + ["checksum:" + algorithm, path + "." + algorithm,
                                                 str(len(sidecar)), p.digest(sidecar)])
    metadata = {"inventory.tsv": "".join("\t".join(r) + "\n" for r in sorted(inventory, key=lambda r: r[6])).encode(),
                "model.tsv": ("\n".join(sorted(model)) + "\n").encode(),
                "producers.txt": ("\n".join(sorted(producers)) + "\n").encode()}
    reseal(metadata)
    return metadata, payloads, files


class OwnedFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="kira-policy-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def archive(self, metadata=None, payloads=None, extra=(), format=tarfile.USTAR_FORMAT):
        default_meta, default_payloads, files = bundle_fixture()
        metadata = default_meta if metadata is None else metadata
        payloads = default_payloads if payloads is None else payloads
        entries = list(sorted(metadata.items())) + [("payloads/" + n, b) for n, b in sorted(payloads.items())]
        entries.extend(extra)
        path = self.root / p.ARCHIVE
        with tarfile.open(path, "w", format=format) as archive:
            for name, data in entries:
                info = name if isinstance(name, tarfile.TarInfo) else tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return path, files


class VersionEligibilityTests(unittest.TestCase):
    def test_canonical_and_snapshot_are_distinct(self):
        for version in ("0.0.0", "1.2.3", "100.22.333", "0.1.0-SNAPSHOT"):
            with self.subTest(version=version):
                self.assertEqual(p.committed_version(f"VERSION_NAME={version}\n".encode()), version)
                self.assertEqual(p.candidate_eligible(context(), version, SHA), not version.endswith("SNAPSHOT"))

    def test_malformed_ambiguous_duplicate_and_override_versions(self):
        bad = [b"", b"VERSION_NAME=01.2.3", b"VERSION_NAME=1.2", b"VERSION_NAME=1.2.3-rc1",
               b"VERSION_NAME=1.2.3+build", b"VERSION_NAME:1.2.3", b"VERSION_NAME 1.2.3",
               b"VERSION_NAME=1.2.3\nVERSION_NAME=1.2.3", b"VERSION_NAME=1.2.3 # not a value",
               b"VERSION_NAME=1.2.3\\\n", b"VERSION_\\u004eAME=1.2.3", b"VERSION_NAME=1.2.3\x00"]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(p.Refusal):
                p.committed_version(data)
        with self.assertRaises(p.Refusal):
            p.committed_version(b"VERSION_NAME=1.2.3", overrides=["-PVERSION_NAME=1.2.3"])
        for key in ("VERSION_NAME", "ORG_GRADLE_PROJECT_VERSION_NAME", "JAVA_OPTS", "GRADLE_OPTS", "JAVA_TOOL_OPTIONS"):
            with self.subTest(key=key), self.assertRaises(p.Refusal):
                p.check_environment({key: ""})

    def test_exact_tag_only_is_a_comparison_not_a_ci_trigger(self):
        p.matching_tag(RELEASE, "v" + RELEASE)
        for version, tag in ((RELEASE, RELEASE), (RELEASE, "v1.2.4"), ("1.2.3-SNAPSHOT", "v1.2.3-SNAPSHOT")):
            with self.subTest(tag=tag), self.assertRaises(p.Refusal):
                p.matching_tag(version, tag)

    def test_exact_push_and_source_workflow_identities(self):
        self.assertTrue(p.candidate_eligible(context(), RELEASE, SHA))
        for key, values in {
            "GITHUB_EVENT_NAME": ("pull_request", "pull_request_target", "workflow_dispatch", "workflow_run"),
            "GITHUB_REPOSITORY": ("fork/kira-source-engine", "kira-manga/another", "Kira-manga/kira-source-engine"),
            "GITHUB_REF": ("refs/heads/main", "refs/heads/feature/test", "refs/tags/v1.2.3", p.REF + "-extra"),
            "GITHUB_SHA": ("b" * 40, ""), "GITHUB_WORKFLOW_SHA": ("b" * 40, ""),
            "GITHUB_WORKFLOW_REF": (p.WORKFLOW_REF.replace("ci.yml", "publish.yml"), p.WORKFLOW_REF + "-extra", ""),
        }.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    c = context()
                    c[key] = value
                    self.assertFalse(p.candidate_eligible(c, RELEASE, SHA))


class SourceAgreementTests(OwnedFixture):
    def checkout(self, version=RELEASE):
        root = self.root / "checkout"
        root.mkdir()
        entries = []
        for name, data in sorted(source_bytes(version).items()):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o644)
            blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False).hexdigest()
            entries.append(f"100644 blob {blob}\t{name}".encode())

        def fake_git(_root, *args):
            if args == ("rev-parse", "HEAD"):
                return (SHA + "\n").encode()
            if args == ("rev-parse", SHA + "^{tree}"):
                return (TREE + "\n").encode()
            if args[0] == "diff":
                return b""
            if args[0] == "ls-tree":
                return b"\0".join(entries) + b"\0"
            self.fail("Unexpected Git adapter call")

        patcher = mock.patch.object(p, "git", side_effect=fake_git)
        patcher.start()
        self.addCleanup(patcher.stop)
        return root

    def test_every_committed_blob_and_exact_generated_roots(self):
        root = self.checkout()
        before = p.source_snapshot(root, SHA, fresh=True)
        self.assertEqual(before["source_tree"], TREE)
        self.assertEqual(set(before["files"]), set(source_bytes()))
        for generated in p.GENERATED:
            path = root / generated / "owned-output"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test-generated")
        self.assertEqual(before, p.source_snapshot(root, SHA))
        with self.assertRaises(p.Refusal):
            p.source_snapshot(root, SHA, fresh=True)

    def test_snapshot_cli_is_check_only_and_pack_refuses(self):
        root = self.checkout("0.1.0-SNAPSHOT")
        state, output = self.root / "before.json", self.root / "github-output.txt"
        env = {**context(), "GITHUB_OUTPUT": str(output)}
        argv = ["policy", "--root", str(root), "ci-start", "--state", str(state)]
        with mock.patch.dict(p.os.environ, env, clear=True), mock.patch.object(p.sys, "argv", argv), redirect_stdout(io.StringIO()):
            p.main()
        self.assertIn("candidate=false\n", output.read_text())
        self.assertFalse(json.loads(state.read_bytes())["candidate"])
        argv = ["policy", "--root", str(root), "pack", "--state", str(state), "--output", str(self.root / p.ARCHIVE)]
        with mock.patch.dict(p.os.environ, env, clear=True), mock.patch.object(p.sys, "argv", argv), \
             mock.patch.object(p, "archive_bundle") as pack:
            with self.assertRaises(p.Refusal):
                p.main()
            pack.assert_not_called()

    def test_mutation_deletion_mode_and_unexpected_input_refuse(self):
        root = self.checkout()
        workflow = root / p.WORKFLOW
        original = workflow.read_bytes()
        workflow.write_bytes(original + b"changed")
        with self.assertRaises(p.Refusal):
            p.source_snapshot(root, SHA)
        workflow.write_bytes(original)
        workflow.chmod(0o755)
        with self.assertRaises(p.Refusal):
            p.source_snapshot(root, SHA)
        workflow.chmod(0o644)
        for name in ("local.properties", ".github/added.yml", "source-engine/src/build/hidden.gradle.kts"):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("uncommitted")
            with self.subTest(name=name), self.assertRaises(p.Refusal):
                p.source_snapshot(root, SHA)
            path.unlink()
            # Keep cleanup owned; remove only newly empty directories below this checkout.
            parent = path.parent
            while parent != root and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
        workflow.unlink()
        with self.assertRaises(FileNotFoundError):
            p.source_snapshot(root, SHA)

    def test_wrong_checkout_and_source_symlink(self):
        root = self.checkout()
        with self.assertRaises(p.Refusal):
            p.source_snapshot(root, "0" * 40)
        path = root / "gradle.properties"
        path.unlink()
        path.symlink_to(root / "build.gradle.kts")
        with self.assertRaises(p.Refusal):
            p.source_snapshot(root, SHA)


class SdkTests(OwnedFixture):
    def sdk(self, layout="android-37", api="37", xml_api=None):
        root = self.root / "sdk"
        directory = root / "platforms" / layout
        directory.mkdir(parents=True)
        (directory / "android.jar").write_bytes(b"test-only jar")
        (directory / "source.properties").write_text(f"AndroidVersion.ApiLevel={api}\nAndroidVersion.CodeName=\n")
        (directory / "package.xml").write_text(
            f'<repository><localPackage path="platforms;{layout}"><type-details>'
            f'<api-level>{xml_api or api}</api-level></type-details></localPackage></repository>')
        return root, directory

    def test_both_real_api37_metadata_layouts(self):
        for layout, api in (("android-37", "37"), ("android-37.0", "37.0")):
            root, _ = self.sdk(layout, api)
            self.assertEqual(p.existing_sdk37({"ANDROID_HOME": str(root), "ANDROID_SDK_ROOT": str(root)}), str(root))

    def test_equivalent_api37_metadata_numbers(self):
        root, _ = self.sdk("android-37.0", "37.0", "37")
        self.assertEqual(p.existing_sdk37({"ANDROID_HOME": str(root)}), str(root))

    def test_missing_jar_wrong_api_preview_and_inconsistent_roots(self):
        root, directory = self.sdk()
        other = self.root / "other"
        other.mkdir()
        for env in ({}, {"ANDROID_HOME": str(root), "ANDROID_SDK_ROOT": str(other)}):
            with self.subTest(env=env), self.assertRaises(p.Refusal):
                p.existing_sdk37(env)
        prop = directory / "source.properties"
        for data in ("AndroidVersion.ApiLevel=36\n", "AndroidVersion.ApiLevel=37.1\n",
                     "AndroidVersion.ApiLevel=37\nAndroidVersion.CodeName=Preview\n",
                     "AndroidVersion.ApiLevel=37\nAndroidVersion.PreviewSdkInt=1\n",
                     "AndroidVersion.ApiLevel=37\nAndroidVersion.ApiLevel=37\n"):
            prop.write_text(data)
            with self.subTest(data=data), self.assertRaises(p.Refusal):
                p.existing_sdk37({"ANDROID_HOME": str(root)})
        prop.write_text("AndroidVersion.ApiLevel=37\n")
        (directory / "android.jar").unlink()
        with self.assertRaises(FileNotFoundError):
            p.existing_sdk37({"ANDROID_HOME": str(root)})

    def test_xml_disagreement_and_no_platform_alias(self):
        root, directory = self.sdk("android-37.0", "37.0", "36")
        with self.assertRaises(p.Refusal):
            p.existing_sdk37({"ANDROID_HOME": str(root)})
        (directory / "package.xml").unlink()
        (root / "platforms/android-37").symlink_to(directory, target_is_directory=True)
        with self.assertRaises(p.Refusal):
            p.existing_sdk37({"ANDROID_HOME": str(root)})


class ArchiveTests(OwnedFixture):
    def test_archive_exact_set_hashes_seals_and_no_extraction(self):
        path, files = self.archive()
        measured, metadata = p.archive_intake(path, RELEASE, files)
        self.assertEqual(measured, {"archive_sha256": p.digest(path.read_bytes()),
                                    "inventory_sha256": p.digest(metadata["inventory.tsv"]),
                                    "seal_sha256": p.digest(metadata["seal.sha256"])})
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_pack_once_without_changing_bundle_bytes(self):
        metadata, payloads, files = bundle_fixture()
        bundle = self.root / "bundle"
        data = {**metadata, **{"payloads/" + k: v for k, v in payloads.items()}}
        for name, value in data.items():
            path = bundle / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        archive = self.root / p.ARCHIVE
        result = p.archive_bundle(bundle, archive, RELEASE, files)
        self.assertEqual(result, p.archive_intake(archive, RELEASE, files)[0])
        self.assertEqual({n: (bundle / n).read_bytes() for n in data}, data)
        with self.assertRaises(FileExistsError):
            p.archive_bundle(bundle, archive, RELEASE, files)
        (bundle / "unexpected-empty-directory").mkdir()
        with self.assertRaises(p.Refusal):
            p.archive_bundle(bundle, self.root / "other.tar", RELEASE, files)

    def test_raw_missing_extra_changed_and_sidecar_materialization(self):
        metadata, original, _ = bundle_fixture()
        first = next(iter(original))
        variants = []
        for kind in ("missing", "extra", "changed", "sidecar", "maven-index"):
            data = original.copy()
            if kind == "missing":
                del data[first]
            elif kind == "changed":
                data[first] += b"changed"
            else:
                name = {"extra": "extra.jar", "sidecar": first + ".sha256", "maven-index": "me/maven-metadata.xml"}[kind]
                data[name] = b"not an original raw payload"
            variants.append((kind, data))
        for kind, payloads in variants:
            path, files = self.archive(metadata, payloads)
            with self.subTest(kind=kind), self.assertRaises(p.Refusal):
                p.archive_intake(path, RELEASE, files)

    def test_inventory_model_source_and_seal_disagreement(self):
        original, payloads, _ = bundle_fixture()
        for kind in ("seal", "checksum", "source", "output", "producers", "duplicate-inventory", "missing-metadata"):
            metadata = original.copy()
            if kind == "seal":
                metadata["seal.sha256"] = b"0" + metadata["seal.sha256"][1:]
            elif kind == "checksum":
                rows = metadata["inventory.tsv"].decode().splitlines()
                row = rows[1].split("\t")
                row[8] = "0" * 64
                rows[1] = "\t".join(row)
                metadata["inventory.tsv"] = ("\n".join(rows) + "\n").encode()
            elif kind in ("source", "output"):
                lines = metadata["model.tsv"].decode().splitlines()
                lines.remove(next(line for line in lines if line.startswith(kind + "\t")))
                metadata["model.tsv"] = ("\n".join(lines) + "\n").encode()
            elif kind == "producers":
                metadata["producers.txt"] = b":source-engine:notARealProducer\n"
            elif kind == "duplicate-inventory":
                metadata["inventory.tsv"] += metadata["inventory.tsv"].splitlines(keepends=True)[0]
            if kind != "seal":
                reseal(metadata)
            if kind == "missing-metadata":
                del metadata["model.tsv"]
            path, files = self.archive(metadata, payloads)
            with self.subTest(kind=kind), self.assertRaises(p.Refusal):
                p.archive_intake(path, RELEASE, files)
        path, files = self.archive()
        with self.assertRaises(p.Refusal):
            p.archive_intake(path, "1.2.4", files)
        changed = copy.deepcopy(files)
        changed["gradle.properties"]["sha256"] = "0" * 64
        with self.assertRaises(p.Refusal):
            p.archive_intake(path, RELEASE, changed)

    def test_traversal_duplicates_links_devices_extensions_and_trailing_data(self):
        metadata, payloads, _ = bundle_fixture()
        extras = [(name, b"unsafe") for name in ("../escape", "/absolute", "payloads/../escape", "C:/escape", "payloads\\escape", "extra.txt")]
        extras.append(("inventory.tsv", metadata["inventory.tsv"]))
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.DIRTYPE, tarfile.XHDTYPE):
            member = tarfile.TarInfo("payloads/unsafe")
            member.type = kind
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                member.linkname = "../outside"
            extras.append((member, b""))
        for extra in extras:
            path, files = self.archive(metadata, payloads, [extra])
            with self.subTest(extra=extra[0]), self.assertRaises((p.Refusal, tarfile.TarError)):
                p.archive_intake(path, RELEASE, files)
        path, files = self.archive()
        path.write_bytes(path.read_bytes() + b"nonzero trailing data")
        with self.assertRaises(p.Refusal):
            p.archive_intake(path, RELEASE, files)
        self.assertFalse((self.root / "escape").exists())


class ReceiptTests(unittest.TestCase):
    def compare(self, data, e=None):
        p.compare_official_receipts(e or expected(), data["attempt"], data["current"], data["jobs"], data["artifact"], NOW)

    def test_completed_exact_run_attempt_jobs_and_artifact(self):
        self.compare(receipts())

    def test_failed_in_progress_stale_superseded_and_wrong_identity(self):
        for part, key, value in (
            ("attempt", "status", "in_progress"), ("attempt", "conclusion", "failure"),
            ("current", "status", "queued"), ("current", "run_attempt", 3), ("attempt", "run_attempt", 1),
            ("attempt", "head_sha", "0" * 40), ("current", "id", 30), ("attempt", "head_branch", "main"),
            ("attempt", "event", "workflow_dispatch"), ("attempt", "path", ".github/workflows/publish.yml"),
            ("attempt", "workflow_id", 18), ("attempt", "repository", {"full_name": "fork/repo"}),
            ("current", "head_repository", {"full_name": "fork/repo"}),
        ):
            data = receipts()
            data[part][key] = value
            with self.subTest(part=part, key=key, value=value), self.assertRaises(p.Refusal):
                self.compare(data)
        for key, value in (("id", 99), ("run_attempt", 1), ("status", "in_progress"), ("conclusion", "skipped"), ("head_sha", "0" * 40)):
            data = receipts()
            data["jobs"]["jobs"][0][key] = value
            with self.subTest(job=key), self.assertRaises(p.Refusal):
                self.compare(data)
        data = receipts()
        data["jobs"]["total_count"] = 3
        with self.assertRaises(p.Refusal):
            self.compare(data)

    def test_missing_expired_replaced_wrong_run_and_transport_digest(self):
        for key, value in (("id", 43), ("id", None), ("expired", True), ("expired", 0),
                           ("expires_at", "2000-01-01T00:00:00Z"), ("name", p.artifact_name(31, 1)),
                           ("digest", "sha256:" + expected()["archive_sha256"]), ("digest", ""),
                           ("url", "https://api.github.com/repos/fork/repo/actions/artifacts/42"),
                           ("workflow_run", {"id": 32, "head_sha": SHA, "head_branch": p.BRANCH})):
            data = receipts()
            data["artifact"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(p.Refusal):
                self.compare(data)
        data = receipts()
        data["artifact"] = {}
        with self.assertRaises(p.Refusal):
            self.compare(data)


class NativePolicyTests(unittest.TestCase):
    def test_documented_native_shape_not_nested_extensions(self):
        p._verified_policy(native_output(), expected())
        document = native_output()
        result = document[0]["verificationResult"]
        result["signature"]["certificate"] = {"extensions": result["signature"]["certificate"]}
        with self.assertRaises(p.Refusal):
            p._verified_policy(document, expected())

    def test_signer_source_invocation_and_verified_timestamps(self):
        fields = ("issuer", "subjectAlternativeName", "buildSignerURI", "buildSignerDigest", "buildConfigURI",
                  "buildConfigDigest", "runnerEnvironment", "sourceRepositoryURI", "sourceRepositoryDigest",
                  "sourceRepositoryRef", "sourceRepositoryOwnerURI", "buildTrigger", "runInvocationURI")
        for field in fields:
            document = native_output()
            document[0]["verificationResult"]["signature"]["certificate"][field] = "mismatch"
            with self.subTest(field=field), self.assertRaises(p.Refusal):
                p._verified_policy(document, expected())
        for field, value in (("signature", {}), ("verifiedTimestamps", []), ("mediaType", "unknown")):
            document = native_output()
            document[0]["verificationResult"][field] = value
            with self.subTest(field=field), self.assertRaises(p.Refusal):
                p._verified_policy(document, expected())

    def test_no_absent_decoded_dsse_boolean_or_subject_fallback(self):
        for document in ([], {"verified": True}, {"attested": True}, {"payload": "decoded DSSE"}, [native_output()[0]] * 2):
            with self.subTest(document=document), self.assertRaises(p.Refusal):
                p._verified_policy(document, expected())
        for key, value in (("name", "renamed.tar"), ("digest", {"sha256": "0" * 64}),
                           ("digest", {"sha512": "0" * 128})):
            document = native_output()
            document[0]["verificationResult"]["statement"]["subject"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(p.Refusal):
                p._verified_policy(document, expected())
        with self.assertRaises(p.Refusal):
            p.json_data(b'{"verified":true,"verified":true}')


class NativeAdapterAndIntakeTests(OwnedFixture):
    def test_native_adapter_pin_arguments_success_before_policy_and_exit_zero_refusal(self):
        # NOT an acquired or executable gh: subprocess is mocked for the entire adapter call.
        asset = self.root / p.GH_ASSET
        with tarfile.open(asset, "w:gz") as archive:
            member = tarfile.TarInfo(p.GH_MEMBER)
            data = b"synthetic test-only native executable placeholder"
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        bundle = self.root / "fixture.json"
        bundle.write_text('{"fixture":"not a signed bundle"}')
        subject = self.root / p.ARCHIVE
        subject.write_bytes(b"fixture")
        with mock.patch.object(p.subprocess, "run") as execute:
            with self.assertRaises(p.Refusal):
                p.native_verify(asset, bundle, [subject], expected())
            execute.assert_not_called()
        with mock.patch.object(p, "GH_ASSET_SHA256", p.digest(asset.read_bytes())), \
             mock.patch.object(p.platform, "system", return_value="Linux"), \
             mock.patch.object(p.platform, "machine", return_value="x86_64"), \
             mock.patch.object(p.subprocess, "run") as execute:
            execute.return_value = subprocess.CompletedProcess([], 0, json.dumps(native_output()).encode(), b"")
            p.native_verify(asset, bundle, [subject], expected())
            command = execute.call_args.args[0]
            self.assertEqual(command[1:3], ["attestation", "verify"])
            for flag, value in (("--repo", p.REPOSITORY), ("--cert-identity", p.SIGNER),
                                ("--source-ref", p.REF), ("--source-digest", SHA), ("--signer-digest", SHA)):
                self.assertEqual(command[command.index(flag) + 1], value)
            self.assertIn("--deny-self-hosted-runners", command)
            self.assertTrue(execute.call_args.kwargs["check"])
            self.assertNotIn("GH_TOKEN", execute.call_args.kwargs["env"])
            execute.return_value = subprocess.CompletedProcess([], 0, b'{"verified":true}', b"")
            with self.assertRaises(p.Refusal):
                p.native_verify(asset, bundle, [subject], expected())
            execute.side_effect = subprocess.CalledProcessError(1, ["mock native verifier"])
            with mock.patch.object(p, "_verified_policy") as policy:
                with self.assertRaises(subprocess.CalledProcessError):
                    p.native_verify(asset, bundle, [subject], expected())
                policy.assert_not_called()

    def test_completed_intake_requires_native_and_independent_digests_and_committed_version(self):
        archive, files = self.archive()
        checkout = self.root / "checkout"
        checkout.mkdir()
        (checkout / "gradle.properties").write_bytes(source_bytes()["gradle.properties"])
        e = expected()
        measured, _ = p.archive_intake(archive, RELEASE, files)
        e.update(measured)
        source = {"source_sha": SHA, "source_tree": TREE, "workflow_sha256": e["workflow_sha256"], "files": files}
        with mock.patch.object(p, "source_snapshot", return_value=source), mock.patch.object(p, "native_verify") as native:
            p.completed_intake(checkout, e, receipts(), archive, self.root / "not-acquired-gh", self.root / "not-signed.json")
            native.assert_called_once()
            wrong = {**e, "archive_sha256": "9" * 64}
            with self.assertRaises(p.Refusal):
                p.completed_intake(checkout, wrong, receipts(), archive, "unused", "unused")
            native.side_effect = p.Refusal("native verification absent")
            with self.assertRaises(p.Refusal):
                p.completed_intake(checkout, e, receipts(), archive, "unused", "unused")
            (checkout / "gradle.properties").write_text("VERSION_NAME=0.1.0-SNAPSHOT\n")
            with self.assertRaises(p.Refusal):
                p.completed_intake(checkout, e, receipts(), archive, "unused", "unused")


class FiniteHoldTests(unittest.TestCase):
    def test_complete_tuple_readback_and_partial_gav_never_create_authority(self):
        # Opaque references stand for separately authenticated Stage C facts, NOT approvals here.
        admission = {field: "reviewed-test-reference:" + field for field in p.APPROVAL_FIELDS}
        admission.update(version=RELEASE, tag="v" + RELEASE, run_id=31, run_attempt=2, artifact_id=42,
                         subjects={p.ARCHIVE: "d" * 64, "inventory.tsv": "e" * 64})
        readback = {"complete-test-only-path": [12, "a" * 64]}
        self.assertEqual(p.equality_issues(admission, admission.copy(), p.APPROVAL_FIELDS), ())
        self.assertEqual(p.release_holds(admission, admission, readback, readback),
                         ("stage-c-authority-and-live-rechecks-required",))
        for field in sorted(p.APPROVAL_FIELDS):
            changed = admission.copy()
            del changed[field]
            with self.subTest(missing=field):
                self.assertIn("missing:" + field, p.release_holds(admission, changed, readback, readback))
            changed[field] = "different"
            with self.subTest(mismatch=field):
                self.assertIn("mismatch:" + field, p.release_holds(admission, changed, readback, readback))
        self.assertIn("unexpected:approved", p.release_holds(admission, {"approved": True}, readback, readback))
        for actual in (None, {}, {"extra": [12, "a" * 64]}, {"complete-test-only-path": [13, "a" * 64]}):
            with self.subTest(readback=actual):
                self.assertTrue(any(i.startswith("readback:") for i in p.release_holds(admission, admission, readback, actual)))
        holds = p.release_holds(admission, admission, readback, readback, used_gavs=["one-of-fifteen"], rerun=True)
        self.assertIn("used-or-partial-gav-cohort", holds)
        self.assertIn("rerun-hold", holds)
        self.assertIn("complete-readback-expectation-missing", p.release_holds(admission, admission, {}, {}))


class ReleaseLineageGitTests(OwnedFixture):
    """Real local object/ref operations, not mocked ancestry or protected-origin proof."""

    def setUp(self):
        super().setUp()
        self.repo = self.root / "repository"
        self.repo.mkdir()
        self.template = self.root / "empty-template"
        self.template.mkdir()
        self.git_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.git_env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                            GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="",
                            GIT_AUTHOR_DATE="2026-09-16T00:00:00Z", GIT_COMMITTER_DATE="2026-09-16T00:00:00Z")
        self.git("init", "--quiet", "--template=" + str(self.template), "--initial-branch=" + p.BRANCH)
        (self.repo / "gradle.properties").write_text("VERSION_NAME=" + RELEASE + "\n")
        self.source = self.commit("release fixture")
        self.tree = self.git("rev-parse", self.source + "^{tree}")
        (self.repo / "README.md").write_text("descendant fixture\n")
        self.tip = self.commit("campaign descendant")
        self.tag = "v" + RELEASE
        self.tag_ref = "refs/tags/" + self.tag
        self.git("tag", "--no-sign", self.tag, self.source)
        self.facts = {"version": RELEASE, "tag": self.tag, "source_sha": self.source,
                      "source_tree": self.tree, "tag_object": self.source, "branch_tip": self.tip}

    def git(self, *args, root=None):
        command = ["git", "--no-replace-objects", "-c", "core.hooksPath=" + str(self.root / "no-hooks"),
                   "-c", "commit.gpgSign=false", "-c", "tag.gpgSign=false",
                   "-c", "user.name=Kira lineage fixture", "-c", "user.email=lineage@example.invalid",
                   "-C", str(root or self.repo), *args]
        return subprocess.run(command, env=self.git_env, check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=5).stdout.decode().strip()

    def commit(self, message):
        self.git("add", "--all")
        self.git("commit", "--quiet", "-m", message)
        return self.git("rev-parse", "HEAD")

    def test_lightweight_and_annotated_tags_and_cli(self):
        observed = p.release_lineage(self.repo, **self.facts)
        self.assertEqual(observed["source_sha"], self.source)
        self.assertEqual(observed["walked_commits"], 2)
        self.assertEqual(observed["hold"], "stage-c-authority-and-live-rechecks-required")
        self.assertNotIn("approved", observed)
        self.git("tag", "--delete", self.tag)
        self.git("tag", "--no-sign", "--annotate", self.tag, "--message", "unsigned fixture", self.source)
        self.facts["tag_object"] = self.git("rev-parse", self.tag_ref)
        self.assertNotEqual(self.facts["tag_object"], self.source)
        argv = ["publication_policy.py", "--root", str(self.repo), "lineage"]
        for key, value in self.facts.items():
            argv.extend(["--" + key.replace("_", "-"), value])
        output = io.StringIO()
        with mock.patch.object(p.sys, "argv", argv), redirect_stdout(output):
            p.main()
        observed = json.loads(output.getvalue())
        self.assertEqual(observed["tag_object"], self.facts["tag_object"])
        self.assertEqual(observed["scope"], "local-git-lineage-observation-only")
        self.assertIn("Not a reservation", observed["notice"])

    def test_invalid_arguments_and_wrong_selected_objects(self):
        invalid = [{"source_sha": "HEAD"}, {"source_tree": "A" * 40}, {"tag_object": "a" * 39},
                   {"branch_tip": "--all"}, {"version": "1" * 65}, {"tag": "v1.2.3/extra"}]
        for changed in invalid:
            with self.subTest(changed=changed), mock.patch.object(p.subprocess, "run") as execute:
                with self.assertRaises(p.Refusal):
                    p.release_lineage(self.repo, **(self.facts | changed))
                execute.assert_not_called()
        for changed in ({"source_sha": self.tip}, {"source_tree": self.git("rev-parse", self.tip + "^{tree}")},
                        {"tag_object": self.tip}, {"branch_tip": self.source}):
            with self.subTest(changed=changed), self.assertRaises(p.Refusal):
                p.release_lineage(self.repo, **(self.facts | changed))
        self.git("tag", "--force", "--no-sign", self.tag, self.tree)
        with self.assertRaisesRegex(p.Refusal, "Tag must peel to a commit"):
            p.release_lineage(self.repo, **(self.facts | {"tag_object": self.tree}))

    def test_committed_version_not_worktree_or_environment(self):
        (self.repo / "gradle.properties").write_text("VERSION_NAME=9.9.9\n")
        self.assertEqual(p.release_lineage(self.repo, **self.facts)["version"], RELEASE)
        self.git("tag", "--no-sign", "v1.2.4", self.source)
        with self.assertRaisesRegex(p.Refusal, "Committed version/tag mismatch"):
            p.release_lineage(self.repo, **(self.facts | {"version": "1.2.4", "tag": "v1.2.4"}))
        (self.repo / "gradle.properties").write_text("VERSION_NAME=0.1.0-SNAPSHOT\n")
        snapshot = self.commit("committed snapshot")
        self.git("tag", "--force", "--no-sign", self.tag, snapshot)
        facts = self.facts | {"source_sha": snapshot, "source_tree": self.git("rev-parse", snapshot + "^{tree}"),
                              "tag_object": snapshot, "branch_tip": snapshot}
        (self.repo / "gradle.properties").write_text("VERSION_NAME=" + RELEASE + "\n")
        with mock.patch.dict(os.environ, {"VERSION_NAME": RELEASE, "ORG_GRADLE_PROJECT_VERSION_NAME": RELEASE}):
            with self.assertRaisesRegex(p.Refusal, "Committed version/tag mismatch"):
                p.release_lineage(self.repo, **facts)

    def test_equal_tree_divergence_is_not_ancestry(self):
        unrelated = self.git("commit-tree", self.tree, "-m", "unrelated root with identical tree")
        self.assertEqual(self.git("rev-parse", unrelated + "^{tree}"), self.tree)
        self.git("update-ref", p.REF, unrelated)
        with self.assertRaisesRegex(p.Refusal, "Git lineage read refused"):
            p.release_lineage(self.repo, **(self.facts | {"branch_tip": unrelated}))

    def test_missing_and_moved_refs(self):
        for ref, original, changed in ((self.tag_ref, self.source, self.tip), (p.REF, self.tip, self.source)):
            with self.subTest(ref=ref):
                self.git("update-ref", "-d", ref)
                with self.assertRaises(p.Refusal):
                    p.release_lineage(self.repo, **self.facts)
                self.git("update-ref", ref, changed)
                with self.assertRaisesRegex(p.Refusal, "Missing/moved"):
                    p.release_lineage(self.repo, **self.facts)
                self.git("update-ref", ref, original)

    def test_ref_movement_during_observation(self):
        execute = subprocess.run
        for ref, original, changed in ((self.tag_ref, self.source, self.tip), (p.REF, self.tip, self.source)):
            moved = []

            def move_after_real_ancestry(command, **kwargs):
                result = execute(command, **kwargs)
                if command[-4:] == ["merge-base", "--is-ancestor", self.source, self.tip] and not moved:
                    execute(["git", "-C", str(self.repo), "update-ref", ref, changed], env=self.git_env,
                            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
                    moved.append(ref)
                return result

            with self.subTest(ref=ref), mock.patch.object(p.subprocess, "run", side_effect=move_after_real_ancestry):
                with self.assertRaisesRegex(p.Refusal, "Refs moved during lineage observation"):
                    p.release_lineage(self.repo, **self.facts)
            self.assertEqual(moved, [ref])
            self.git("update-ref", ref, original)

    def test_shallow_grafted_replaced_alternate_and_promisor_history(self):
        for relative, data in (("shallow", self.tip + "\n"), ("info/grafts", self.tip + "\n"),
                               ("objects/info/alternates", str(self.repo / ".git/objects") + "\n")):
            path = self.repo / ".git" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data)
            try:
                with self.subTest(history=relative), self.assertRaisesRegex(p.Refusal, "Shallow|Grafted/shallow/alternate"):
                    p.release_lineage(self.repo, **self.facts)
            finally:
                path.unlink()
        replacement = "refs/replace/" + self.tip
        self.git("update-ref", replacement, self.source)
        try:
            with self.assertRaisesRegex(p.Refusal, "Replacement refs refused"):
                p.release_lineage(self.repo, **self.facts)
        finally:
            self.git("update-ref", "-d", replacement)
        marker = self.repo / ".git/objects/pack" / ("pack-" + self.source + ".promisor")
        marker.touch()
        try:
            with self.assertRaisesRegex(p.Refusal, "Promisor pack markers refused"):
                p.release_lineage(self.repo, **self.facts)
        finally:
            marker.unlink()
        for key in ("remote.fixture.promisor", "extensions.partialClone"):
            self.git("config", key, "true" if key.endswith("promisor") else "fixture")
            try:
                with self.subTest(config=key), self.assertRaisesRegex(p.Refusal, "Partial/promisor"):
                    p.release_lineage(self.repo, **self.facts)
            finally:
                self.git("config", "--unset", key)

    def test_missing_parent_with_source_equal_tip(self):
        self.git("tag", "--force", "--no-sign", self.tag, self.tip)
        facts = self.facts | {"source_sha": self.tip, "source_tree": self.git("rev-parse", self.tip + "^{tree}"),
                              "tag_object": self.tip}
        parent_object = self.repo / ".git/objects" / self.source[:2] / self.source[2:]
        self.assertTrue(parent_object.is_file())
        parent_object.unlink()  # Only this disposable fixture's known loose parent object.
        with self.assertRaisesRegex(p.Refusal, "Git lineage read refused"):
            p.release_lineage(self.repo, **facts)

    def test_git_environment_cannot_redirect_or_rewrite_graph(self):
        foreign = self.root / "foreign"
        foreign.mkdir()
        self.git("init", "--quiet", "--template=" + str(self.template), root=foreign)
        graft = self.root / "foreign-graft"
        graft.write_text(self.tip + "\n")
        config = self.root / "foreign-config"
        config.write_text("[core]\n\tbare = true\n")
        injected = {"GIT_DIR": str(foreign / ".git"), "GIT_WORK_TREE": str(foreign),
                    "GIT_COMMON_DIR": str(foreign / ".git"), "GIT_OBJECT_DIRECTORY": str(foreign / ".git/objects"),
                    "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(foreign / ".git/objects"), "GIT_NAMESPACE": "foreign",
                    "GIT_GRAFT_FILE": str(graft), "GIT_SHALLOW_FILE": str(graft), "GIT_REPLACE_REF_BASE": "refs/foreign/",
                    "GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_SYSTEM": str(config), "GIT_CONFIG_NOSYSTEM": "0",
                    "GIT_CONFIG_PARAMETERS": "'core.bare'='true'", "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "core.worktree", "GIT_CONFIG_VALUE_0": str(foreign),
                    "GIT_NO_REPLACE_OBJECTS": "0", "GIT_NO_LAZY_FETCH": "0", "GIT_ALLOW_PROTOCOL": "file"}
        with mock.patch.dict(os.environ, injected):
            observed = p.release_lineage(self.repo, **self.facts)
        self.assertEqual(observed["source_sha"], self.source)
        self.assertEqual(observed["branch_tip"], self.tip)


if __name__ == "__main__":
    unittest.main()
