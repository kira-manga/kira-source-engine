"""Actual caller/HTTP/byte composition; synthetic Git/native/policy facts, no approval evidence."""

import copy
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
from unittest import mock
import unittest

import github_publication_receipts as receipts
import publication_policy as policy
import publisher_publication_intake as publisher
import test_connected_publication_intake as connected_fixtures
import test_github_branch_bypass_proof as bypass_fixtures
import test_github_publication_receipts as http_fixtures
import test_publication_policy as fixtures


class PublisherIntakeTests(fixtures.OwnedFixture):
    # Reuse boundary fixture helpers, not inherited test methods or supplied intake PASSs.
    patch = connected_fixtures.ConnectedIntakeTests.patch
    native_phase = connected_fixtures.ConnectedIntakeTests.native_phase
    connect = connected_fixtures.ConnectedIntakeTests.connect
    close_responses = connected_fixtures.ConnectedIntakeTests.close_responses
    assert_cleaned = connected_fixtures.ConnectedIntakeTests.assert_cleaned

    def setUp(self):
        fixtures.OwnedFixture.setUp(self)
        self.archive_path, files = self.archive()
        self.checkout = fixtures.SourceAgreementTests.checkout(self)
        measured, self.metadata = policy.archive_intake(self.archive_path, fixtures.RELEASE, files)
        producer = fixtures.expected() | measured | {"workflow_sha256": files[policy.WORKFLOW]["sha256"]}
        invocation, protection = http_fixtures.publisher_selection()
        self.selection = {"producer": producer, "publisher": invocation, "protection": protection,
                          "app_parity_ref": "synthetic-app-parity-receipt-sha256:" + "1" * 64,
                          "backend_parity_ref": "synthetic-backend-parity-receipt-sha256:" + "2" * 64}
        self.native_asset = self.root / "not-acquired-gh-asset"
        self.attestation = self.root / "not-signed.json"
        self.events, self.inventory_paths, self.connections, self.requests, self.responses = [], [], [], [], []
        self.native_selection = self.after_native = self.on_request = None
        self.reset_responses()
        self.addCleanup(self.close_responses)
        original_source, original_archive = policy.source_snapshot, policy.archive_intake

        def source(*args, **kwargs):
            self.events.append("source")
            return original_source(*args, **kwargs)

        def archive(*args, **kwargs):
            self.events.append("archive")
            return original_archive(*args, **kwargs)

        self.source = self.patch(policy, "source_snapshot", side_effect=source)
        self.archive_check = self.patch(policy, "archive_intake", side_effect=archive)
        self.native = self.patch(policy, "native_verify", side_effect=self.native_phase)
        self.http = self.patch(receipts.http.client, "HTTPSConnection", side_effect=self.connect)
        self.lineage = self.patch(policy, "release_lineage", side_effect=self.lineage_phase)

    def reset_responses(self, policy_values=None, producer_values=None):
        self.close_responses()
        producer_values = fixtures.receipts() if producer_values is None else producer_values
        policy_values = http_fixtures.publisher_policy_values() if policy_values is None else policy_values
        self.responses = [http_fixtures.Response(producer_values[key]) for key in ("attempt", "jobs", "artifact", "current")]
        self.responses.extend(http_fixtures.Response(value) for value in policy_values)
        self.connections.clear()
        self.requests.clear()

    def lineage_phase(self, root, **selection):
        self.events.append("lineage")
        self.assertEqual(root, self.checkout)
        self.assertEqual(selection, {"version": fixtures.RELEASE, "tag": "v1.2.3", "source_sha": fixtures.SHA,
                                     "source_tree": fixtures.TREE, "tag_object": "9" * 40, "branch_tip": "e" * 40})
        return {"scope": "synthetic-local-lineage-observation-only", **selection,
                "hold": "stage-c-authority-and-live-rechecks-required"}

    def preflight(self, token=None):
        return publisher.publisher_preflight(self.checkout, self.selection, self.archive_path,
                                             self.native_asset, self.attestation, token=token)

    def recheck(self, before, token=None):
        return publisher.publisher_recheck(self.checkout, self.selection, before, self.archive_path,
                                           self.native_asset, self.attestation, token=token)

    def assert_refused(self, phase="preflight", before=None, token=None):
        with self.assertRaises(policy.Refusal) as caught:
            self.preflight(token) if phase == "preflight" else self.recheck(before, token)
        self.assertEqual(str(caught.exception), "Publisher " + phase + " refused")
        self.assertTrue(caught.exception.__suppress_context__)
        self.assert_cleaned()

    def test_preflight_uses_real_connected_intake_before_policy_then_final_bytes_and_lineage(self):
        result = self.preflight()
        first_http = ["http:" + path for path in connected_fixtures.HTTP_PATHS]
        policy_http = ["http:" + path for _, path, _ in self.requests[4:]]
        self.assertEqual(self.events, ["source", "archive", "native"] + first_http + ["source", "archive"]
                         + policy_http + ["source", "archive", "lineage"])
        self.assertEqual(len(self.requests), 14)
        self.assertEqual(result["selection"], self.selection)
        self.assertEqual(result["observations"]["publisher"]["run_id"], 61)
        self.assertEqual(result["selection"]["producer"]["run_id"], 31)
        self.assertEqual(result["lineage"]["hold"], "stage-c-authority-and-live-rechecks-required")
        self.assertNotIn("approved", json.dumps(result))
        self.native.assert_called_once()
        self.assert_cleaned()

    def test_recheck_repeats_native_all_gets_and_final_rereads_without_reusing_pass(self):
        before = self.preflight()
        original = copy.deepcopy(before)
        self.assert_cleaned()
        self.reset_responses()
        self.events.clear()
        # The input projection is copied before callbacks; callers cannot change its comparison mid-read.
        self.on_request = lambda _: before["selection"].update(app_parity_ref="changed-during-recheck")
        result = self.recheck(before)
        self.assertEqual(result, original)
        self.assertEqual(len(self.requests), 14)
        self.assertEqual(self.native.call_count, 2)
        self.assertEqual(self.source.call_count, 6)
        self.assertEqual(self.archive_check.call_count, 6)
        self.assertEqual(self.lineage.call_count, 2)
        self.assert_cleaned()

    def test_recheck_reacquires_each_bypass_proof_and_binds_selected_rule_ids(self):
        protection = self.selection["protection"]
        bypass_fixtures.select_rule_ids(protection)
        values = bypass_fixtures.bypass_values(self.selection["publisher"], protection)
        self.reset_responses(values)
        before = self.preflight(bypass_fixtures.TOKEN)
        self.assertEqual(before["selection"]["protection"], protection)
        self.assertEqual(sum(method == "POST" for method, _, _ in self.requests), 2)
        self.assert_cleaned()
        self.reset_responses(values)
        self.assertEqual(self.recheck(before, bypass_fixtures.TOKEN), before)
        self.assertEqual(sum(method == "POST" for method, _, _ in self.requests), 2)
        self.assertEqual(self.native.call_count, 2)
        self.assert_cleaned()
        # New live zero-bypass proof is not enough if independently selected rule identity changed.
        protection["branches"][0]["bypass_rule_id"] = "BPR_synthetic_replacement"
        values = bypass_fixtures.bypass_values(self.selection["publisher"], protection)
        self.reset_responses(values)
        self.assert_refused("recheck", before, bypass_fixtures.TOKEN)
        self.assertEqual(sum(method == "POST" for method, _, _ in self.requests), 2)
        self.assertEqual(self.native.call_count, 3)
        self.assertEqual(self.lineage.call_count, 3)
        self.assertNotIn(bypass_fixtures.TOKEN, repr(self.native.call_args))
        self.assertNotIn(bypass_fixtures.TOKEN, repr(self.lineage.call_args))

    def test_selection_is_deep_copied_and_read_token_reaches_only_official_gets(self):
        original = copy.deepcopy(self.selection)

        def mutate():
            self.selection["producer"]["run_attempt"] = 99
            self.selection["publisher"]["run_id"] = 999
            self.selection["protection"]["environment"]["reviewer_ids"].append(999)
            self.selection["protection"]["branches"][0]["checks"][0]["app_id"] = 999
            self.selection["app_parity_ref"] = "changed"

        self.after_native = mutate
        token = "synthetic_SECRET_read_token"
        result = self.preflight(token)
        self.assertEqual(result["selection"], original)
        self.assertEqual(self.native_selection, original["producer"])
        self.assertNotIn(token, repr(self.native.call_args))
        self.assertNotIn(token, repr(self.lineage.call_args))
        self.assertNotIn(token, json.dumps(result))
        self.assertTrue(all(kwargs["headers"]["Authorization"] == "Bearer " + token for _, _, kwargs in self.requests))
        self.assert_cleaned()

    def test_missing_selection_parity_or_separate_publisher_refuses_before_work(self):
        original = copy.deepcopy(self.selection)
        cases = [None, original | {"app_parity_ref": ""}, original | {"backend_parity_ref": None},
                 original | {"app_parity_ref": "x" * 1025}, original | {"unknown": "untrusted"},
                 original | {"producer": original["producer"] | {"secret": "untrusted"}},
                 original | {"publisher": original["publisher"] | {"run_id": 31}},
                 original | {"publisher": original["publisher"] | {"workflow_id": 17}},
                 original | {"publisher": original["publisher"] | {"run_attempt": 2}}]
        for index, selection in enumerate(cases):
            with self.subTest(index=index):
                self.selection = selection
                self.assert_refused()
        self.selection = original
        self.assert_refused(token="synthetic\r\nSECRET")
        self.assert_refused(phase="recheck", before={"approved": True})
        self.assertEqual(self.events, [])
        self.native.assert_not_called()
        self.http.assert_not_called()

    def test_recheck_refuses_changed_policy_even_with_successful_candidate_intake(self):
        before = self.preflight()
        self.assert_cleaned()
        values = http_fixtures.publisher_policy_values()
        values[3]["protection_rules"][0]["reviewers"][0]["reviewer"]["id"] = 83
        self.reset_responses(values)
        self.events.clear()
        self.assert_refused("recheck", before)
        self.assertEqual(self.native.call_count, 2)
        self.assertEqual(len(self.requests), 8)
        self.assertEqual(self.lineage.call_count, 1)

    def test_changed_parity_binding_cannot_reuse_a_preflight_projection(self):
        before = self.preflight()
        self.assert_cleaned()
        self.reset_responses()
        self.selection["backend_parity_ref"] = "different-independent-parity-reference"
        self.assert_refused("recheck", before)
        self.assertEqual(self.native.call_count, 2)
        self.assertEqual(len(self.requests), 14)
        self.assertEqual(self.lineage.call_count, 2)

    def test_producer_supersession_or_publisher_completion_cannot_reuse_candidate_success(self):
        before = self.preflight()
        self.assert_cleaned()
        producer_values = fixtures.receipts()
        producer_values["current"]["run_attempt"] = 3
        self.reset_responses(producer_values=producer_values)
        self.assert_refused("recheck", before)
        self.assertEqual(len(self.requests), 4)
        values = http_fixtures.publisher_policy_values()
        values[-1].update(status="completed", conclusion="success")
        self.reset_responses(values)
        self.assert_refused("recheck", before)
        self.assertEqual(len(self.requests), 14)
        self.assertEqual(self.native.call_count, 3)
        self.assertEqual(self.lineage.call_count, 1)

    def test_policy_get_mutation_of_local_source_or_archive_fails_final_reread(self):
        source_path = self.checkout / policy.WORKFLOW
        original_source, original_archive = source_path.read_bytes(), self.archive_path.read_bytes()
        for kind in ("source", "archive"):
            with self.subTest(kind=kind):
                source_path.write_bytes(original_source)
                self.archive_path.write_bytes(original_archive)
                self.reset_responses()
                self.events.clear()

                def mutate(path):
                    if path.endswith("/actions/runs/61"):
                        target = source_path if kind == "source" else self.archive_path
                        with target.open("ab") as stream:
                            stream.write(b"changed-during-policy-http")

                self.on_request = mutate
                self.assert_refused()
                self.assertEqual(len(self.requests), 14)
                self.assertEqual(self.events[-1], kind)
        self.lineage.assert_not_called()

    def test_local_lineage_failure_is_not_satisfied_by_equal_remote_sha_strings(self):
        self.lineage.side_effect = policy.Refusal("synthetic_PRIVATE_missing_ancestor")
        self.assert_refused()
        self.assertEqual(len(self.requests), 14)
        self.native.assert_called_once()

    def test_provider_or_native_error_is_content_free_and_owned_resources_close(self):
        self.native.side_effect = OSError("synthetic_SECRET_native_output")
        self.assert_refused(token="synthetic_SECRET_token")
        self.assertFalse(self.native.call_args.args[2][1].parent.exists())
        self.http.assert_not_called()
        self.native.side_effect = self.native_phase
        self.http.side_effect = OSError("synthetic_SECRET_provider_output")
        self.assert_refused(token="synthetic_SECRET_token")
        self.assertEqual(self.native.call_count, 2)
        self.lineage.assert_not_called()


