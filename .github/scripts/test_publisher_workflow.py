"""Publisher composition with real byte/intake code and synthetic Git/native/API facts.

These tests are not genuine GitHub approval, Darwin execution or publication evidence.
"""

import ast
import base64
import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import signal
import socket
import tarfile
import tempfile
import textwrap
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import urllib.parse

import github_publication_receipts as receipts
import publication_policy as policy
import publisher_workflow as workflow
import remote_maven_readback as reader
import test_github_publication_receipts as http_fixtures
import test_publication_policy as fixtures
import test_publisher_publication_intake as intake_fixtures


class PublisherWorkflowTests(fixtures.OwnedFixture):
    # Reuse fixture helpers only; the earlier eleven intake tests are not inherited.
    patch = intake_fixtures.PublisherIntakeTests.patch
    native_phase = intake_fixtures.PublisherIntakeTests.native_phase
    connect = intake_fixtures.PublisherIntakeTests.connect
    close_responses = intake_fixtures.PublisherIntakeTests.close_responses
    assert_cleaned = intake_fixtures.PublisherIntakeTests.assert_cleaned
    reset_responses = intake_fixtures.PublisherIntakeTests.reset_responses
    lineage_phase = intake_fixtures.PublisherIntakeTests.lineage_phase

    def setUp(self):
        intake_fixtures.PublisherIntakeTests.setUp(self)
        self.original_archive = self.archive_path
        self.archive_bytes = self.archive_path.read_bytes()
        self.control = self.root / "control"
        self.control.mkdir()
        self.context = http_fixtures.publisher_context()
        self.token = "synthetic_EXPLICIT_read_token"
        self.package_token = "synthetic_PACKAGES_read_token"
        self.audit_token = "synthetic_METADATA_AUDIT_token"
        self.credentials = {"KIRA_PUBLICATION_API_READ_TOKEN": self.token,
                            "KIRA_MAVEN_READ_USERNAME": "synthetic-reader", "KIRA_MAVEN_READ_TOKEN": self.package_token,
                            "KIRA_MAVEN_WRITE_USERNAME": "synthetic-owner", "KIRA_MAVEN_WRITE_TOKEN": "synthetic_WRITE_token"}
        self.window = {"control": {"fixture": "not-live-authority"}, "registry": {"fixture": "not-live-registry"}}
        self.audit_read = self.patch(workflow, "audit_deleted", return_value={"synthetic": "not-real-audit"})
        self.window_read = self.patch(workflow.intake, "observe_release_window", side_effect=self.window_phase)
        protection = self.selection["protection"]
        self.trusted = {"publisher_ref": self.selection["publisher"]["ref"],
                        "environment": copy.deepcopy(protection["environment"]),
                        "branches": [{k: v for k, v in rule.items() if k != "tip"}
                                     for rule in copy.deepcopy(protection["branches"])]}
        self.patch(workflow, "TRUSTED_POLICY", new=self.trusted)
        self.identity = self.patch(receipts, "acquire_current_publisher", return_value=self.selection["publisher"])
        self.bundle = self.patch(receipts, "acquire_attestation_bundle", return_value=b'{"synthetic":"UNVERIFIED"}')
        self.producer_source = self.source.side_effect
        self.source.side_effect = self.source_phase
        self.producer_git = policy.git.side_effect
        self.fetched_tip, self.local_tip = "e" * 40, ""
        policy.git.side_effect = self.repository_git

    def window_phase(self, selected, *, api_token, package_token, deleted_observer, before=None):
        self.assertEqual((selected, api_token, package_token), (self.selection, self.token, self.package_token))
        self.assertEqual(deleted_observer(), {"synthetic": "not-real-audit"})
        self.audit_read.assert_called_with(self.selection, self.audit_token)
        self.assertGreater(self.native.call_count, 0)
        if before is not None:
            self.assertEqual(before, self.window)
        self.events.append("window")
        return copy.deepcopy(self.window)

    def source_phase(self, root, sha, *, fresh=False):
        if root == self.control:
            self.assertEqual((sha, fresh), (self.context["GITHUB_WORKFLOW_SHA"], True))
            return {"scope": "synthetic-control-check-only"}
        return self.producer_source(root, sha, fresh=fresh)

    def repository_git(self, root, *args):
        if args == ("show-ref", "--verify", "--hash", "refs/remotes/origin/" + policy.BRANCH):
            return (self.fetched_tip + "\n").encode()
        if args == ("for-each-ref", "--format=%(objectname)", policy.REF):
            return (self.local_tip + "\n").encode()
        if args[:5] == ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "update-ref"):
            self.assertEqual(args[5:], (policy.REF, self.fetched_tip, "0" * 40))
            self.assertEqual(self.local_tip, "")
            self.local_tip = self.fetched_tip
            return b""
        return self.producer_git(root, *args)

    def request(self):
        return {"producer": self.selection["producer"], "source_branch_tip": "e" * 40,
                "tag_object": "9" * 40, "app_parity_ref": self.selection["app_parity_ref"],
                "backend_parity_ref": self.selection["backend_parity_ref"], "release_window": self.selection["release_window"]}

    def job(self, phase, name=None):
        runtime = workflow.Runtime(self.control, self.checkout, self.root / (name or phase), self.context.copy(), phase)
        # Prepare reserves an absent checkout; the real workflow checks it out only afterwards.
        parked = self.root / "parked-source-fixture"
        self.checkout.rename(parked)
        try:
            prepared = workflow.prepare(runtime, self.request(), token=self.token)
        finally:
            parked.rename(self.checkout)
        (runtime.root / "artifact" / policy.ARCHIVE).write_bytes(self.archive_bytes)
        self.archive_path = runtime.root / policy.ARCHIVE
        self.native_asset = runtime.root / policy.GH_DARWIN_ASSET
        self.attestation = runtime.root / "attestation.json"
        workflow.write_private(self.native_asset, b"SYNTHETIC asset; native seam mocked")
        self.assertNotIn("environment", prepared)
        return runtime, prepared["selection_sha256"]

    def before(self):
        runtime, selection_hash = self.job("preflight")
        output = workflow.preflight(runtime, selection_hash, token=self.token, package_token=self.package_token, audit_token=self.audit_token)
        data = (runtime.root / "comparison.json").read_bytes()
        self.assert_cleaned()
        workflow.cleanup(runtime)
        self.reset_responses()
        self.events.clear()
        return data, output["record_sha256"]

    def handoff(self, runtime, data):
        (runtime.root / "handoff/comparison.json").write_bytes(data)

    def native_result(self, record_hash):
        publications = sorted(":" + module + ":" + publication for module in policy.MODULES for publication in policy.PUBLICATIONS)
        return {"scope": "native-remote-maven-readback", "record_sha256": record_hash,
                "producer": {key: self.selection["producer"][key] for key in ("run_id", "source_sha", "seal_sha256", "inventory_sha256")},
                "publisher_run_id": self.selection["publisher"]["run_id"], "publications": publications,
                "readback": {"completed": publications, "inventory_sha256": self.selection["producer"]["inventory_sha256"],
                             "indexes": {key: "synthetic-not-remote-readback" for key in publications}}, "window": self.window}

    def test_unconfigured_or_dispatch_policy_refuses_before_http_or_environment_output(self):
        runtime = workflow.Runtime(self.control, self.checkout, self.root / "unconfigured", self.context, "preflight")
        with mock.patch.object(workflow, "TRUSTED_POLICY", None), self.assertRaises(workflow.MissingAuthority):
            workflow.prepare(runtime, self.request(), token=self.token)
        self.source.assert_not_called()
        invalid = [self.request() | extra for extra in
                   ({"approved": True}, {"environment": self.trusted["environment"]}, {"protection": self.trusted})]
        invalid.append({key: value for key, value in self.request().items() if key != "release_window"})
        for request in invalid:
            with self.subTest(fields=tuple(request)), self.assertRaises(policy.Refusal):
                workflow.prepare(runtime, request, token=self.token)
        self.assertFalse(runtime.root.exists())
        self.identity.assert_not_called()
        self.http.assert_not_called()
        self.native.assert_not_called()

    def test_preflight_real_intake_precedes_environment_output_and_binds_private_record(self):
        runtime, selection_hash = self.job("preflight")
        output = workflow.preflight(runtime, selection_hash, token=self.token, package_token=self.package_token, audit_token=self.audit_token)
        data = (runtime.root / "comparison.json").read_bytes()
        self.assertEqual(output, {"record_sha256": policy.digest(data),
                                  "environment": self.trusted["environment"]["name"],
                                  "environment_id": self.trusted["environment"]["id"]})
        self.assertEqual(json.loads(data)["selection"], self.selection)
        self.assertEqual(set(json.loads(data)), {"selection", "preflight", "window"})
        self.assertEqual(json.loads(data)["window"], self.window)
        self.assertNotIn(self.token.encode(), data)
        self.assertNotIn(self.package_token.encode(), data)
        self.assertFalse((self.checkout / "build").exists())
        self.assertEqual(len(self.requests), 14)
        self.assertLess(self.events.index("native"), next(i for i, event in enumerate(self.events) if event.startswith("http:")))
        self.assertGreater(self.events.index("window"), self.events.index("lineage"))
        self.native.assert_called_once()
        self.bundle.assert_called_once_with(self.selection["producer"], token=self.token)
        workflow.private_file(runtime.root / "comparison.json")
        self.assert_cleaned()
        workflow.cleanup(runtime)

    def test_recheck_repeats_intake_and_window_around_tooling_before_exact_native_command(self):
        data, record_hash = self.before()
        runtime, selection_hash = self.job("recheck")
        self.handoff(runtime, data)
        raw_stage = workflow.stage_raw

        def stage(*args):
            self.assertEqual(self.native.call_count, 2)
            self.assertEqual(len(self.requests), 14)
            self.assertEqual(self.window_read.call_count, 3)
            self.assertEqual(self.events[-2:], ["tooling", "window"])
            self.assertFalse((self.checkout / "build").exists())
            return raw_stage(*args)

        def tooling(job, selected, env):
            self.assertEqual(self.window_read.call_count, 2)
            self.assertIn("window", self.events)
            self.assertTrue(set(env).isdisjoint(self.credentials))
            self.events.append("tooling")
            return env

        def native(job, env, command, credentials, *, selected, audit_token):
            self.assertEqual(credentials, self.credentials)
            self.assertEqual((selected, audit_token), (self.selection, self.audit_token))
            self.assertTrue(set(env).isdisjoint(credentials))
            self.assertNotIn(self.audit_token, repr(env) + repr(credentials) + repr(command))
            self.assertTrue((self.checkout / "build/publication-bytes/input/seal.sha256").is_file())
            workflow.write_private(self.checkout / "build/publication-bytes/remote-readback.json", workflow.encoded(self.native_result(record_hash)))
            return {"exit_code": 0, "seconds": 0, "output_bytes": 0}

        with mock.patch.object(workflow, "stage_raw", side_effect=stage), \
                mock.patch.object(workflow, "native_credentials", return_value=self.credentials), \
                mock.patch.object(workflow, "tooling", side_effect=tooling), \
                mock.patch.object(workflow.owned_helpers(), "profile"), \
                mock.patch.object(workflow, "run_native", side_effect=native) as run:
            self.assertEqual(workflow.recheck(runtime, selection_hash, record_hash, token=self.token,
                                             package_token=self.package_token, audit_token=self.audit_token),
                             {"result": "complete-native-remote-readback"})
        command = run.call_args.args[2]
        self.assertEqual(command[:8], [str(self.checkout / "gradlew"), "--offline", "--no-daemon", "--no-parallel",
                                      "--max-workers=1", "--no-build-cache", "--no-configuration-cache", "--console=plain"])
        self.assertIn("-PkiraPublicationSealSha256=" + self.selection["producer"]["seal_sha256"], command)
        self.assertIn("-PkiraPublisherRecordSha256=" + record_hash, command)
        self.assertNotIn("--info", command)
        split = command.index(":promoteKiraPublicationBytes")
        self.assertEqual(command[split + 1:], [part for task in policy.text_lines(self.metadata["producers.txt"]) for part in ("-x", task)])
        target = self.checkout / "build/publication-bytes/input"
        for name, value in self.metadata.items():
            self.assertEqual((target / name).read_bytes(), value)
        self.assertEqual(len(list((target / "payloads").rglob("*.sha256"))), 0)
        unrelated = self.root / "unrelated"
        unrelated.write_text("retain")
        self.assert_cleaned()
        self.assertEqual(json.loads((runtime.root / "diagnostics/remote-readback.json").read_bytes()), self.native_result(record_hash))
        workflow.owned_helpers().finalize(runtime, {})
        workflow.owned_helpers().dispose(runtime)
        self.assertFalse(runtime.root.exists())
        self.assertFalse((self.checkout / "build").exists())
        self.assertEqual(unrelated.read_text(), "retain")
        self.assertEqual(self.original_archive.read_bytes(), self.archive_bytes)

    def test_window_refusal_before_or_after_tooling_never_stages_or_launches_native(self):
        data, record_hash = self.before()
        for late in (False, True):
            self.reset_responses()
            runtime, selection_hash = self.job("recheck", "late-window" if late else "early-window")
            self.handoff(runtime, data)
            observations = [self.window, policy.Refusal("changed window")] if late else [policy.Refusal("changed window")]
            with mock.patch.object(workflow.intake, "observe_release_window", side_effect=observations) as window, \
                    mock.patch.object(workflow, "native_credentials", return_value=self.credentials), \
                    mock.patch.object(workflow, "tooling", return_value={}) as tooling, \
                    mock.patch.object(workflow, "stage_raw") as stage, mock.patch.object(workflow, "run_native") as native:
                with self.subTest(late=late), self.assertRaises(policy.Refusal):
                    workflow.recheck(runtime, selection_hash, record_hash, token=self.token,
                                      package_token=self.package_token, audit_token=self.audit_token)
                self.assertEqual(tooling.call_count, int(late))
                self.assertEqual(window.call_count, 2 if late else 1)
                self.assertTrue(all(call.kwargs["before"] == self.window for call in window.call_args_list))
                stage.assert_not_called()
                native.assert_not_called()
            self.assertFalse((self.checkout / "build").exists())
            self.assertFalse((runtime.root / "diagnostics/remote-readback.json").exists())
            workflow.cleanup(runtime)

    def test_missing_substituted_or_partial_native_readback_is_never_copied_as_success(self):
        runtime, _ = self.job("recheck")
        path = self.checkout / "build/publication-bytes/remote-readback.json"
        with self.assertRaises(FileNotFoundError):
            workflow.capture_readback(runtime, self.selection, "a" * 64)
        path.parent.mkdir(parents=True)
        valid = self.native_result("a" * 64)
        cases = [valid | {"record_sha256": "b" * 64}, valid | {"publisher_run_id": 62},
                 valid | {"producer": valid["producer"] | {"source_sha": "0" * 40}},
                 valid | {"publications": valid["publications"][:-1]},
                 valid | {"readback": valid["readback"] | {"completed": valid["publications"][:-1]}}]
        for number, value in enumerate(cases):
            workflow.write_private(path, workflow.encoded(value))
            with self.subTest(case=number), self.assertRaises(policy.Refusal):
                workflow.capture_readback(runtime, self.selection, "a" * 64)
            self.assertFalse((runtime.root / "diagnostics/remote-readback.json").exists())
            path.unlink()
        workflow.cleanup(runtime)

    def test_native_credentials_use_actual_package_account_and_reject_aliased_writer_before_http(self):
        with mock.patch.object(receipts, "_get_json", return_value={"id": 81, "login": "synthetic-reader"}) as user:
            actual = workflow.native_credentials(api_token=self.token, package_token=self.package_token,
                                                 write_username="synthetic-owner", write_token="synthetic_WRITE_token")
            self.assertEqual(actual, self.credentials)
            user.assert_called_once_with("/user", self.package_token)
            user.reset_mock()
            for write in (self.token, self.package_token, None, "bad token"):
                with self.subTest(write_is_missing=write is None), self.assertRaises(policy.Refusal):
                    workflow.native_credentials(api_token=self.token, package_token=self.package_token,
                                                 write_username="synthetic-owner", write_token=write)
            user.assert_not_called()

    def test_native_failure_redacts_split_secrets_and_stops_only_owned_group_and_home(self):
        temporary = tempfile.TemporaryDirectory(prefix="kira-audit-", dir=Path("/tmp").resolve(strict=True))
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / ("kira-publisher-" + self.context["GITHUB_RUN_ID"] + "-1")
        authorization = base64.b64encode(b"synthetic-owner:synthetic_WRITE_token")
        for timeout in (False, True):
            runtime, _ = self.job("recheck", root)
            env = workflow.runtime_environment(runtime, {"JAVA_HOME": "/fixture/java", "GITHUB_ENV": "/fixture/runner-env",
                "GITHUB_ACTIONS": "true", "RUNNER_OS": "macOS", "RUNNER_ARCH": "ARM64", "GH_TOKEN": "ambient-forbidden"})
            process = mock.Mock(pid=4321, returncode=1)
            process.poll.return_value = 1
            live = [True]
            chunks = [b"synthetic_EXPLI", b"CIT_read_token synthetic_PACKAGES_read_token ",
                      b"synthetic_WRITE_token " + self.audit_token.encode() + b" " + authorization
                      + b" https://storage.invalid/x?signature=private\n", b""]
            with mock.patch.object(workflow.subprocess, "Popen", return_value=process) as launch, \
                    mock.patch.object(workflow.selectors, "DefaultSelector") as factory, \
                    mock.patch.object(workflow.os, "read", side_effect=chunks), \
                    mock.patch.object(workflow.os, "killpg") as kill, \
                    mock.patch.object(workflow.time, "monotonic", side_effect=[0, 1201] if timeout else None, return_value=1), \
                    mock.patch.object(workflow.owned_helpers(), "stop_owned") as stop:
                selector = factory.return_value.__enter__.return_value
                selector.get_map.side_effect = lambda: {17: True} if live else {}
                selector.select.return_value = [(SimpleNamespace(fd=17, fileobj=process.stdout), workflow.selectors.EVENT_READ)]
                selector.unregister.side_effect = lambda _: live.clear()
                with self.subTest(timeout=timeout), self.assertRaises(policy.Refusal):
                    workflow.run_native(runtime, env, workflow.guarded_command(runtime, self.selection, self.metadata, "a" * 64),
                                         self.credentials, selected=self.selection, audit_token=self.audit_token)
                child = launch.call_args.kwargs["env"]
                self.assertTrue(launch.call_args.kwargs["start_new_session"])
                self.assertNotIn("GH_TOKEN", child)
                self.assertNotIn("GITHUB_ENV", child)
                self.assertNotIn("KIRA_PACKAGES_METADATA_AUDIT_TOKEN", child)
                self.assertNotIn(self.audit_token, repr(launch.call_args))
                self.assertEqual(launch.call_args.kwargs["stdin"], workflow.subprocess.DEVNULL)
                self.assertEqual({key: child[key] for key in self.credentials}, self.credentials)
                self.assertEqual(child["GRADLE_USER_HOME"], str(runtime.root / "gradle"))
                self.assertEqual(kill.call_args_list, [mock.call(4321, signal.SIGTERM), mock.call(4321, signal.SIGKILL)])
                stop.assert_called_once_with(runtime, env, "native")
                self.assertTrue(set(stop.call_args.args[1]).isdisjoint(self.credentials))
                process.stdout.close.assert_called_once()
            log = (runtime.root / "diagnostics/native.log").read_bytes()
            for forbidden in (self.token.encode(), self.package_token.encode(), self.audit_token.encode(),
                              b"synthetic_WRITE_token", authorization, b"signature=private"):
                self.assertNotIn(forbidden, log)
            if not timeout:
                self.assertIn(b"[redacted]", log)
                self.assertIn(b"[redacted-query-url]", log)
            self.assertFalse((runtime.root / "diagnostics/native-result.json").exists())
            self.assertFalse((runtime.root / workflow.intake.AUDIT_SOCKET).exists())
            workflow.cleanup(runtime)

    def test_disposal_requires_worker_barrier_and_retains_unrelated_paths(self):
        runtime, _ = self.job("recheck")
        outside = self.root / "unrelated-owner"
        outside.write_text("retain")
        marker = runtime.root / "bootstrap-started.json"
        workflow.write_private(marker, workflow.encoded({"source_sha": self.selection["producer"]["source_sha"]}))
        owned = workflow.owned_helpers()
        with self.assertRaises((FileNotFoundError, policy.Refusal)):
            owned.dispose(runtime)
        with self.assertRaises(policy.Refusal):
            workflow.cleanup(runtime)
        with mock.patch.object(owned, "stop_owned", side_effect=policy.Refusal("fixture stop failure")), \
                mock.patch.object(owned, "bootstrap") as bootstrap:
            with self.assertRaises(policy.Refusal):
                owned.finalize(runtime, {})
            bootstrap.assert_not_called()
        self.assertFalse((runtime.root / "diagnostics/cleanup-ready.json").exists())
        self.assertTrue(runtime.producer.is_dir())
        marker.unlink()  # No actual bootstrap was started in this synthetic test.
        owned.finalize(runtime, {})
        self.assertTrue((runtime.root / "diagnostics/cleanup-ready.json").is_file())
        owned.dispose(runtime)
        self.assertFalse(runtime.root.exists())
        self.assertFalse(runtime.producer.exists())
        self.assertEqual(outside.read_text(), "retain")

    def test_substituted_handoff_context_or_source_refuses_before_fresh_native_and_staging(self):
        data, record_hash = self.before()
        version = self.checkout / "gradle.properties"
        original = version.read_bytes()
        for case in ("record", "selection", "context", "source"):
            runtime, selected_hash = self.job("recheck", case)
            self.handoff(runtime, data)
            attempted = runtime
            if case == "context":
                attempted = workflow.Runtime(runtime.control, runtime.producer, runtime.root,
                                             runtime.context | {"GITHUB_RUN_ID": "62"}, runtime.phase)
            if case == "source":
                version.write_text("VERSION_NAME=1.2.4\n")
            try:
                with self.subTest(case=case), self.assertRaises(policy.Refusal):
                    workflow.recheck(attempted, "0" * 64 if case == "selection" else selected_hash,
                                     "0" * 64 if case == "record" else record_hash, token=self.token,
                                     package_token=self.package_token, audit_token=self.audit_token)
                self.assertEqual(self.native.call_count, 1)
                self.assertEqual(self.requests, [])
                self.assertFalse((self.checkout / "build").exists())
            finally:
                version.write_bytes(original)
                workflow.cleanup(runtime)

    def test_mutable_policy_is_reobserved_and_failed_recheck_never_stages(self):
        data, record_hash = self.before()
        values = http_fixtures.publisher_policy_values()
        values[1]["can_admins_bypass"] = True
        self.reset_responses(values)
        runtime, selected_hash = self.job("recheck")
        self.handoff(runtime, data)
        with self.assertRaisesRegex(policy.Refusal, "^Publisher recheck refused$"):
            workflow.recheck(runtime, selected_hash, record_hash, token=self.token,
                             package_token=self.package_token, audit_token=self.audit_token)
        self.assertEqual(self.native.call_count, 2)
        self.assertGreater(len(self.requests), 4)
        self.assertFalse((self.checkout / "build").exists())
        self.assertFalse((runtime.root / "diagnostics/held-command.json").exists())
        self.assert_cleaned()
        workflow.cleanup(runtime)

    def test_raw_staging_refuses_unsafe_duplicate_link_and_existing_generated_root(self):
        runtime, _ = self.job("recheck")
        link = tarfile.TarInfo("payloads/link")
        link.type, link.linkname = tarfile.SYMTYPE, "../../outside"
        cases = [("../outside", b"unsafe"), ("inventory.tsv", self.metadata["inventory.tsv"]), (link, b"")]
        for entry in cases:
            archive, _ = self.archive(extra=[entry])
            with self.subTest(entry=str(entry[0])), self.assertRaises(policy.Refusal):
                workflow.stage_raw(runtime, self.selection, archive)
            self.assertFalse((self.checkout / "build").exists())
        self.original_archive.write_bytes(self.archive_bytes)
        marker = self.checkout / "build/foreign"
        marker.parent.mkdir()
        marker.write_text("retain")
        with self.assertRaises(policy.Refusal):
            workflow.stage_raw(runtime, self.selection, self.original_archive)
        workflow.cleanup(runtime)
        self.assertEqual(marker.read_text(), "retain")
        self.assertFalse((self.root / "outside").exists())

    def test_detached_checkout_creates_only_exact_observed_fetched_ref_without_hooks(self):
        runtime, _ = self.job("preflight")
        for fetched, local in (("0" * 40, ""), ("e" * 40, "f" * 40)):
            self.fetched_tip, self.local_tip = fetched, local
            with self.assertRaises(policy.Refusal):
                workflow.inputs(runtime, self.selection, token=self.token)
        self.bundle.assert_not_called()
        self.fetched_tip, self.local_tip = "e" * 40, ""
        workflow.inputs(runtime, self.selection, token=self.token)
        self.assertEqual(self.local_tip, self.fetched_tip)
        updates = [call.args for call in policy.git.call_args_list if "update-ref" in call.args]
        self.assertEqual(updates, [(self.checkout, "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                                   "update-ref", policy.REF, "e" * 40, "0" * 40)])
        workflow.cleanup(runtime)


class MetadataAuditCustodyTests(unittest.TestCase):
    def setUp(self):
        # Darwin's runner TMPDIR is too long for the unchanged production socket bound.
        temporary = tempfile.TemporaryDirectory(prefix="kira-audit-", dir=Path("/tmp").resolve(strict=True))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        invocation, protection = http_fixtures.publisher_selection()
        producer = fixtures.expected()
        self.selected = {"producer": producer, "publisher": invocation, "protection": protection,
                         "app_parity_ref": "synthetic-app", "backend_parity_ref": "synthetic-backend",
                         "release_window": intake_fixtures.selected_window(producer, invocation, protection)}
        root = self.root / ("kira-publisher-" + str(invocation["run_id"]) + "-1")
        root.mkdir(mode=0o700)
        self.runtime = workflow.Runtime(self.root / "control", self.root / "producer", root,
                                        http_fixtures.publisher_context(), "recheck")
        workflow.write_private(root / "owner.json", workflow.encoded(self.runtime.owner()))
        self.token = "synthetic_separate_METADATA_AUDIT"
        self.observation = {"packages": {identity: value | {"deleted": []}
                                         for identity, value in self.selected["release_window"]["packages"].items()},
                            "grants": {"profile": reader.AUDIT_PROFILE, "oauth_scopes": ["delete:packages", "read:packages"]}}
        self.patch = mock.patch.object(workflow, "audit_deleted", return_value=self.observation)
        self.get = self.patch.start()
        self.addCleanup(self.patch.stop)
        timeout = mock.patch.object(workflow.intake, "AUDIT_SECONDS", 2)
        timeout.start()
        self.addCleanup(timeout.stop)

    def audit(self):
        return workflow.MetadataAudit(self.runtime, self.selected, self.token, time.monotonic() + 10)

    def client(self, finally_close=None):
        results = []

        def run():
            try:
                results.append(workflow.intake.controller_deleted_versions(self.runtime.root / policy.ARCHIVE, self.selected))
            except Exception as error:
                results.append(error)
            finally:
                if finally_close is not None:
                    os.close(finally_close)

        thread = threading.Thread(target=run)
        thread.start()
        return thread, results

    def joined(self, thread):
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())

    def test_real_owned_socket_has_sixteen_fresh_fixed_observations_then_refuses_and_disappears(self):
        with self.audit() as audit:
            self.assertEqual(audit.path.stat().st_mode & 0o777, 0o600)
            for _ in range(16):
                thread, results = self.client()
                try:
                    audit.serve_once()
                finally:
                    self.joined(thread)
                self.assertEqual(results, [self.observation])
            self.assertEqual(self.get.call_count, 16)
            self.get.assert_called_with(self.selected, self.token)
            with self.assertRaises(policy.Refusal):
                audit.serve_once()
            self.assertNotIn(self.token, workflow.encoded(results).decode())
        self.assertEqual(audit.listener.fileno(), -1)
        self.assertFalse(audit.path.exists())
        self.assertIsNone(audit.token)
        self.assertEqual([p.name for p in self.runtime.root.iterdir()], ["owner.json"])

    def test_fixed_operation_binding_size_and_provider_failure_never_return_credentials(self):
        binding = workflow.intake.metadata_audit_binding(self.selected) | {"nonce": "a" * 32}
        cases = [workflow.encoded(binding | change) for change in
                 ({"operation": "active-versions"}, {"selection_sha256": "0" * 64},
                  {"nonce": "bad"}, {"url": "https://untrusted.invalid"}, {"token": self.token})]
        cases.append(b"x" * (workflow.intake.AUDIT_REQUEST_LIMIT + 1))
        with self.audit() as audit:
            for request in cases:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(2)
                    client.connect(str(audit.path))
                    client.sendall(request)
                    client.shutdown(socket.SHUT_WR)
                    with self.assertRaises((policy.Refusal, ValueError)):
                        audit.serve_once()
                    self.assertEqual(client.recv(1), b"")
            self.get.assert_not_called()
        with self.assertRaises(OSError):
            with self.audit() as audit:
                self.get.side_effect = OSError(self.token)
                thread, results = self.client()
                try:
                    audit.serve_once()
                finally:
                    self.joined(thread)
                self.fail("Provider error was swallowed")
        self.assertFalse(audit.path.exists())
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], Exception)
        self.assertNotIn(self.token, str(results[0]))

    def test_client_rejects_nonce_substitution_and_missing_members_and_reused_socket(self):
        for change in ("nonce", "membership"):
            with self.audit() as audit:
                thread, results = self.client()
                try:
                    with audit.listener.accept()[0] as connection:
                        request = workflow.intake.audit_receive(connection, workflow.intake.AUDIT_REQUEST_LIMIT, 2)
                        self.assertEqual(set(request), {"operation", "selection_sha256", "nonce"})
                        self.assertNotIn(self.token, repr(request))
                        response = request | {"observation": self.observation}
                        if change == "nonce":
                            response["nonce"] = "0" * 32
                        else:
                            response["observation"] = self.observation | {"packages": {}}
                        connection.sendall(workflow.encoded(response))
                finally:
                    self.joined(thread)
                self.assertEqual(len(results), 1)
                self.assertIsInstance(results[0], policy.Refusal)
        path = self.runtime.root / workflow.intake.AUDIT_SOCKET
        path.write_text("unrelated")
        with self.assertRaises(policy.Refusal):
            with self.audit():
                self.fail("Reused socket path admitted")
        self.assertEqual(path.read_text(), "unrelated")

    def test_existing_stdout_selector_services_audit_without_a_production_thread_or_child(self):
        read, write = os.pipe()
        with os.fdopen(read, "rb") as stdout, self.audit() as audit:
            process = SimpleNamespace(stdout=stdout, poll=lambda: 0)
            thread, results = self.client(finally_close=write)
            credentials = {"KIRA_PUBLICATION_API_READ_TOKEN": "synthetic_API", "KIRA_MAVEN_READ_USERNAME": "reader",
                           "KIRA_MAVEN_READ_TOKEN": "synthetic_PAYLOAD", "KIRA_MAVEN_WRITE_USERNAME": "writer",
                           "KIRA_MAVEN_WRITE_TOKEN": "synthetic_WRITE"}
            try:
                count = workflow.native_output(process, io.BytesIO(), credentials, time.monotonic() + 5, audit)
            finally:
                self.joined(thread)
            self.assertEqual((count, audit.calls, results), (0, 1, [self.observation]))
        self.assertFalse(audit.path.exists())

    def test_missing_or_aliased_audit_custody_refuses_before_any_child_or_http(self):
        for value in (None, "", "bad token", "API", "PAYLOAD", "WRITE"):
            with self.subTest(value=value), self.assertRaises(policy.Refusal):
                workflow.audit_credentials(value, api_token="API", package_token="PAYLOAD", write_token="WRITE")
        workflow.audit_credentials(self.token, api_token="API", package_token="PAYLOAD", write_token="WRITE")
        with mock.patch.object(workflow.subprocess, "Popen") as child, mock.patch.object(receipts, "_get_json") as http:
            preflight = workflow.Runtime(self.runtime.control, self.runtime.producer, self.runtime.root,
                                          self.runtime.context, "preflight")
            with self.assertRaisesRegex(policy.Refusal, "Separate explicit metadata-audit custody required"):
                workflow.preflight(preflight, "0" * 64, token="API", package_token="PAYLOAD")
            with self.assertRaisesRegex(policy.Refusal, "Separate explicit metadata-audit custody required"):
                workflow.recheck(self.runtime, "0" * 64, "0" * 64, token="API", package_token="PAYLOAD", audit_token="PAYLOAD")
            with self.assertRaisesRegex(policy.Refusal, "Native credential projection refused"):
                workflow.run_native(self.runtime, {}, [], {"KIRA_PACKAGES_METADATA_AUDIT_TOKEN": self.token},
                                     selected=self.selected, audit_token=self.token)
            child.assert_not_called()
            http.assert_not_called()


