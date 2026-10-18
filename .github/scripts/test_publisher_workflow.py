"""Held caller composition with real byte/intake code and synthetic Git/native/API facts.

These tests are not genuine GitHub approval, Darwin execution or publication evidence.
"""

import copy
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import tarfile
import unittest
from unittest import mock

import github_publication_receipts as receipts
import publication_policy as policy
import publisher_workflow as workflow
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
                "backend_parity_ref": self.selection["backend_parity_ref"]}

    def job(self, phase, name=None):
        runtime = workflow.Runtime(self.control, self.checkout, self.root / (name or phase), self.context.copy(), phase)
        prepared = workflow.prepare(runtime, self.request(), token=self.token)
        (runtime.root / "artifact" / policy.ARCHIVE).write_bytes(self.archive_bytes)
        self.archive_path = runtime.root / policy.ARCHIVE
        self.native_asset = runtime.root / policy.GH_DARWIN_ASSET
        self.attestation = runtime.root / "attestation.json"
        workflow.write_private(self.native_asset, b"SYNTHETIC asset; native seam mocked")
        self.assertNotIn("environment", prepared)
        return runtime, prepared["selection_sha256"]

    def before(self):
        runtime, selection_hash = self.job("preflight")
        output = workflow.preflight(runtime, selection_hash, token=self.token)
        data = (runtime.root / "comparison.json").read_bytes()
        self.assert_cleaned()
        workflow.cleanup(runtime)
        self.reset_responses()
        self.events.clear()
        return data, output["record_sha256"]

    def handoff(self, runtime, data):
        (runtime.root / "handoff/comparison.json").write_bytes(data)

    def test_unconfigured_or_dispatch_policy_refuses_before_http_or_environment_output(self):
        runtime = workflow.Runtime(self.control, self.checkout, self.root / "unconfigured", self.context, "preflight")
        with mock.patch.object(workflow, "TRUSTED_POLICY", None), self.assertRaises(workflow.MissingAuthority):
            workflow.prepare(runtime, self.request(), token=self.token)
        self.source.assert_not_called()
        for extra in ({"approved": True}, {"environment": self.trusted["environment"]}, {"protection": self.trusted}):
            with self.subTest(extra=extra), self.assertRaises(policy.Refusal):
                workflow.prepare(runtime, self.request() | extra, token=self.token)
        self.assertFalse(runtime.root.exists())
        self.identity.assert_not_called()
        self.http.assert_not_called()
        self.native.assert_not_called()

    def test_preflight_real_intake_precedes_environment_output_and_binds_private_record(self):
        runtime, selection_hash = self.job("preflight")
        output = workflow.preflight(runtime, selection_hash, token=self.token)
        data = (runtime.root / "comparison.json").read_bytes()
        self.assertEqual(output, {"record_sha256": policy.digest(data),
                                  "environment": self.trusted["environment"]["name"],
                                  "environment_id": self.trusted["environment"]["id"]})
        self.assertEqual(json.loads(data)["selection"], self.selection)
        self.assertNotIn(self.token.encode(), data)
        self.assertFalse((self.checkout / "build").exists())
        self.assertEqual(len(self.requests), 14)
        self.assertLess(self.events.index("native"), next(i for i, event in enumerate(self.events) if event.startswith("http:")))
        self.native.assert_called_once()
        self.bundle.assert_called_once_with(self.selection["producer"], token=self.token)
        workflow.private_file(runtime.root / "comparison.json")
        self.assert_cleaned()
        workflow.cleanup(runtime)

    def test_recheck_repeats_live_intake_before_staging_then_holds_exact_native_command(self):
        data, record_hash = self.before()
        runtime, selection_hash = self.job("recheck")
        self.handoff(runtime, data)
        raw_stage = workflow.stage_raw

        def stage(*args):
            self.assertEqual(self.native.call_count, 2)
            self.assertEqual(len(self.requests), 14)
            self.assertFalse((self.checkout / "build").exists())
            return raw_stage(*args)

        with mock.patch.object(workflow, "stage_raw", side_effect=stage), mock.patch("subprocess.run") as process:
            with self.assertRaises(workflow.MissingAuthority):
                workflow.recheck(runtime, selection_hash, record_hash, token=self.token)
            process.assert_not_called()
        held = json.loads((runtime.root / "diagnostics/held-command.json").read_bytes())
        self.assertEqual(held["scope"], "constructed-not-executed")
        command = held["command"]
        self.assertEqual(command[:8], [str(self.checkout / "gradlew"), "--offline", "--no-daemon", "--no-parallel",
                                      "--max-workers=1", "--no-build-cache", "--no-configuration-cache", "--console=plain"])
        self.assertIn("-PkiraPublicationSealSha256=" + self.selection["producer"]["seal_sha256"], command)
        self.assertIn("-PkiraPublisherRecordSha256=" + record_hash, command)
        split = command.index(":promoteKiraPublicationBytes")
        self.assertEqual(command[split + 1:], [part for task in policy.text_lines(self.metadata["producers.txt"]) for part in ("-x", task)])
        target = self.checkout / "build/publication-bytes/input"
        for name, value in self.metadata.items():
            self.assertEqual((target / name).read_bytes(), value)
        self.assertEqual(len(list((target / "payloads").rglob("*.sha256"))), 0)
        unrelated = self.root / "unrelated"
        unrelated.write_text("retain")
        self.assert_cleaned()
        workflow.cleanup(runtime)
        self.assertFalse(runtime.root.exists())
        self.assertFalse((self.checkout / "build").exists())
        self.assertEqual(unrelated.read_text(), "retain")
        self.assertEqual(self.original_archive.read_bytes(), self.archive_bytes)

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
                                     "0" * 64 if case == "record" else record_hash, token=self.token)
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
            workflow.recheck(runtime, selected_hash, record_hash, token=self.token)
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


