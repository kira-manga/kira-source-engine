"""Synthetic caller checks only: no real native signatures, Gradle, network or publication."""

import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import unittest
from unittest import mock

import owned_file_promotion as caller
import publication_policy as policy
import publisher_workflow as common
import test_publication_policy as fixtures


SELECTED_SHA, SELECTED_RUN = caller.PRODUCER_SHA, caller.PRODUCER_RUN


class OwnedFilePromotionTests(fixtures.OwnedFixture):
    def setUp(self):
        super().setUp()
        self.bundle, self.payloads, files = fixtures.bundle_fixture()
        self.tools = {"java.runtime.version": "21.0.12.1+1-LTS", "java.vendor": "Eclipse Adoptium",
                      "os.name": "Mac OS X", "os.arch": "aarch64"}
        model = policy.text_lines(self.bundle["model.tsv"]) + [f"tool\t{k}\t{v}" for k, v in self.tools.items()]
        self.bundle["model.tsv"] = ("\n".join(sorted(model)) + "\n").encode()
        fixtures.reseal(self.bundle)
        self.archive, _ = self.archive(self.bundle, self.payloads)
        self.producer = fixtures.SourceAgreementTests.checkout(self)
        self.control = self.root / "control"
        shutil.copytree(self.producer, self.control)
        measured, _ = policy.archive_intake(self.archive, fixtures.RELEASE, files)
        self.selected = fixtures.expected() | measured | {"run_attempt": 1, "workflow_sha256": files[policy.WORKFLOW]["sha256"]}
        for name, value in (("PRODUCER_SHA", fixtures.SHA), ("PRODUCER_RUN", 31)):
            patch = mock.patch.object(caller, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.context = fixtures.context() | {"GITHUB_REPOSITORY_ID": "1310736214", "GITHUB_RUN_ID": "51",
                                            "GITHUB_RUN_ATTEMPT": "1", "GITHUB_REF": caller.CONTROL_REF,
                                            "GITHUB_WORKFLOW_REF": policy.REPOSITORY + "/" + caller.WORKFLOW + "@" + caller.CONTROL_REF}
        self.job = common.Runtime(self.control, self.producer, self.root / "owned", self.context, "owned-file")
        self.job.root.mkdir(mode=0o700)
        caller.write_json(self.job.root / "owner.json", self.job.owner())
        for name in ("artifact", "diagnostics", "gradle", "home", "tmp"):
            (self.job.root / name).mkdir(mode=0o700)
        caller.write_json(self.job.root / "selection.json", self.selected)
        shutil.copyfile(self.archive, self.job.root / "artifact" / policy.ARCHIVE)
        common.write_private(self.job.root / policy.GH_DARWIN_ASSET, b"synthetic; not a native verifier")
        self.env = {"JAVA_HOME": "/fixture/jdk", "GITHUB_TOKEN": "not-for-children",
                    "KIRA_INTAKE_READ_TOKEN": "also-not-for-children", "ORG_GRADLE_PROJECT_FAKE": "not-for-children",
                    "JAVA_TOOL_OPTIONS": "not-for-children", "PATH": "/untrusted", "HOME": "/untrusted"}

    def admitted_fixture(self):
        # Synthetic fixture setup, not an assertion of genuine completed intake.
        (self.job.root / "artifact" / policy.ARCHIVE).rename(self.job.root / policy.ARCHIVE)
        caller.write_json(self.job.root / "diagnostics/intake.json", self.selected)

    def test_selection_is_exact_first_attempt_and_rejects_missing_extra_or_other_cohort(self):
        self.assertEqual(caller.selection(self.selected), self.selected)
        for changed in ({}, self.selected | {"approval": True}, self.selected | {"run_attempt": 2},
                        self.selected | {"source_sha": "e" * 40}, self.selected | {"run_id": 32},
                        self.selected | {"artifact_id": True}, self.selected | {"archive_sha256": "unknown"}):
            with self.subTest(changed=tuple(changed)), self.assertRaises(policy.Refusal):
                caller.selection(changed)

    def test_control_context_requires_exact_repository_ref_workflow_host_and_attempt(self):
        workspace, temp = self.root / "workspace", self.root / "temp"
        workspace.mkdir()
        temp.mkdir()
        env = self.context | {"GITHUB_WORKSPACE": str(workspace), "RUNNER_TEMP": str(temp),
                              "GITHUB_ACTIONS": "true", "RUNNER_OS": "macOS", "RUNNER_ARCH": "ARM64"}
        with mock.patch.object(caller.platform, "system", return_value="Darwin"), \
                mock.patch.object(caller.platform, "machine", return_value="arm64"):
            self.assertEqual(caller.runtime(env).producer, workspace / "producer")
            for key, value in (("GITHUB_REF", policy.REF), ("GITHUB_REPOSITORY_ID", "1"),
                               ("GITHUB_WORKFLOW_SHA", "e" * 40), ("GITHUB_EVENT_NAME", "pull_request"),
                               ("GITHUB_RUN_ATTEMPT", "2"), ("RUNNER_ARCH", "X64")):
                with self.subTest(key=key), self.assertRaises(policy.Refusal):
                    caller.runtime(env | {key: value})

    def test_real_connected_adapter_precedes_intake_record_and_never_stages_source(self):
        token = "synthetic_read_only"

        def connected(root, selected, archive, asset, bundle, **kwargs):
            self.assertEqual((root, selected, archive), (self.producer, self.selected, self.job.root / policy.ARCHIVE))
            self.assertEqual(kwargs, {"token": token})
            self.assertFalse((self.producer / "build").exists())
            self.assertFalse((self.job.root / "diagnostics/intake.json").exists())
            self.assertEqual(bundle.read_bytes(), b'{"fixture":"not a signature"}')

        with mock.patch.object(caller.receipts, "acquire_attestation_bundle", return_value=b'{"fixture":"not a signature"}') as bundle, \
                mock.patch.object(caller.connected, "connected_intake", side_effect=connected) as intake:
            caller.acquire(self.job, self.selected, token)
        bundle.assert_called_once_with(self.selected, token=token)
        intake.assert_called_once()
        self.assertEqual(caller.metadata(self.job, self.selected), self.bundle)
        self.assertFalse((self.producer / "build").exists())

    def test_connected_refusal_leaves_no_admission_or_generated_root(self):
        with mock.patch.object(caller.receipts, "acquire_attestation_bundle", return_value=b"{}"), \
                mock.patch.object(caller.connected, "connected_intake", side_effect=policy.Refusal("fixture")):
            with self.assertRaises(policy.Refusal):
                caller.acquire(self.job, self.selected, None)
        self.assertFalse((self.job.root / "diagnostics/intake.json").exists())
        self.assertFalse((self.producer / "build").exists())

    def test_existing_raw_stager_and_local_command_keep_every_original_exclusion(self):
        self.admitted_fixture()
        bundle = common.stage_raw(self.job, {"producer": self.selected}, self.job.root / policy.ARCHIVE)
        command = caller.promotion_command(self.job, self.selected, bundle)
        exclusions = [command[index + 1] for index, part in enumerate(command) if part == "-x"]
        self.assertEqual(exclusions, policy.text_lines(self.bundle["producers.txt"]))
        self.assertIn("--offline", command)
        self.assertIn("-PkiraPublicationMode=promote", command)
        self.assertIn("-PkiraPublicationSealSha256=" + self.selected["seal_sha256"], command)
        self.assertEqual(command[-1], ":promoteKiraPublicationBytes")
        self.assertNotIn("promote-remote", " ".join(command))
        self.assertFalse(any("VERSION_NAME" in part or "MavenLocal" in part or "init-script" in part for part in command))
        for line in policy.text_lines(bundle["model.tsv"]):
            row = line.split("\t")
            if row[0] == "output":
                self.assertFalse((self.producer / row[10]).exists())
        for bad in (b":compile\n:compile\n", b":compile;echo\n", b"\n"):
            with self.subTest(bad=bad), self.assertRaises(policy.Refusal):
                caller.promotion_command(self.job, self.selected, bundle | {"producers.txt": bad})

    def test_runtime_binding_refuses_same_major_different_patch_or_vendor(self):
        self.admitted_fixture()

        def output(properties):
            return subprocess.CompletedProcess([], 0, ("\n".join(f"    {key} = {value}" for key, value in properties.items())).encode())

        with mock.patch.object(policy, "existing_sdk37"), mock.patch.object(caller.subprocess, "run", return_value=output(self.tools)) as run:
            self.assertEqual(caller.toolchain(self.job, self.selected, self.env), self.tools)
            self.assertNotIn("GITHUB_TOKEN", run.call_args.kwargs["env"])
            for changes in ({"java.runtime.version": "21.0.12+1-LTS"}, {"java.vendor": "Another vendor"}, {"os.arch": "x86_64"}):
                run.return_value = output(self.tools | changes)
                with self.subTest(changes=changes), self.assertRaises(policy.Refusal):
                    caller.toolchain(self.job, self.selected, self.env)

    def test_tooling_is_help_only_in_distinct_scratch_then_discards_its_histories(self):
        scratch = self.producer.with_name("tooling")
        shutil.copytree(self.producer, scratch)
        with mock.patch.object(caller, "profile"), mock.patch.object(caller, "toolchain", return_value=self.tools), \
                mock.patch.object(caller, "run_gradle") as run:
            caller.warmup(self.job, self.selected, self.env)
        command, cwd, label = run.call_args.args[2:]
        self.assertEqual((command[0], command[-1], cwd, label), (str(scratch / "gradlew"), "help", scratch, "tooling"))
        self.assertFalse(any("kiraPublicationMode" in part or "publish" in part or "check" == part for part in command))
        self.assertFalse(scratch.exists())
        self.assertTrue(self.producer.is_dir())
        self.assertFalse((self.producer / "build").exists())

    def test_owned_gradle_stop_uses_only_its_launcher_and_credential_free_environment(self):
        launcher = self.job.root / "gradle/wrapper/dists/gradle-9.6.1-bin/fixture/gradle-9.6.1/bin/gradle"
        launcher.parent.mkdir(parents=True)
        launcher.write_bytes(b"not executed")
        with mock.patch.object(caller.subprocess, "run") as run:
            caller.stop_owned(self.job, self.env, "test")
        self.assertEqual(run.call_args.args[0], [str(launcher), "--offline", "--no-daemon", "--stop"])
        child = run.call_args.kwargs["env"]
        self.assertEqual(child["GRADLE_USER_HOME"], str(self.job.root / "gradle"))
        self.assertEqual(child["PATH"], "/usr/bin:/bin:/usr/sbin:/sbin")
        self.assertTrue(set(child).isdisjoint({"GITHUB_TOKEN", "KIRA_INTAKE_READ_TOKEN", "JAVA_TOOL_OPTIONS", "ORG_GRADLE_PROJECT_FAKE"}))
        self.assertEqual(run.call_args.kwargs["timeout"], 60)

    def test_failed_gradle_still_calls_immediate_owned_stop(self):
        process = mock.Mock()
        process.poll.return_value = 1
        process.returncode = 1
        with mock.patch.object(caller.subprocess, "Popen", return_value=process) as start, \
                mock.patch.object(caller, "stop_owned") as stop:
            with self.assertRaises(policy.Refusal):
                caller.run_gradle(self.job, self.env, ["/fixture/gradlew", "help"], self.producer, "failed")
        stop.assert_called_once_with(self.job, self.env, "failed")
        self.assertNotIn("GITHUB_TOKEN", start.call_args.kwargs["env"])
        self.assertTrue(start.call_args.kwargs["start_new_session"])

    def test_interrupted_process_exit_race_cannot_skip_owned_stop(self):
        process = mock.Mock(pid=123)
        process.poll.return_value = None
        with mock.patch.object(caller.subprocess, "Popen", return_value=process), \
                mock.patch.object(caller.time, "monotonic", side_effect=(0, 301)), \
                mock.patch.object(caller.os, "killpg", side_effect=ProcessLookupError), \
                mock.patch.object(caller, "stop_owned") as stop:
            with self.assertRaises(policy.Refusal):
                caller.run_gradle(self.job, self.env, ["/fixture/gradlew", "help"], self.producer, "interrupted")
        stop.assert_called_once_with(self.job, self.env, "interrupted")
        process.wait.assert_called_once_with(timeout=5)

    def test_retained_archive_contains_actual_native_bytes_and_refuses_changed_tree(self):
        source = self.producer / "build/publication-bytes/owned-maven"
        files = {"me/manga/fixture/1.2.3/fixture.jar": b"actual fixture bytes",
                 "me/manga/fixture/maven-metadata.xml": b"actual fixture native index"}
        for name, data in files.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        observed = caller.tree_hashes(source)
        receipt = caller.archive_owned_maven(self.job, observed)
        target = self.job.root / "diagnostics/owned-maven.tar"
        self.assertEqual(receipt, {"file": target.name, "size": target.stat().st_size,
                                  "sha256": policy.digest(target.read_bytes()), "files": len(files)})
        with tarfile.open(target, "r:") as archive:
            self.assertEqual(archive.getnames(), sorted(files))
            for member in archive.getmembers():
                self.assertTrue(member.isreg())
                self.assertEqual((member.uid, member.gid, member.mtime, member.mode), (0, 0, 0, 0o600))
                self.assertEqual(archive.extractfile(member).read(), files[member.name])
        target.unlink()
        (source / next(iter(files))).write_bytes(b"changed")
        with self.assertRaises(policy.Refusal):
            caller.archive_owned_maven(self.job, observed)
        self.assertFalse(target.exists())

    def test_disposal_requires_confirmed_worker_cleanup_and_removes_only_reserved_locations(self):
        outside = self.root / "unrelated"
        outside.mkdir()
        (outside / "keep").write_text("keep")
        with self.assertRaises((policy.Refusal, FileNotFoundError)):
            caller.dispose(self.job)
        caller.write_json(self.job.root / "diagnostics/cleanup-ready.json",
                          {"owned_gradle_stop": "complete", "bootstrap_absent": True})
        scratch = self.producer.with_name("tooling")
        scratch.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(policy.Refusal):
            caller.dispose(self.job)
        scratch.unlink()
        caller.dispose(self.job)
        self.assertTrue((outside / "keep").exists())
        self.assertTrue(self.control.is_dir())
        self.assertFalse(self.job.root.exists())
        self.assertFalse(self.producer.exists())

    def test_check_workflow_pins_actual_cohort_and_never_requests_write_or_producer_tasks(self):
        text = (Path(__file__).resolve().parents[1] / "workflows/owned-file-promotion-check.yml").read_text()
        selected_text = text.split("SELECTED_PRODUCER: >-\n", 1)[1].split("\njobs:", 1)[0]
        selected = json.loads(selected_text)
        policy.expected_identity(selected)
        self.assertEqual((selected["source_sha"], selected["run_id"], selected["run_attempt"]), (SELECTED_SHA, SELECTED_RUN, 1))
        for required in ("github.repository_id == '1310736214'", "github.run_attempt == 1", caller.CONTROL_REF,
                         "github.workflow_sha == github.sha", "persist-credentials: false", "java-version: '21'"):
            self.assertIn(required, text)
        for forbidden in ("secrets.", "packages:", "id-token:", "environment:", "cache:", "promote-remote",
                          "publishToMavenLocal", "ci-start", "simctl", "./gradlew check", "exportKiraPublicationBytes"):
            self.assertNotIn(forbidden, text)
        self.assertLess(text.index(".py intake"), text.index(".py bootstrap"))
        self.assertLess(text.index(".py warmup"), text.index(".py promote"))
        self.assertLess(text.index(".py finalize"), text.index("uses: actions/upload-artifact@"))
        self.assertLess(text.index("uses: actions/upload-artifact@"), text.index(".py dispose"))


if __name__ == "__main__":
    unittest.main()