class PublisherWriterBoundaryTests(fixtures.OwnedFixture):
    # Reuse owned fixture setup only; don't rerun the prior eleven intake tests.
    setUp = PublisherIntakeTests.setUp
    patch = PublisherIntakeTests.patch
    native_phase = PublisherIntakeTests.native_phase
    connect = PublisherIntakeTests.connect
    close_responses = PublisherIntakeTests.close_responses
    assert_cleaned = PublisherIntakeTests.assert_cleaned
    reset_responses = PublisherIntakeTests.reset_responses
    lineage_phase = PublisherIntakeTests.lineage_phase
    preflight = PublisherIntakeTests.preflight

    def admitted(self):
        before = self.preflight()
        self.reset_responses()
        self.events.clear()
        self.native.reset_mock()
        self.source.reset_mock()
        self.archive_check.reset_mock()
        (self.checkout / "build").mkdir()
        return before

    def late(self, before, token=None):
        return publisher.publisher_writer_recheck(self.checkout, self.selection, before, self.archive_path, token=token)

    def test_late_rechecks_source_archive_and_all_official_facts_without_reusing_native_or_fresh_intake(self):
        before = self.admitted()
        self.assertEqual(self.late(before, "synthetic_read_only"), before)
        self.assertEqual(len(self.requests), 14)
        self.assertEqual(self.events[:2], ["source", "archive"])
        self.assertEqual(self.events[-3:], ["source", "archive", "lineage"])
        self.assertEqual([call.kwargs for call in self.source.call_args_list], [{"fresh": False}] * 2)
        self.native.assert_not_called()
        self.assertNotIn("synthetic_read_only", repr(self.lineage.call_args))
        self.assert_cleaned()
        self.reset_responses()
        with self.assertRaisesRegex(policy.Refusal, "^Publisher recheck refused$"):
            publisher.publisher_recheck(self.checkout, self.selection, before, self.archive_path,
                                        self.native_asset, self.attestation)
        self.assertEqual(self.requests, [])  # Full preflight/recheck still require a fresh checkout.
        self.native.assert_not_called()

    def test_late_owns_projection_and_selection_before_mutable_http_callbacks(self):
        before = self.admitted()
        expected = copy.deepcopy(before)

        def mutate(_):
            before["selection"]["app_parity_ref"] = "changed-after-copy"
            self.selection["publisher"]["run_id"] = 999
            self.selection["protection"]["environment"]["reviewer_ids"].append(999)

        self.on_request = mutate
        self.assertEqual(self.late(before), expected)
        self.native.assert_not_called()
        self.assert_cleaned()

    def test_late_refuses_superseded_producer_completed_publisher_and_changed_parity(self):
        before = self.admitted()
        original = copy.deepcopy(self.selection)
        for kind in ("producer", "publisher", "parity"):
            with self.subTest(kind=kind):
                self.selection = copy.deepcopy(original)
                producer_values = fixtures.receipts()
                policy_values = http_fixtures.publisher_policy_values()
                if kind == "producer":
                    producer_values["current"]["run_attempt"] = 3
                elif kind == "publisher":
                    policy_values[-1].update(status="completed", conclusion="success")
                else:
                    self.selection["app_parity_ref"] = "different-qualified-evidence-reference"
                self.reset_responses(policy_values, producer_values)
                with self.assertRaisesRegex(policy.Refusal, "^Publisher writer recheck refused$"):
                    self.late(before)
                self.native.assert_not_called()
                self.assert_cleaned()

    def test_late_refuses_source_archive_mutation_and_unmodeled_source_addition(self):
        before = self.admitted()
        source = self.checkout / policy.WORKFLOW
        original_source, original_archive = source.read_bytes(), self.archive_path.read_bytes()
        for kind in ("source", "archive"):
            with self.subTest(kind=kind):
                source.write_bytes(original_source)
                self.archive_path.write_bytes(original_archive)
                self.reset_responses()

                def mutate(path):
                    if path.endswith("/actions/runs/61"):
                        with (source if kind == "source" else self.archive_path).open("ab") as stream:
                            stream.write(b"changed-at-writer-boundary")

                self.on_request = mutate
                with self.assertRaisesRegex(policy.Refusal, "^Publisher writer recheck refused$"):
                    self.late(before)
                self.assert_cleaned()
        source.write_bytes(original_source)
        self.archive_path.write_bytes(original_archive)
        (self.checkout / "unmodeled.txt").write_text("not a generated output")
        self.reset_responses()
        with self.assertRaisesRegex(policy.Refusal, "^Publisher writer recheck refused$"):
            self.late(before)
        self.assertEqual(self.requests, [])

    def request(self, before):
        invocation = self.selection["publisher"]
        context_fields = {"GITHUB_REPOSITORY": "repository", "GITHUB_REPOSITORY_ID": "repository_id",
                          "GITHUB_REF": "ref", "GITHUB_SHA": "source_sha", "GITHUB_WORKFLOW_REF": "workflow_ref",
                          "GITHUB_WORKFLOW_SHA": "workflow_sha", "GITHUB_RUN_ID": "run_id", "GITHUB_RUN_ATTEMPT": "run_attempt"}
        publications = publisher.remote.cohort(self.metadata["inventory.tsv"], fixtures.RELEASE)
        self.synthetic_indexes = {identity: {"versions": ["0.1.0"], "latest": "0.1.0", "release": "0.1.0",
                                            "last_updated": "20260913000000", "size": 500, "sha256": "0" * 64}
                                  for identity in publications}
        return {"phase": "writer", "root": str(self.checkout), "archive": str(self.archive_path),
                "record": {"selection": self.selection, "preflight": before},
                "context": {**{key: str(invocation[field]) for key, field in context_fields.items()},
                            "GITHUB_EVENT_NAME": "workflow_dispatch"},
                "seal_sha256": self.selection["producer"]["seal_sha256"],
                "inventory_sha256": self.selection["producer"]["inventory_sha256"],
                "completed": [], "active": sorted(publications)[0], "indexes_before": None, "progress": None,
                "credentials": {"api_token": "synthetic_api_read", "username": "reader", "token": "synthetic_package_read"}}

    def test_bridge_binds_separate_invocation_and_restored_seal_before_network_and_never_invents_readback(self):
        before = self.admitted()
        request = self.request(before)
        indexes = self.patch(publisher.remote, "prewrite_indexes", return_value=self.synthetic_indexes)
        result = publisher.native_boundary(request)
        self.assertEqual(result["indexes_before"], self.synthetic_indexes)
        self.assertIsNone(result["readback"])
        self.assertEqual(result["completed"], [])
        self.assertEqual(result["hold"], "stage-c-authority-and-live-rechecks-required")
        self.assertNotIn("synthetic_package_read", json.dumps(result))
        self.assertNotIn("synthetic_api_read", repr(self.lineage.call_args))
        self.assertEqual(len(self.requests), 14)
        self.native.assert_not_called()
        self.assert_cleaned()
        for changed in (request | {"seal_sha256": "1" * 64}, request | {"approved": True},
                        request | {"context": request["context"] | {"GITHUB_RUN_ID": "31"}},
                        request | {"context": request["context"] | {"GITHUB_RUN_ATTEMPT": "2"}},
                        request | {"completed": [request["active"], request["active"]]}):
            with self.subTest(keys=sorted(changed)):
                indexes.reset_mock()
                self.reset_responses()
                with self.assertRaisesRegex(policy.Refusal, "^Native publisher boundary refused$"):
                    publisher.native_boundary(changed)
                indexes.assert_not_called()
                self.assertEqual(self.requests, [])

    def test_bridge_keeps_actual_progress_separate_from_prior_index_state_and_reobserves_policy_last(self):
        before = self.admitted()
        request = self.request(before)
        identity = request["active"]
        request.update(phase="readback", active=None, completed=[identity], indexes_before=self.synthetic_indexes)

        def readback(inventory, version, previous, completed, **credentials):
            self.events.append("remote-readback")
            self.assertEqual(completed, [identity])
            self.assertEqual(previous, self.synthetic_indexes)
            return {"completed": completed, "inventory_sha256": policy.digest(inventory), "indexes": {"synthetic": "observed"}}

        verify = self.patch(publisher.remote, "verify_remote", side_effect=readback)
        result = publisher.native_boundary(request)
        self.assertEqual(result["readback"]["completed"], [identity])
        self.assertEqual(result["indexes_before"], self.synthetic_indexes)
        self.assertLess(self.events.index("remote-readback"), next(i for i, event in enumerate(self.events) if event.startswith("http:")))
        self.assertEqual(self.events[-3:], ["source", "archive", "lineage"])
        verify.assert_called_once()
        self.native.assert_not_called()
        self.assert_cleaned()
        self.reset_responses()
        verify.reset_mock()
        request["progress"] = result["readback"]
        with self.assertRaisesRegex(policy.Refusal, "^Native publisher boundary refused$"):
            publisher.native_boundary(request)
        verify.assert_not_called()
        self.assertEqual(self.requests, [])