class PublisherWorkflowEntryTests(fixtures.OwnedFixture):
    def test_cli_strips_child_credentials_and_refuses_unconfigured_or_write_custody(self):
        workspace, temporary = self.root / "workspace", self.root / "runner-temp"
        workspace.mkdir()
        temporary.mkdir()
        env = http_fixtures.publisher_context() | {"GITHUB_ACTIONS": "true", "RUNNER_OS": "macOS", "RUNNER_ARCH": "ARM64",
              "GITHUB_WORKSPACE": str(workspace), "RUNNER_TEMP": str(temporary), "KIRA_PUBLISHER_REQUEST": "{}",
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
        self.assertNotIn("secret", errors.getvalue().lower())
        self.assertEqual(list(temporary.iterdir()), [])

    def test_workflow_uses_trusted_control_conditional_environment_exact_artifacts_and_no_writer(self):
        source = (Path(__file__).resolve().parents[1] / "workflows/publish.yml").read_text()
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("${{ inputs.access_check && github.event_name == 'workflow_dispatch'", source)
        self.assertIn("${{ !inputs.access_check && github.event_name == 'workflow_dispatch'", source)
        probe = source.split("  approval-and-access-check:", 1)[1].split("  preflight:", 1)[0]
        self.assertIn("environment: source-publication", probe)
        self.assertIn("packages: read", probe)
        self.assertEqual(probe.count("secrets."), 2)
        self.assertIn("KIRA_PUBLICATION_API_READ_TOKEN: ${{ secrets.KIRA_PUBLICATION_API_READ_TOKEN }}", probe)
        self.assertIn("KIRA_PACKAGES_READ_TOKEN: ${{ secrets.KIRA_PACKAGES_READ_TOKEN }}", probe)
        self.assertNotIn("github.token", probe)
        self.assertNotIn("GH_TOKEN:", probe)
        self.assertNotIn("outputs:", probe)
        self.assertEqual(probe.count("uses:"), 1)
        self.assertIn("uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5", probe)
        self.assertIn("persist-credentials: false", probe)
        self.assertIn("sparse-checkout-cone-mode: false", probe)
        self.assertIn("import github_publication_receipts as receipts", probe)
        self.assertIn("import remote_maven_readback as reader", probe)
        self.assertIn("receipts._get_json(path, policy_token)", probe)
        self.assertIn("receipts._get_json('/user', package_token)", probe)
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
        self.assertIn("# Remote package publication is disabled", source)
        self.assertNotIn("packages: write", source)
        self.assertNotIn("id-token: write", source)
        self.assertNotIn("run: ./gradlew", source)
        self.assertIn("name: ${{ needs.preflight.outputs.environment }}", source)
        self.assertIn("needs.preflight.result == 'success'", source)
        self.assertEqual(source.count("ref: ${{ github.workflow_sha }}"), 3)
        self.assertEqual(source.count("fetch-depth: 0"), 2)
        self.assertIn("artifact-ids: ${{ needs.preflight.outputs.record_artifact_id }}", source)
        self.assertEqual(source.count("KIRA_PUBLICATION_API_READ_TOKEN: ${{ secrets.KIRA_PUBLICATION_API_READ_TOKEN }}"), 5)
        self.assertNotIn("KIRA_PUBLICATION_API_READ_TOKEN: ${{ github.token }}", source)
        self.assertIn("cleanup-preflight", source)
        self.assertIn("cleanup-recheck", source)
        for pin in ("34e114876b0b11c390a56381ad16ebd13914f8d5", "d3f86a106a0bac45b974a628896c90dbdf5c8093",
                    "ea165f8d65b6e75b540449e92b4886f43607fa02"):
            self.assertIn(pin, source)


if __name__ == "__main__":
    unittest.main()
