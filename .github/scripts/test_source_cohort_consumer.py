"""Synthetic extraction-boundary tests only; no native/consumer/network qualification."""

import hashlib
import io
import json
from pathlib import Path
import tarfile
from unittest import mock
import xml.etree.ElementTree as ET

import source_cohort_consumer as caller
import publication_policy as policy
import test_publication_policy as fixtures


class SourceCohortConsumerTests(fixtures.OwnedFixture):
    def setUp(self):
        super().setUp()
        # Reuse the existing synthetic sealed-format fixture, never substitute it
        # into a Gradle repository or represent it as the selected real cohort.
        self.metadata, payloads, self.source_files = fixtures.bundle_fixture()
        self.payloads = payloads
        self.files = dict(payloads)
        for name, data in payloads.items():
            for algorithm in policy.ALGORITHMS:
                self.files[name + "." + algorithm] = hashlib.new(algorithm, data, usedforsecurity=False).hexdigest().encode()
        self.files["me/manga/kira/source/source-engine/maven-metadata.xml"] = b"synthetic native writer metadata\n"
        self.manifest = {name: {"size": len(data), "sha256": policy.digest(data)} for name, data in self.files.items()}
        self.entries = sorted(self.files.items())

    def archive(self, label, entries=None, format=tarfile.USTAR_FORMAT):
        path = self.root / (label + ".tar")
        with tarfile.open(path, "w", format=format) as output:
            for name, data in (self.entries if entries is None else entries):
                member = name if isinstance(name, tarfile.TarInfo) else tarfile.TarInfo(name)
                member.size = len(data)
                output.addfile(member, io.BytesIO(data))
        return path, self.record(path)

    def record(self, path):
        return {"file": path.name, "files": len(self.manifest), "size": path.stat().st_size, "sha256": policy.digest(path.read_bytes())}

    def test_complete_inventory_and_extra_native_writer_files_survive_exact_extraction(self):
        archive, selected = self.archive("complete")
        target = self.root / "maven"
        actual = caller.owned_tar(archive, self.manifest, selected)
        self.assertFalse(target.exists(), "Full preflight must not materialize any Maven bytes")
        checked = policy.check_bundle(self.metadata, {name: actual[name] for name in self.payloads},
                                      fixtures.RELEASE, self.source_files)
        self.assertEqual(checked["inventory_sha256"], policy.digest(self.metadata["inventory.tsv"]))
        self.assertEqual(caller.owned_tar(archive, self.manifest, selected, target), actual)
        self.assertEqual(caller.owned.tree_hashes(target), self.manifest)
        for line in policy.text_lines(self.metadata["inventory.tsv"]):
            row = line.split("\t")
            self.assertEqual(self.manifest[row[6]], {"size": int(row[7]), "sha256": row[8]})
        self.assertEqual((target / "me/manga/kira/source/source-engine/maven-metadata.xml").read_bytes(),
                         b"synthetic native writer metadata\n")

    def test_missing_duplicate_extra_and_reordered_members_fail_even_with_matching_tar_digest(self):
        variants = {
            "missing": self.entries[:-1],
            "duplicate": [self.entries[0], *self.entries],
            "extra": self.entries + [("me/manga/kira/source/extra.txt", b"unexpected")],
            "reordered": [self.entries[1], self.entries[0], *self.entries[2:]],
        }
        for label, entries in variants.items():
            with self.subTest(label=label):
                path, selected = self.archive(label, entries)
                with self.assertRaises(policy.Refusal):
                    caller.owned_tar(path, self.manifest, selected)
        self.assertFalse((self.root / "maven").exists())

    def test_links_devices_directories_and_implicit_extension_headers_are_refused(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.CHRTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.XHDTYPE):
            with self.subTest(kind=kind):
                member = tarfile.TarInfo(self.entries[0][0])
                member.type = kind
                if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    member.linkname = "../../outside"
                path, selected = self.archive("type-" + kind.hex(), [(member, self.entries[0][1]), *self.entries[1:]])
                with self.assertRaises(policy.Refusal):
                    caller.owned_tar(path, self.manifest, selected)
        member = tarfile.TarInfo(self.entries[0][0])
        member.pax_headers = {"comment": "hidden extension"}
        path, selected = self.archive("pax", [(member, self.entries[0][1]), *self.entries[1:]], format=tarfile.PAX_FORMAT)
        with self.assertRaises(policy.Refusal):
            caller.owned_tar(path, self.manifest, selected)

    def test_unsafe_manifest_paths_and_existing_or_symlink_destination_do_not_write(self):
        archive, selected = self.archive("paths")
        for name in ("../escape", "/absolute", "me/manga/kira/source/../escape", "me\\manga\\escape", "other/group/file"):
            with self.subTest(name=name), self.assertRaises(policy.Refusal):
                bad = dict(self.manifest)
                bad[name] = bad.pop(self.entries[0][0])
                caller.owned_tar(archive, bad, selected, self.root / "maven")
        self.assertFalse((self.root / "maven").exists())
        target = self.root / "existing"
        target.mkdir()
        sentinel = target / "sentinel"
        sentinel.write_text("untouched")
        with self.assertRaises(policy.Refusal):
            caller.owned_tar(archive, self.manifest, selected, target)
        link = self.root / "redirected"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(policy.Refusal):
            caller.owned_tar(archive, self.manifest, selected, link)
        self.assertEqual(sentinel.read_text(), "untouched")

    def test_member_hash_size_and_checksum_sidecar_changes_fail_before_extraction(self):
        for name in (self.entries[0][0], next(name for name in self.files if name.endswith(".sha256"))):
            data = self.files[name]
            changed = [(key, b"!" + value[1:] if key == name else value) for key, value in self.entries]
            path, selected = self.archive("changed-" + str(len(name)), changed)
            with self.assertRaises(policy.Refusal):
                caller.owned_tar(path, self.manifest, selected)
            self.assertEqual(len(data), len(dict(changed)[name]))
        archive, selected = self.archive("size")
        manifest = {key: dict(value) for key, value in self.manifest.items()}
        manifest[self.entries[0][0]]["size"] += 1
        with self.assertRaises(policy.Refusal):
            caller.owned_tar(archive, manifest, selected)

    def test_trailing_data_invalid_padding_and_changed_archive_identity_are_refused(self):
        for label, change in (
            ("trailing", lambda data: data + b"!" + bytes(511)),
            ("padding", lambda data: data[:512 + len(self.entries[0][1])] + b"!" + data[513 + len(self.entries[0][1]):]),
            ("truncated", lambda data: data[:-513]),
        ):
            with self.subTest(label=label):
                path, _ = self.archive(label)
                path.write_bytes(change(path.read_bytes()))
                with self.assertRaises(policy.Refusal):
                    caller.owned_tar(path, self.manifest, self.record(path))
        path, selected = self.archive("identity")
        selected["sha256"] = "0" * 64
        with self.assertRaises(policy.Refusal):
            caller.owned_tar(path, self.manifest, selected)

    def test_dispatch_cannot_replace_the_independently_selected_real_promotion(self):
        workflow = Path(__file__).parents[1] / "workflows/source-cohort-consumer-check.yml"
        selected = json.loads(next(line.strip() for line in workflow.read_text().splitlines() if line.startswith('    {"')))
        self.assertEqual(caller.selection(selected), selected)
        for changed in ({}, selected | {"run_attempt": 2}, selected | {"artifact_id": selected["artifact_id"] + 1},
                        selected | {"owned_maven": selected["owned_maven"] | {"files": 419}},
                        selected | {"producer": selected["producer"] | {"inventory_sha256": "0" * 64}}):
            with self.subTest(changed=tuple(changed)), self.assertRaises(policy.Refusal):
                caller.selection(changed)

    def test_fixture_refusal_keeps_cleanup_independent_without_crossing_worker_barrier(self):
        for worker_fails in (False, True):
            with self.subTest(worker_fails=worker_fails):
                root = self.root / ("worker-fails" if worker_fails else "fixture-fails")
                root.mkdir(mode=0o700)
                for name in ("app", "backend", "diagnostics"):
                    (root / name).mkdir(mode=0o700)
                job = caller.common.Runtime(self.root / "control", self.root / "app-source", root, {}, "apple")
                caller.owned.write_json(root / "owner.json", job.owner())
                events = []

                def stop(job, label, env):
                    events.append(label + "-workers")
                    if worker_fails and label == "app":
                        raise policy.Refusal("synthetic owned worker refusal")

                def fixture(*args):
                    events.append("fixture-cleanup")
                    raise policy.Refusal("synthetic missing fixture socket evidence")

                with mock.patch.object(caller, "stop_owned", side_effect=stop), \
                        mock.patch.object(caller, "fixture", side_effect=fixture), \
                        mock.patch.object(caller, "native_cleanup", side_effect=lambda *args: events.append("native-bootstrap")):
                    with self.assertRaises(policy.Refusal):
                        caller.finalize(job, {})
                expected = ["app-workers", "backend-workers", "fixture-cleanup"]
                self.assertEqual(events, expected if worker_fails else expected + ["native-bootstrap"])
                failures = ["app-workers", "fixture-cleanup", "native-blocked-by-app-workers"] if worker_fails else ["fixture-cleanup"]
                self.assertEqual(json.loads((root / "diagnostics/finalize-result.json").read_text()), {"failures": failures})
                self.assertFalse((root / "diagnostics/cleanup-ready.json").exists())

    def test_native_user_home_keeps_owned_caches_and_excludes_ambient_credentials(self):
        workflow = Path(__file__).parents[1] / "workflows/source-cohort-consumer-check.yml"
        selected = json.loads(next(line.strip() for line in workflow.read_text().splitlines() if line.startswith('    {"')))
        caller.owned.write_json(self.root / "selection.json", selected)
        env = {"HOME": "/Users/runner", "JAVA_HOME": "/tool/java", "KONAN_DATA_DIR": str(self.root / "konan"),
               "GRADLE_OPTS": "-Dkonan.data.dir=" + str(self.root / "konan"),
               "GITHUB_TOKEN": "synthetic-not-a-credential", "JAVA_TOOL_OPTIONS": "synthetic-ambient-override"}
        for phase in ("apple", "linux"):
            with self.subTest(phase=phase):
                job = caller.common.Runtime(self.root / "control", self.root / "app-source", self.root, {}, phase)
                child = caller.child_environment(job, "app", env)
                self.assertEqual(child["HOME"], env["HOME"] if phase == "apple" else str(self.root / "app/home"))
                self.assertEqual(child["GRADLE_USER_HOME"], str(self.root / "app/gradle"))
                self.assertEqual(child["TMPDIR"], str(self.root / "app/tmp"))
                self.assertNotIn("GITHUB_TOKEN", child)
                self.assertNotIn("JAVA_TOOL_OPTIONS", child)
                if phase == "apple":
                    self.assertEqual(child["KONAN_DATA_DIR"], env["KONAN_DATA_DIR"])

    def test_xml_uses_case_identity_for_jvm_display_and_native_task_names(self):
        for task, name, display, prefix, suffix in (
            ("desktopTest", caller.ENGINE_PREFIX + "DefaultSourceConfigValidatorTest", "DefaultSourceConfigValidatorTest[desktop]", "", "[desktop]"),
            ("testAndroidHostTest", caller.ANDROID_TRANSPORT, "AndroidGenericSingleHopTest[android]", "", "[android]"),
            ("iosSimulatorArm64Test", caller.IOS_TRANSPORT, "iosSimulatorArm64Test." + caller.IOS_TRANSPORT, "iosSimulatorArm64Test.", "[iosSimulatorArm64]"),
        ):
            with self.subTest(task=task):
                methods = sorted(caller.TRANSPORT_METHODS) if name in (caller.ANDROID_TRANSPORT, caller.IOS_TRANSPORT) else ["validDocument"]
                suite = ET.Element("testsuite", name=display, tests=str(len(methods)), failures="0", errors="0", skipped="0")
                for method in methods:
                    ET.SubElement(suite, "testcase", classname=prefix + name, name=method + suffix)
                self.assertTrue(self.xml_result(task, name, suite)["valid"])
                suite[0].set("classname", "another.task." + name)
                self.assertFalse(self.xml_result(task, name, suite)["valid"])

    def test_xml_refuses_missing_mixed_duplicate_skipped_or_false_success(self):
        name = caller.IOS_TRANSPORT
        for variant in ("missing", "mixed", "duplicate", "skipped", "failure", "counts", "wrong-method"):
            with self.subTest(variant=variant):
                suite = ET.Element("testsuite", name=name, tests="3", failures="0", errors="0", skipped="0")
                for method in sorted(caller.TRANSPORT_METHODS):
                    ET.SubElement(suite, "testcase", classname="iosSimulatorArm64Test." + name, name=method + "[iosSimulatorArm64]")
                if variant == "missing":
                    suite.remove(suite[0])
                    suite.set("tests", "2")
                elif variant == "mixed":
                    suite[0].set("classname", caller.ANDROID_TRANSPORT)
                elif variant == "duplicate":
                    suite[0].set("name", suite[1].get("name"))
                elif variant in ("skipped", "failure"):
                    ET.SubElement(suite[0], variant)
                elif variant == "counts":
                    suite.set("failures", "1")
                else:
                    suite[0].set("name", "unrelatedTest[iosSimulatorArm64]")
                self.assertFalse(self.xml_result("iosSimulatorArm64Test", name, suite)["valid"])

    def xml_result(self, task, name, suite):
        root = self.root / ("xml-" + str(len(list(self.root.glob("xml-*")))))
        directory = "build/test-results/" + task
        (root / "source" / directory).mkdir(parents=True)
        (root / "diagnostics").mkdir()
        ET.ElementTree(suite).write(root / "source" / directory / ("TEST-" + name + ".xml"))
        job = caller.common.Runtime(self.root / "control", root / "source", root, {}, "apple")
        return caller.capture_xml(job, "app", root / "source", {directory: {name}})