class NativeAttachmentSourceTests(unittest.TestCase):
    def test_cli_refuses_ambient_write_custody_and_secret_arguments_without_echo_or_work(self):
        for arguments, environment in [(["synthetic_SECRET_argument"], {}),
                                       ([], {"KIRA_MAVEN_WRITE_TOKEN": "synthetic_SECRET_write"}),
                                       ([], {"GITHUB_TOKEN": "synthetic_SECRET_ambient"})]:
            with self.subTest(environment=list(environment)):
                error = io.StringIO()
                with mock.patch.dict(os.environ, environment, clear=True), redirect_stderr(error), \
                        mock.patch.object(publisher, "native_boundary") as boundary, \
                        mock.patch.object(publisher.sys, "stdin") as stdin:
                    self.assertEqual(publisher.main(arguments), 1)
                    boundary.assert_not_called()
                    stdin.buffer.read.assert_not_called()
                self.assertEqual(error.getvalue(), "Native publisher boundary refused. No release authority.\n")

    def test_actual_native_attachment_retains_original_action_and_literal_hold_before_guard_completion(self):
        root = Path(__file__).resolve().parents[2]
        source = (root / "gradle/publication-gates.gradle.kts").read_text()
        guard = source.split('writer.doFirst("whole-cohort sealed byte guard") {', 1)[1].split("val recipe = writer.actions.toList()", 1)[0]
        self.assertLess(guard.index("remoteObservation(bundle, writer)"), guard.index('requireBytes(false, "REMOTE_HELD"'))
        self.assertLess(guard.index('requireBytes(false, "REMOTE_HELD"'), guard.index("guardedWriters.add(writer.path)"))
        self.assertIn("recipe[1] === nativeActions.single()", source)
        self.assertIn("selected.toSet() == writers.toSet()", source)
        self.assertIn("builder.environment().clear()", source)
        command = source.split('val builder = ProcessBuilder(', 1)[1].split("val child = builder.start()", 1)[0]
        self.assertNotIn("writeToken", command)
        self.assertNotIn("readToken", command)
        self.assertNotIn("apiToken", command)
        self.assertIn('"/usr/bin/python3", "-B", "-E", "-s", helper.toString()', command)
        self.assertIn("readToken != writeToken && apiToken != writeToken", source)
        progress = source.split("val prior = guarded.filter { writer ->", 1)[1].split("val completedIds", 1)[0]
        self.assertIn("val status = (writer as Task).state", progress)
        self.assertIn("status.failure == null", progress)
        snapshot = source.split('val sourceSha = jsonMap(jsonMap(record["selection"])["producer"])', 1)[1].split("val builder = ProcessBuilder(", 1)[0]
        self.assertIn('"/usr/bin/git", "--no-optional-locks"', snapshot)
        self.assertIn('"ls-tree", "-z", "--full-tree"', snapshot)
        self.assertIn("gitBuilder.environment().clear()", snapshot)
        self.assertIn('"GIT_NO_LAZY_FETCH" to "1"', snapshot)
        self.assertIn("it.readNBytes(4097)", snapshot)
        self.assertIn('digest.update("blob ${bytes.size}\\u0000".toByteArray(UTF_8))', snapshot)
        self.assertIn("HexFormat.of().formatHex(digest.digest(bytes)) == blobs.getValue(path)", snapshot)
        self.assertLess(snapshot.index("digest.digest(bytes)"), snapshot.index("Files.write(target, bytes)"))
        self.assertIn('val helper = scripts.resolve("publisher_publication_intake.py")', snapshot)
        self.assertNotIn("writeToken", snapshot)
        self.assertNotIn("readToken", snapshot)
        self.assertNotIn("apiToken", snapshot)
        model = source.split("fun modelText(", 1)[1].split("fun linkedPath(", 1)[0]
        self.assertNotIn("remoteDestination", model)
        self.assertNotIn("nativeCredentials", model)
        self.assertIn('"gradle/publication-gates.gradle.kts"', model)
        self.assertIn("Remote package publication is disabled by Engine6 current-source containment.",
                      (root / ".github/workflows/publish.yml").read_text())


if __name__ == "__main__":
    unittest.main()
