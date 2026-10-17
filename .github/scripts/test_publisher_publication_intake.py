"""Actual caller/HTTP/byte composition; synthetic Git/native/policy facts, no approval evidence."""

import copy
import json
from unittest import mock
import unittest

import github_publication_receipts as receipts
import publication_policy as policy
import publisher_publication_intake as publisher
import test_connected_publication_intake as connected_fixtures
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
        super().setUp()
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


if __name__ == "__main__":
    unittest.main()