class PublisherWorkflowEntryTests(fixtures.OwnedFixture):
    def test_cli_strips_child_credentials_and_refuses_unconfigured_or_write_custody(self):
        workspace, temporary = self.root / "workspace", self.root / "runner-temp"
        workspace.mkdir()
        temporary.mkdir()
        output = self.root / "github-output"
        output.write_text("")
        env = http_fixtures.publisher_context() | {"GITHUB_ACTIONS": "true", "RUNNER_OS": "macOS", "RUNNER_ARCH": "ARM64",
              "GITHUB_WORKSPACE": str(workspace), "RUNNER_TEMP": str(temporary), "KIRA_PUBLISHER_REQUEST": "{}",
              "GITHUB_OUTPUT": str(output),
              "KIRA_PUBLICATION_API_READ_TOKEN": "synthetic_READ_token", "GH_TOKEN": "ambient_SECRET",
              "GIT_CONFIG_COUNT": "1", "PYTHONPATH": "/untrusted", "HTTPS_PROXY": "https://untrusted.invalid"}

        def unconfigured():
            self.assertEqual(set(os.environ), {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"})
            raise workflow.MissingAuthority("not configured")

        with mock.patch.object(workflow.platform, "system", return_value="Darwin"), \
             mock.patch.object(workflow.platform, "machine", return_value="arm64"), \
             mock.patch.object(workflow.os, "umask"), mock.patch.object(workflow, "selected_policy", side_effect=unconfigured) as selected:
            errors = io.StringIO()
            with mock.patch.dict(os.environ, env, clear=True), redirect_stderr(errors):
                self.assertEqual(workflow.main(["prepare-preflight"]), 3)
            selected.assert_called_once()
            selected.reset_mock()
            with mock.patch.dict(os.environ, env | {"KIRA_MAVEN_WRITE_TOKEN": "synthetic_WRITE_secret"}, clear=True), redirect_stderr(errors):
                self.assertEqual(workflow.main(["prepare-preflight"]), 1)
            selected.assert_not_called()
            with mock.patch.dict(os.environ, env | {"KIRA_PACKAGES_METADATA_AUDIT_TOKEN": "synthetic_AUDIT_secret"}, clear=True), redirect_stderr(errors):
                self.assertEqual(workflow.main(["prepare-preflight"]), 1)
            selected.assert_not_called()
        self.assertNotIn("secret", errors.getvalue().lower())
        self.assertEqual(list(temporary.iterdir()), [])

    def test_workflow_isolates_privileged_probe_and_native_recheck_and_finalizes_before_capture(self):
        source = (Path(__file__).resolve().parents[1] / "workflows/publish.yml").read_text()
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("${{ inputs.access_check && !inputs.metadata_audit_check && github.event_name == 'workflow_dispatch'", source)
        self.assertIn("${{ !inputs.access_check && !inputs.metadata_audit_check && github.event_name == 'workflow_dispatch'", source)
        probe = source.split("  approval-and-access-check:", 1)[1].split("  approval-and-metadata-audit:", 1)[0]
        self.assertIn("environment: source-publication", probe)
        self.assertIn("packages: read", probe)
        self.assertEqual(probe.count("secrets."), 2)
        self.assertIn("KIRA_PUBLICATION_API_READ_TOKEN: ${{ secrets.KIRA_PUBLICATION_API_READ_TOKEN }}", probe)
        self.assertIn("KIRA_PACKAGES_READ_TOKEN: ${{ secrets.KIRA_PACKAGES_READ_TOKEN }}", probe)
        self.assertEqual(probe.count("github.token"), 1)
        self.assertIn("KIRA_DIAGNOSTIC_JOB_READ_TOKEN: ${{ github.token }}", probe)
        self.assertLess(probe.index("os.environ.pop('KIRA_DIAGNOSTIC_JOB_READ_TOKEN')"),
                        probe.index("import github_publication_receipts"))
        self.assertIn("timeout-minutes: 5", probe)
        self.assertNotIn("error_class", probe)
        self.assertNotIn("GH_TOKEN:", probe)
        self.assertNotIn("outputs:", probe)
        self.assertEqual(probe.count("uses:"), 1)
        self.assertIn("uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5", probe)
        self.assertIn("persist-credentials: false", probe)
        self.assertIn("sparse-checkout-cone-mode: false", probe)
        self.assertIn("import github_publication_receipts as receipts", probe)
        self.assertIn("import remote_maven_readback as reader", probe)
        self.assertIn("receipts.observe_json(path, policy_token)['value']", probe)
        self.assertIn("receipts.observe_json('/user', package_token, package_read=True)", probe)
        self.assertIn("'http_status': getattr(error, 'status', None)", probe)
        self.assertIn("'stage': getattr(error, 'stage', None)", probe)
        self.assertIn("check('package_identity_and_version_metadata', package_metadata)", probe)
        self.assertIn("/orgs/kira-manga/packages?package_type=maven&per_page=100&page=1", probe)
        self.assertIn("len(items) != 15", probe)
        self.assertIn("(actor, package_token), 1048576, budget, capture=True)", probe)
        self.assertIn("budget.requests = 2", probe)
        self.assertIn("failed.append(name)", probe)
        self.assertIn("raise SystemExit(1 if failed else 0)", probe)
        self.assertIn("Remove only this diagnostic's disposable helper checkout", probe)
        cleanup = probe.split("Remove only this diagnostic's disposable helper checkout", 1)[1]
        self.assertIn("if: ${{ always() }}", cleanup)
        self.assertIn("shutil.rmtree(root)", cleanup)
        self.assertIn("github.repository_id == '1310736214'", source)
        self.assertIn("github.ref == 'refs/heads/remediation/production-readiness-2026-09-04'", source)
        before, protected = source.split("  protected-recheck:", 1)
        read_probe, audit_and_preflight = before.split("  approval-and-metadata-audit:", 1)
        audit, preflight = audit_and_preflight.split("  preflight:", 1)
        self.assertNotIn("packages: write", read_probe + preflight)
        self.assertNotIn("packages: write", audit)
        self.assertIn("${{ inputs.metadata_audit_check && !inputs.access_check && github.event_name == 'workflow_dispatch'", audit)
        for guard in ("github.repository == 'kira-manga/kira-source-engine'", "github.repository_id == '1310736214'",
                      "github.ref == 'refs/heads/remediation/production-readiness-2026-09-04'", "github.run_attempt == 1",
                      "github.workflow_ref == format('kira-manga/kira-source-engine/.github/workflows/publish.yml@{0}', github.ref)",
                      "github.workflow_sha == github.sha"):
            self.assertIn(guard, audit)
        self.assertIn("environment: source-publication", audit)
        self.assertIn("runs-on: ubuntu-24.04", audit)
        self.assertIn("timeout-minutes: 5", audit)
        self.assertNotIn("github.token", audit)
        self.assertEqual(audit.count("secrets."), 2)
        self.assertEqual(audit.count("uses:"), 1)
        self.assertIn("actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5", audit)
        self.assertIn("persist-credentials: false", audit)
        self.assertIn("sparse-checkout-cone-mode: false", audit)
        self.assertEqual(re.findall(r"^            /(.+)$", audit, re.M),
                         [".github/scripts/publication_policy.py", ".github/scripts/github_publication_receipts.py",
                          ".github/scripts/remote_maven_readback.py"])
        for key in ("KIRA_PACKAGES_READ_TOKEN", "KIRA_PACKAGES_METADATA_AUDIT_TOKEN"):
            self.assertLess(audit.index("os.environ.pop('" + key + "')"),
                            audit.index("import github_publication_receipts"))
            self.assertIn(key + ": ${{ secrets." + key + " }}", audit)
        self.assertIn("receipts._get_deleted_versions(item['name'], audit_token)", audit)
        self.assertIn("package_token, collection=True, package_read=True", audit)
        for forbidden in ("inputs.selection", "outputs:", "GITHUB_OUTPUT", "GITHUB_ENV", "needs:",
                          "upload-artifact", "gradlew", "publisher_workflow.py", "bypass_branch", "subprocess", "'/user'"):
            self.assertNotIn(forbidden, audit)
        self.assertIn("if: ${{ always() }}", audit)
        self.assertIn("root.is_symlink()", audit)
        self.assertIn("shutil.rmtree(root)", audit)
        self.assertNotIn("KIRA_METADATA_AUDIT_JOB_TOKEN", source)
        self.assertIn("environment: source-publication", preflight)
        audit_secret = "KIRA_PACKAGES_METADATA_AUDIT_TOKEN: ${{ secrets.KIRA_PACKAGES_METADATA_AUDIT_TOKEN }}"
        self.assertEqual(audit.count(audit_secret), 1)
        self.assertEqual(preflight.count(audit_secret), 1)
        self.assertEqual(protected.count(audit_secret), 1)
        self.assertNotIn("KIRA_PACKAGES_METADATA_AUDIT_TOKEN", read_probe)
        self.assertNotIn("KIRA_PACKAGES_METADATA_AUDIT_TOKEN", preflight.split("Real fresh native intake", 1)[0])
        self.assertNotIn("KIRA_PACKAGES_METADATA_AUDIT_TOKEN", protected.split("publisher_workflow.py recheck", 1)[1])
        self.assertNotIn("KIRA_MAVEN_WRITE_", before)
        self.assertEqual(protected.count("packages: write"), 1)
        self.assertEqual(protected.count("KIRA_MAVEN_WRITE_USERNAME: ${{ github.actor }}"), 1)
        self.assertEqual(protected.count("KIRA_MAVEN_WRITE_TOKEN: ${{ github.token }}"), 1)
        self.assertNotIn("id-token: write", source)
        self.assertNotIn("run: ./gradlew", source)
        self.assertIn("name: ${{ needs.preflight.outputs.environment }}", source)
        self.assertIn("needs.preflight.result == 'success'", source)
        self.assertEqual(source.count("ref: ${{ github.workflow_sha }}"), 4)
        self.assertEqual(source.count("fetch-depth: 0"), 2)
        self.assertIn("artifact-ids: ${{ needs.preflight.outputs.record_artifact_id }}", source)
        self.assertEqual(source.count("KIRA_PUBLICATION_API_READ_TOKEN: ${{ secrets.KIRA_PUBLICATION_API_READ_TOKEN }}"), 5)
        self.assertEqual(source.count("KIRA_PACKAGES_READ_TOKEN: ${{ secrets.KIRA_PACKAGES_READ_TOKEN }}"), 4)
        self.assertNotIn("KIRA_PUBLICATION_API_READ_TOKEN: ${{ github.token }}", source)
        self.assertNotIn("KIRA_PACKAGES_READ_TOKEN: ${{ github.token }}", source)
        self.assertIn("cleanup-preflight", source)
        self.assertLess(protected.index("publisher_workflow.py finalize-recheck"), protected.index("uses: actions/upload-artifact@"))
        self.assertLess(protected.index("uses: actions/upload-artifact@"), protected.index("publisher_workflow.py cleanup-recheck"))
        self.assertNotIn("KIRA_MAVEN_WRITE_", protected.split("publisher_workflow.py recheck", 1)[1])
        self.assertIn("path: tooling", protected)
        self.assertIn("path: ${{ steps.runtime.outputs.root }}/diagnostics/", protected)
        ci = (Path(__file__).resolve().parents[1] / "workflows/ci.yml").read_text()
        self.assertIn("      - main", ci.split("  push:", 1)[0])
        self.assertNotIn("      - main", ci.split("  push:", 1)[1].split("permissions:", 1)[0])
        ipc_step = ci.split("      - name: Test controller metadata custody on the actual Darwin host", 1)[1].split("      - name:", 1)[0]
        self.assertIn("timeout-minutes: 2", ipc_step)
        self.assertIn("python3 -B -m unittest -v", ipc_step)
        self.assertIn("test_publisher_workflow.MetadataAuditCustodyTests", ipc_step)
        self.assertIn('tee "$KIRA_CI_ROOT/diagnostics/metadata-audit-ipc.log"', ipc_step)
        self.assertNotIn("secrets.", ipc_step)
        self.assertLess(ci.index("Test controller metadata custody"), ci.index("Prepare authenticated source-built libffi"))
        for pin in ("34e114876b0b11c390a56381ad16ebd13914f8d5", "d3f86a106a0bac45b974a628896c90dbdf5c8093",
                    "ea165f8d65b6e75b540449e92b4886f43607fa02", "c1e323688fd81a25caa38c78aa6df2d33d3e20d9"):
            self.assertIn(pin, source)


class PackageVersionDiagnosticTests(unittest.TestCase):
    def test_inline_version_loop_is_bounded_and_only_classic_failures_are_required(self):
        source = (Path(__file__).resolve().parents[1] / "workflows/publish.yml").read_text()
        inline = source.split("python3 -B -E -s - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
        tree = ast.parse(textwrap.dedent(inline))
        check = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "check")
        start = next(i for i, node in enumerate(tree.body)
                     if isinstance(node, ast.FunctionDef) and node.name == "package_metadata")
        # Execute the actual new inline functions/loop, never any real HTTP transport.
        program = compile(ast.Module(body=[check, *tree.body[start:]], type_ignores=[]), "access-check-inline", "exec")
        names = sorted(policy.GROUP + "." + module + suffix for module in policy.MODULES
                       for suffix in policy.PUBLICATIONS.values())
        packages = [{"id": i + 1, "name": name, "package_type": "maven",
                     "repository": {"id": 1310736214, "full_name": "kira-manga/kira-source-engine"}}
                    for i, name in enumerate(names)]
        for mode in ("pass", "classic_refusal", "comparison_refusal", "bad_version", "bad_package"):
            with self.subTest(mode=mode):
                calls = []
                selected = copy.deepcopy(packages)
                if mode == "bad_package":
                    selected[0]["name"] = selected[1]["name"]

                def observe(path, token, *, collection=False, package_read=False):
                    self.assertTrue(collection)
                    if path == "/orgs/kira-manga/packages?package_type=maven&per_page=100&page=1":
                        self.assertEqual((token, package_read), ("synthetic_classic", True))
                        values = selected
                    else:
                        parsed = urllib.parse.urlsplit(path)
                        self.assertIn(parsed.path, ["/orgs/kira-manga/packages/maven/" + name + "/versions"
                                                  for name in names])
                        query = urllib.parse.parse_qs(parsed.query)
                        self.assertEqual(set(query), {"state", "per_page", "page"})
                        self.assertIn(query["state"], (["active"], ["deleted"]))
                        self.assertEqual((query["per_page"], query["page"]), (["100"], ["1"]))
                        calls.append((parsed.path, query["state"][0], token, package_read))
                        if ((mode == "classic_refusal" and len(calls) == 1)
                                or (mode == "comparison_refusal" and token != "synthetic_classic")):
                            raise receipts.ReceiptObservationRefusal(403, "request")
                        values = [{"id": 101, "name": "0.1.0"}]
                        if mode == "bad_version" and len(calls) == 1:
                            values = [{"id": 101, "name": "unvalidated/body-or-token\n"}]
                    return {"value": values, "status": 200, "oauth_scopes": ["read:packages"]}

                output = io.StringIO()
                scope = {"json": json, "re": re, "urllib": urllib, "reader": reader, "failed": [],
                         "receipts": SimpleNamespace(observe_json=observe,
                             ReceiptObservationRefusal=receipts.ReceiptObservationRefusal),
                         "package_token": "synthetic_classic", "policy_token": "synthetic_policy",
                         "job_token": "synthetic_job"}
                with redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
                    exec(program, scope)
                self.assertEqual(stopped.exception.code, int(mode in ("classic_refusal", "bad_version", "bad_package")))
                records = [json.loads(line.removeprefix("KIRA_CREDENTIAL_CHECK "))
                           for line in output.getvalue().splitlines()]
                versions = [row for row in records if row["check"] == "package_versions"]
                if mode == "bad_package":
                    self.assertEqual((calls, versions), ([], []))
                    continue
                self.assertEqual(len(calls), 34)
                self.assertEqual(len(versions), 34)
                self.assertEqual({(path, state) for path, state, _, _ in calls[:30]},
                                 {("/orgs/kira-manga/packages/maven/" + name + "/versions", state)
                                  for name in names for state in ("active", "deleted")})
                self.assertTrue(all((token, classic) == ("synthetic_classic", True) for _, _, token, classic in calls[:30]))
                self.assertEqual([(token, classic) for _, _, token, classic in calls[30:]],
                                 [("synthetic_policy", False)] * 2 + [("synthetic_job", False)] * 2)
                self.assertTrue(all(path.endswith("/" + names[0] + "/versions") for path, _, _, _ in calls[30:]))
                self.assertTrue(all(row["required"] == (i < 30) and row["diagnostic_only"]
                                    and not row["custody_approval"] for i, row in enumerate(versions)))
                for row in versions:
                    self.assertIn(row["package"], names)
                    self.assertIn(row["state"], ("active", "deleted"))
                    if row["result"] == "FAIL":
                        self.assertIn((row["http_status"], row["stage"]), ((403, "request"), (200, "response_validation")))
                for secret in ("synthetic_classic", "synthetic_policy", "synthetic_job", "unvalidated/body-or-token"):
                    self.assertNotIn(secret, output.getvalue())


class MetadataAuditDiagnosticTests(unittest.TestCase):
    def test_actual_probe_uses_only_exact_get_inventory_and_versions_and_refuses_safely(self):
        source = (Path(__file__).resolve().parents[1] / "workflows/publish.yml").read_text()
        job = source.split("  approval-and-metadata-audit:", 1)[1].split("  preflight:", 1)[0]
        inline = job.split("python3 -B -E -s - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
        tree = ast.parse(textwrap.dedent(inline))
        start = next(i for i, node in enumerate(tree.body) if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == "failed" for target in node.targets))
        program = compile(ast.Module(body=tree.body[start:], type_ignores=[]), "metadata-audit-inline", "exec")
        names = sorted(policy.GROUP + "." + module + suffix for module in policy.MODULES
                       for suffix in policy.PUBLICATIONS.values())
        packages = [{"id": i + 1, "name": name, "package_type": "maven",
                     "repository": {"id": 1310736214, "full_name": "kira-manga/kira-source-engine"}}
                    for i, name in enumerate(names)]
        for mode in ("pass", "deleted_refusal", "inventory_refusal", "wrong_name", "wrong_repository",
                     "duplicate_id", "bad_version", "grant_change", "missing_audit", "missing_payload", "aliased_tokens"):
            with self.subTest(mode=mode):
                calls = []
                selected = copy.deepcopy(packages)
                if mode == "wrong_name":
                    selected[0]["name"] = "untrusted/path?token=synthetic_PRIVILEGED"
                elif mode == "wrong_repository":
                    selected[0]["repository"]["full_name"] = "another/repository"
                elif mode == "duplicate_id":
                    selected[0]["id"] = selected[1]["id"]

                def observe(path, token, *, collection=False, package_read=False):
                    self.assertEqual((token, collection, package_read), ("synthetic_PAYLOAD", True, True))
                    self.assertEqual(path, "/orgs/kira-manga/packages?package_type=maven&per_page=100&page=1")
                    calls.append(path)
                    if mode == "inventory_refusal":
                        raise receipts.ReceiptObservationRefusal(403, "response_validation")
                    return {"value": selected, "status": 200, "oauth_scopes": ["read:packages"]}

                def deleted(name, token):
                    self.assertEqual(token, "synthetic_PRIVILEGED")
                    self.assertIn(name, names)
                    calls.append(receipts.DELETED_PACKAGE_PATHS[name])
                    if mode == "deleted_refusal":
                        raise receipts.ReceiptObservationRefusal(403, "response_validation")
                    values = [{"id": 101, "name": "0.1.0"}]
                    if mode == "bad_version" and len(calls) == 2:
                        values = [{"id": 101, "name": "untrusted/synthetic_PRIVILEGED"}]
                    scopes = ["delete:packages", "read:packages"]
                    if mode == "grant_change" and len(calls) == 3:
                        scopes.append("repo")
                    return {"items": values, "oauth_scopes": scopes}

                output = io.StringIO()
                audit_token = "" if mode == "missing_audit" else "synthetic_PRIVILEGED"
                if mode == "aliased_tokens":
                    audit_token = "synthetic_PAYLOAD"
                scope = {"json": json, "reader": reader, "audit_token": audit_token,
                         "package_token": "" if mode == "missing_payload" else "synthetic_PAYLOAD",
                         "receipts": SimpleNamespace(observe_json=observe, _get_deleted_versions=deleted,
                             _validate_read_token=receipts._validate_read_token,
                             ReceiptObservationRefusal=receipts.ReceiptObservationRefusal)}
                with redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
                    exec(program, scope)
                self.assertEqual(stopped.exception.code, int(mode != "pass"))
                rows = [json.loads(line.removeprefix("KIRA_METADATA_AUDIT ")) for line in output.getvalue().splitlines()]
                complete = mode in ("pass", "deleted_refusal", "bad_version", "grant_change")
                invalid_custody = mode in ("missing_audit", "missing_payload", "aliased_tokens")
                self.assertEqual(len(calls), 16 if complete else (0 if invalid_custody else 1))
                self.assertEqual(len(rows), 16 if complete else 1)
                self.assertEqual(len(calls), len(set(calls)))
                self.assertEqual(sum(row["result"] == "FAIL" for row in rows),
                                 0 if mode == "pass" else 15 if mode == "deleted_refusal" else 1)
                self.assertTrue(all(row["method"] == "GET" and row["diagnostic_only"]
                                    and row["custody_approval"] is False for row in rows))
                self.assertEqual(rows[0]["credential_channel"], "payload_read")
                self.assertFalse(rows[0]["mutation_capable_credential"])
                if complete:
                    self.assertEqual(rows[0]["oauth_scopes"], ["read:packages"])
                    self.assertEqual({(row["package"], row["state"]) for row in rows[1:]},
                                     {(name, "deleted") for name in names})
                for row in rows[1:]:
                    self.assertEqual(row["credential_channel"], "dedicated_classic_metadata_audit")
                    self.assertTrue(row["mutation_capable_credential"])
                    if row["result"] == "PASS":
                        self.assertEqual(row["profile"], reader.AUDIT_PROFILE)
                        self.assertEqual(row["oauth_scopes"], ["delete:packages", "read:packages"])
                for row in rows:
                    if row["result"] == "FAIL":
                        self.assertIn((row["http_status"], row["stage"]),
                                      ((None, "token_validation"), (403, "response_validation"), (200, "response_validation")))
                self.assertNotIn("synthetic_PRIVILEGED", output.getvalue())
                self.assertNotIn("synthetic_PAYLOAD", output.getvalue())
                self.assertNotIn("untrusted", output.getvalue())


class RefusalDiagnosticTests(unittest.TestCase):
    def test_suppressed_chain_reports_only_trusted_code_locations(self):
        class UnprintableError(Exception):
            def __str__(self):
                raise AssertionError("Exception text must never be rendered")

        try:
            try:
                receipts._bypass_object(None, set())
            except policy.Refusal:
                raise UnprintableError("synthetic credential and response body") from None
        except UnprintableError as error:
            result = workflow.refusal_sites(error)
        self.assertRegex(result, r"github_publication_receipts\.py:[0-9]+ > publication_policy\.py:[0-9]+$")
        self.assertNotIn("test_publisher_workflow", result)
        self.assertNotIn("synthetic", result)
        self.assertNotIn("/", result)

    def test_external_filename_cycles_and_deep_chains_are_bounded(self):
        try:
            exec(compile("raise ValueError('synthetic credential')", "/untrusted/publication_policy.py", "exec"), {})
        except ValueError as error:
            error.__context__ = error
            self.assertEqual(workflow.refusal_sites(error), "unavailable")
        outer = None
        for _ in range(40):
            try:
                policy.require(False, "synthetic credential")
            except policy.Refusal as error:
                error.__cause__ = outer
                outer = error
        result = workflow.refusal_sites(outer)
        self.assertEqual(len(result.split(" > ")), 8)
        self.assertTrue(all(site.startswith("publication_policy.py:") for site in result.split(" > ")))

    def test_cli_failure_remains_failure_with_safe_location_not_input(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = workflow.main(["synthetic-secret-invalid-command"])
        self.assertEqual(result, 1)
        self.assertIn("Publisher workflow refused", stderr.getvalue())
        self.assertIn("Publisher refusal code sites: publisher_workflow.py:", stderr.getvalue())
        self.assertNotIn("synthetic-secret", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
