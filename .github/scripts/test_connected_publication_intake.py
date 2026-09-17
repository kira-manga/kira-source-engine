"""Connected ordering/refusals only; synthetic Git/native/HTTP, no live provenance or API."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import unittest
from unittest import mock

import connected_publication_intake as connected
import github_publication_receipts as receipts
import publication_policy as policy
import test_github_publication_receipts as http_fixtures
import test_publication_policy as fixtures


PREFIX = "/repos/kira-manga/kira-source-engine/actions/"
HTTP_PATHS = (PREFIX + "runs/31/attempts/2", PREFIX + "runs/31/attempts/2/jobs?per_page=100&page=1",
              PREFIX + "artifacts/42", PREFIX + "runs/31")


class ConnectedIntakeTests(fixtures.OwnedFixture):
    def setUp(self):
        super().setUp()
        self.archive_path, files = self.archive()
        # Reuse the synthetic committed-source/Git fixture, not its original test suite.
        self.checkout = fixtures.SourceAgreementTests.checkout(self)
        measured, self.metadata = policy.archive_intake(self.archive_path, fixtures.RELEASE, files)
        self.selection = fixtures.expected() | measured
        self.selection["workflow_sha256"] = files[policy.WORKFLOW]["sha256"]
        self.native_asset = self.root / "not-acquired-gh.tar.gz"
        self.attestation = self.root / "not-signed.json"
        self.events = []
        self.inventory_paths = []
        self.native_selection = None
        self.after_native = None
        self.on_request = None
        self.connections = []
        self.requests = []
        self.responses = []
        self.set_responses(fixtures.receipts())
        self.addCleanup(self.close_responses)

        original_source = policy.source_snapshot
        original_archive = policy.archive_intake

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

    def patch(self, target, name, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def close_responses(self):
        for response in self.responses:
            if not response.closed:
                response.close()

    def set_responses(self, values):
        self.close_responses()
        self.responses = [http_fixtures.Response(values[key]) for key in ("attempt", "jobs", "artifact", "current")]

    def native_phase(self, asset, attestation, subjects, expected):
        self.events.append("native")
        self.assertEqual((asset, attestation), (self.native_asset, self.attestation))
        self.assertEqual(subjects[0], self.archive_path)
        inventory = Path(subjects[1])
        self.inventory_paths.append(inventory)
        self.assertEqual(inventory.read_bytes(), self.metadata["inventory.tsv"])
        self.native_selection = dict(expected)
        if self.after_native is not None:
            self.after_native()

    def connect(self, host, *, timeout, context):
        self.assertEqual(host, "api.github.com")
        self.assertTrue(context.check_hostname)
        self.assertEqual(timeout, receipts.SOCKET_TIMEOUT)
        self.assertTrue(self.inventory_paths)
        self.assertFalse(self.inventory_paths[-1].parent.exists())
        connection = mock.Mock()
        connection.getresponse.return_value = self.responses[len(self.connections)]

        def request(method, path, **kwargs):
            self.events.append("http:" + path)
            self.requests.append((method, path, kwargs))
            if self.on_request is not None:
                self.on_request(path)

        connection.request.side_effect = request
        self.connections.append(connection)
        return connection

    def run_live(self, token=None):
        return connected.connected_intake(self.checkout, self.selection, self.archive_path,
                                          self.native_asset, self.attestation, token=token)

    def assert_refused(self, token=None):
        with self.assertRaises(policy.Refusal) as caught:
            self.run_live(token)
        self.assertEqual(str(caught.exception), "Connected candidate intake refused")
        self.assertTrue(caught.exception.__suppress_context__)

    def assert_cleaned(self):
        for inventory in self.inventory_paths:
            self.assertFalse(inventory.parent.exists())
        for index, connection in enumerate(self.connections):
            connection.close.assert_called_once()
            self.assertTrue(self.responses[index].closed)

    def cli_args(self):
        selected = self.root / "expected.json"
        selected.write_text(json.dumps(self.selection))
        return ["--root", str(self.checkout), "--expected", str(selected), "--archive", str(self.archive_path),
                "--gh-asset", str(self.native_asset), "--attestation", str(self.attestation)]

    def test_live_order_is_native_then_fresh_gets_then_final_local_rereads(self):
        self.assertIsNone(self.run_live())
        self.assertEqual(self.events, ["source", "archive", "native"] +
                         ["http:" + path for path in HTTP_PATHS] + ["source", "archive"])
        self.native.assert_called_once()
        self.assertTrue(all(method == "GET" for method, _, _ in self.requests))
        self.assert_cleaned()

    def test_selection_is_copied_before_native_and_token_only_reaches_get_adapter(self):
        original = dict(self.selection)
        token = "SYNTHETIC_explicit_read_token"
        self.after_native = lambda: self.selection.update(run_attempt=99, archive_sha256="9" * 64)
        self.assertIsNone(self.run_live(token))
        self.assertEqual(self.native_selection, original)
        self.assertNotIn(token, repr(self.native.call_args))
        self.assertEqual([path for _, path, _ in self.requests], list(HTTP_PATHS))
        self.assertTrue(all(kwargs["headers"]["Authorization"] == "Bearer " + token
                            for _, _, kwargs in self.requests))
        self.assert_cleaned()

    def test_invalid_selection_or_token_refuses_before_byte_native_or_http_work(self):
        valid = dict(self.selection)
        cases = [(None, None), (valid | {"version": "0.1.0-SNAPSHOT"}, None),
                 (valid | {"source_sha": "missing"}, None), (valid | {"run_id": True}, None),
                 (valid | {"artifact_id": 2**64}, None), (valid, ""),
                 (valid, "synthetic\r\nsecret"), (valid, "x" * 1025)]
        for index, (selection, token) in enumerate(cases):
            with self.subTest(case=index):
                self.selection = selection
                self.assert_refused(token)
        self.assertEqual(self.events, [])
        self.http.assert_not_called()
        self.native.assert_not_called()

    def test_initial_source_mutation_refuses_before_native_or_http(self):
        (self.checkout / "gradle.properties").write_text("VERSION_NAME=1.2.4\n")
        self.assert_refused()
        self.assertEqual(self.events, ["source"])
        self.http.assert_not_called()
        self.native.assert_not_called()

    def test_initial_archive_mutation_refuses_before_native_or_http(self):
        with self.archive_path.open("ab") as stream:
            stream.write(b"changed-before-intake")
        self.assert_refused()
        self.assertEqual(self.events, ["source", "archive"])
        self.http.assert_not_called()
        self.native.assert_not_called()

    def test_native_failure_makes_no_get_and_cleans_temporary_inventory(self):
        def fail():
            raise OSError("SYNTHETIC_private_native_path_and_content")

        self.after_native = fail
        self.assert_refused()
        self.assertEqual(self.events, ["source", "archive", "native"])
        self.http.assert_not_called()
        self.assert_cleaned()

    def test_superseded_final_run_cannot_reuse_earlier_byte_success(self):
        values = fixtures.receipts()
        values["current"]["run_attempt"] = 3
        self.set_responses(values)
        self.assert_refused()
        self.assertEqual(self.events, ["source", "archive", "native"] + ["http:" + path for path in HTTP_PATHS])
        self.source.assert_called_once()
        self.native.assert_called_once()
        self.assert_cleaned()

    def test_source_mutated_during_get_is_refused_by_final_reread(self):
        def mutate(path):
            if path == HTTP_PATHS[-1]:
                with (self.checkout / policy.WORKFLOW).open("ab") as stream:
                    stream.write(b"\n# changed during acquisition\n")

        self.on_request = mutate
        self.assert_refused()
        self.assertEqual(self.events[-1], "source")
        self.assertEqual(self.source.call_count, 2)
        self.archive_check.assert_called_once()
        self.native.assert_called_once()
        self.assert_cleaned()

    def test_archive_mutated_during_get_is_refused_by_final_reread(self):
        def mutate(path):
            if path == HTTP_PATHS[-1]:
                with self.archive_path.open("ab") as stream:
                    stream.write(b"changed-during-acquisition")

        self.on_request = mutate
        self.assert_refused()
        self.assertEqual(self.events[-2:], ["source", "archive"])
        self.assertEqual(self.archive_check.call_count, 2)
        self.native.assert_called_once()
        self.assert_cleaned()

    def test_http_failure_is_content_free_and_closes_already_owned_resources(self):
        self.responses[0].read1 = mock.Mock(side_effect=OSError("SYNTHETIC_private_provider_content"))
        self.assert_refused("SYNTHETIC_read_token")
        self.assertEqual(self.events, ["source", "archive", "native", "http:" + HTTP_PATHS[0]])
        self.assertEqual(len(self.connections), 1)
        self.assert_cleaned()

    def test_cli_is_anonymous_despite_ambient_tokens_or_proxy_settings(self):
        output = io.StringIO()
        environment = {"GH_TOKEN": "SYNTHETIC_ignored_token", "GITHUB_TOKEN": "SYNTHETIC_ignored_other",
                       "HTTPS_PROXY": "https://untrusted.invalid"}
        with mock.patch.dict(os.environ, environment), redirect_stdout(output):
            status = connected.main(self.cli_args())
        self.assertEqual(status, 0)
        self.assertIn("NOT release authority or a reservation", output.getvalue())
        self.assertTrue(all("Authorization" not in kwargs["headers"] for _, _, kwargs in self.requests))
        self.assertNotIn("SYNTHETIC", output.getvalue())
        self.assert_cleaned()

    def test_cli_refuses_token_or_saved_receipt_arguments_without_echoing_values(self):
        for option in ("--token", "--receipts"):
            output = io.StringIO()
            with self.subTest(option=option), redirect_stderr(output), self.assertRaises(SystemExit) as caught:
                connected.main(self.cli_args() + [option, "SYNTHETIC_private_value"])
            self.assertEqual(caught.exception.code, 2)
            self.assertEqual(output.getvalue(), "Connected intake arguments refused. No release authority.\n")
        self.assertEqual(self.events, [])
        self.http.assert_not_called()
        self.native.assert_not_called()

    def test_saved_receipt_cli_stays_explicitly_offline_and_retains_both_byte_guards(self):
        args = self.cli_args()
        saved = self.root / "receipts.json"
        saved.write_text(json.dumps(fixtures.receipts()))
        output = io.StringIO()
        argv = ["publication_policy.py", *args[:2], "receipt", *args[2:], "--receipts", str(saved)]
        with mock.patch("sys.argv", argv), redirect_stdout(output):
            policy.main()
        self.assertEqual(output.getvalue(), "Offline saved-receipt comparison matched. No live freshness or release authority.\n")
        self.assertEqual(self.events, ["source", "archive", "native", "source", "archive"])
        self.http.assert_not_called()
        self.native.assert_called_once()
        self.assert_cleaned()


if __name__ == "__main__":
    unittest.main()
