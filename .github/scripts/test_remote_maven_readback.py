"""Synthetic fixed-host reads/index transitions only; no registry, approval or native writes."""

import copy
import hashlib
import io
import ssl
import unittest
from unittest import mock

import publication_policy as policy
import remote_maven_readback as remote
import test_publication_policy as fixtures


def index_bytes(artifact, versions, timestamp="20261017000000"):
    values = "".join("<version>" + version + "</version>" for version in versions)
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<metadata><groupId>{policy.GROUP}</groupId>'
            f'<artifactId>{artifact}</artifactId><versioning><latest>{versions[-1]}</latest>'
            f'<release>{versions[-1]}</release><versions>{values}</versions>'
            f'<lastUpdated>{timestamp}</lastUpdated></versioning></metadata>\n').encode()


class Response(io.BytesIO):
    def __init__(self, data, headers, status, on_read):
        super().__init__(data)
        self.headers, self.status, self.on_read = headers, status, on_read

    def getheaders(self):
        return list(self.headers)

    def getheader(self, key, default=None):
        values = [value for name, value in self.headers if name.lower() == key.lower()]
        return ", ".join(values) if values else default

    def read1(self, size):
        if self.on_read is not None:
            self.on_read()
        return super().read1(size)


class RemoteMavenReadbackTests(unittest.TestCase):
    def setUp(self):
        self.metadata, payloads, _ = fixtures.bundle_fixture()
        self.inventory = self.metadata["inventory.tsv"]
        self.publications = remote.cohort(self.inventory, fixtures.RELEASE)
        self.identities = sorted(self.publications)
        self.objects = dict(payloads)
        for path, data in payloads.items():
            for algorithm in policy.ALGORITHMS:
                self.objects[path + "." + algorithm] = hashlib.new(algorithm, data, usedforsecurity=False).hexdigest().encode()
        self.prior_versions = ["0.1.0", "0.1.4", "0.1.2"]  # Preserve native order, do not sort history.
        self.publish_indexes([])
        self.headers, self.status, self.connections, self.requests, self.responses = {}, {}, [], [], []
        self.on_request = self.on_read = None
        connection = mock.patch.object(remote.http.client, "HTTPSConnection", side_effect=self.connect)
        self.http = connection.start()
        self.addCleanup(connection.stop)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        tls = mock.patch.object(remote.ssl, "create_default_context", return_value=context)
        tls.start()
        self.addCleanup(tls.stop)
        self.addCleanup(self.assert_closed)

    def index_path(self, identity):
        return policy.GROUP.replace(".", "/") + "/" + self.publications[identity]["artifact"] + "/maven-metadata.xml"

    def set_index(self, identity, data):
        path = self.index_path(identity)
        self.objects[path] = data
        for algorithm in policy.ALGORITHMS:
            self.objects[path + "." + algorithm] = hashlib.new(algorithm, data, usedforsecurity=False).hexdigest().encode()

    def publish_indexes(self, completed):
        for identity, value in self.publications.items():
            versions = self.prior_versions + ([fixtures.RELEASE] if identity in completed else [])
            self.set_index(identity, index_bytes(value["artifact"], versions))

    def connect(self, host, *, timeout, context):
        self.assertEqual((host, timeout), (remote.HOST, 15))
        self.assertTrue(context.check_hostname)
        connection = mock.Mock()

        def request(method, path, *, headers):
            self.assertEqual(method, "GET")
            self.assertTrue(path.startswith(remote.PREFIX))
            self.assertEqual(headers["Accept-Encoding"], "identity")
            self.assertEqual(headers["Cache-Control"], "no-cache")
            self.assertNotIn("If-None-Match", headers)
            self.assertEqual(headers["Authorization"], "Basic cmVhZGVyOnN5bnRoZXRpY19yZWFk")
            self.requests.append(path)
            relative = path[len(remote.PREFIX):]
            data = self.objects.get(relative, b"not absence authority")
            response = Response(data, self.headers.get(relative, [("Content-Length", str(len(data)))]),
                                self.status.get(relative, 200 if relative in self.objects else 404), self.on_read)
            self.responses.append(response)
            connection.getresponse.return_value = response
            if self.on_request is not None:
                self.on_request()

        connection.request.side_effect = request
        self.connections.append(connection)
        return connection

    def assert_closed(self):
        self.assertTrue(all(response.closed for response in self.responses))
        for connection in self.connections:
            connection.close.assert_called_once()

    def before(self):
        return remote.prewrite_indexes(self.inventory, fixtures.RELEASE, username="reader", token="synthetic_read")

    def verify(self, before, completed=None):
        return remote.verify_remote(self.inventory, fixtures.RELEASE, before,
                                    self.identities if completed is None else completed,
                                    username="reader", token="synthetic_read")

    def test_all_fifteen_actual_payloads_four_sidecars_and_existing_index_history_match(self):
        before = self.before()
        self.assertEqual(len(self.requests), 15 * 5)
        # Even though fixture candidate payloads already exist, index observation is NOT unused-GAV authority.
        self.assertNotIn("unused", repr(before))
        self.publish_indexes(self.identities)
        result = self.verify(before)
        row_count = len(policy.text_lines(self.inventory))
        self.assertEqual(len(self.requests), row_count + 2 * 15 * 5)
        self.assertEqual(result["completed"], self.identities)
        self.assertEqual(result["inventory_sha256"], policy.digest(self.inventory))
        for observed in result["indexes"].values():
            self.assertEqual(observed["versions"], self.prior_versions + [fixtures.RELEASE])
        self.assertNotIn("synthetic_read", repr(result))

    def test_partial_progress_reads_only_completed_payloads_but_checks_all_pending_indexes(self):
        before = self.before()
        completed = self.identities[:2]
        self.publish_indexes(completed)
        result = self.verify(before, completed)
        self.assertEqual(result["completed"], completed)
        self.assertEqual(before[self.identities[0]]["versions"], self.prior_versions)
        pending = self.identities[-1]
        self.set_index(pending, index_bytes(self.publications[pending]["artifact"], self.prior_versions + ["9.9.9"]))
        with self.assertRaisesRegex(policy.Refusal, "^Remote native readback refused$"):
            self.verify(before, completed)

    def test_each_boundary_reads_new_completion_once_but_final_readback_ignores_progress(self):
        before = self.before()
        first, second = self.identities[:2]
        self.publish_indexes([first])
        progress = self.verify(before, [first])
        self.publish_indexes([first, second])
        count = len(self.requests)
        progress = remote.verify_remote(self.inventory, fixtures.RELEASE, before, [first, second],
                                        username="reader", token="synthetic_read", progress=progress)
        self.assertEqual(len(self.requests) - count, len(self.publications[second]["rows"]) + 15 * 5)
        # Even a same-history rewrite of a previously verified index refuses at the next boundary.
        self.set_index(first, index_bytes(self.publications[first]["artifact"], self.prior_versions + [fixtures.RELEASE], "20261017000001"))
        with self.assertRaisesRegex(policy.Refusal, "^Remote native readback refused$"):
            remote.verify_remote(self.inventory, fixtures.RELEASE, before, [first, second],
                                 username="reader", token="synthetic_read", progress=progress)
        self.publish_indexes(self.identities)
        count = len(self.requests)
        result = self.verify(before)  # Finalizer supplies no prior progress, even after earlier checks.
        self.assertEqual(result["completed"], self.identities)
        self.assertEqual(len(self.requests) - count, len(policy.text_lines(self.inventory)) + 15 * 5)

    def test_changed_truncated_extra_payload_bytes_and_any_of_four_sidecars_refuse(self):
        before = self.before()
        self.publish_indexes(self.identities)
        path = self.publications[self.identities[0]]["rows"][0][0]
        for candidate in [path] + [path + "." + algorithm for algorithm in policy.ALGORITHMS]:
            original = self.objects[candidate]
            for data in (original[:-1], original + b"\n", b"x" * len(original)):
                with self.subTest(path=candidate, size=len(data)):
                    self.objects[candidate] = data
                    with self.assertRaisesRegex(policy.Refusal, "^Remote native readback refused$"):
                        self.verify(before)
            self.objects[candidate] = original

    def test_http_auth_redirect_missing_aged_encoded_ambiguous_and_truncated_responses_refuse_without_retry(self):
        path = self.index_path(self.identities[0])
        for status in (301, 302, 304, 401, 403, 404, 500):
            with self.subTest(status=status):
                count = len(self.requests)
                self.status[path] = status
                with self.assertRaisesRegex(policy.Refusal, "^Remote pre-write index observation refused$"):
                    self.before()
                self.assertEqual(len(self.requests), count + 1)
        self.status.clear()
        for headers in ([('Age', '1')], [('Age', '-1')], [('Content-Encoding', 'gzip')],
                        [('Location', 'https://untrusted.invalid/a')], [('Content-Length', '0')],
                        [('Content-Length', str(len(self.objects[path]) + 1))],
                        [('Content-Length', '1'), ('content-length', '1')],
                        [('Content-Length', '10'), ('Transfer-Encoding', 'chunked')], [('Transfer-Encoding', 'gzip')]):
            with self.subTest(headers=headers):
                self.headers[path] = headers
                with self.assertRaisesRegex(policy.Refusal, "^Remote pre-write index observation refused$"):
                    self.before()

    def test_index_unknown_fields_entities_encodings_duplicates_and_bad_timestamp_refuse(self):
        identity = self.identities[0]
        original = self.objects[self.index_path(identity)]
        cases = [original.replace(b"<metadata>", b'<metadata unknown="x">'),
                 original.replace(b"</metadata>", b"<unknown/></metadata>"),
                 original.replace(b"<groupId>", b"<groupId/><groupId>"),
                 original.replace(b"</versions>", b"<version>0.1.0</version></versions>"),
                 original.replace(b"20261017000000", b"20261399000000"),
                 original.replace(b"<metadata>", b'<!DOCTYPE metadata [<!ENTITY test SYSTEM "file:///not-read">]><metadata>'),
                 original.decode().encode("utf-16"), b""]
        for data in cases:
            with self.subTest(size=len(data)):
                self.set_index(identity, data)
                with self.assertRaisesRegex(policy.Refusal, "^Remote pre-write index observation refused$"):
                    self.before()

    def test_native_index_must_preserve_order_append_exactly_once_and_match_all_index_sidecars(self):
        before = self.before()
        self.publish_indexes(self.identities)
        identity = self.identities[0]
        artifact, path = self.publications[identity]["artifact"], self.index_path(identity)
        original = self.objects[path]
        bad = [index_bytes(artifact, sorted(self.prior_versions) + [fixtures.RELEASE]),
               index_bytes(artifact, [fixtures.RELEASE]), index_bytes(artifact, self.prior_versions + ["9.9.9"]),
               index_bytes(artifact, self.prior_versions + [fixtures.RELEASE] * 2),
               index_bytes(artifact, self.prior_versions + [fixtures.RELEASE], "20251017000000")]
        for data in bad:
            with self.subTest(size=len(data)):
                self.set_index(identity, data)
                with self.assertRaisesRegex(policy.Refusal, "^Remote native readback refused$"):
                    self.verify(before)
        self.set_index(identity, original)
        for algorithm in policy.ALGORITHMS:
            saved = self.objects[path + "." + algorithm]
            self.objects[path + "." + algorithm] = saved + b"\n"
            with self.subTest(algorithm=algorithm), self.assertRaisesRegex(policy.Refusal, "^Remote native readback refused$"):
                self.verify(before)
            self.objects[path + "." + algorithm] = saved

    def test_invalid_custody_inventory_paths_and_progress_refuse_before_get(self):
        for username, token in [("reader:bad", "synthetic_read"), ("reader", "bad\r\ntoken"), ("", "x"), ("reader", None)]:
            with self.subTest(username=username), self.assertRaises(policy.Refusal):
                remote.prewrite_indexes(self.inventory, fixtures.RELEASE, username=username, token=token)
        original = self.inventory
        rows = policy.text_lines(original)
        for path in ("https://untrusted.invalid/a", "../../escape", "me/manga/kira/source/../escape", "//other/a"):
            columns = rows[0].split("\t")
            columns[6] = path
            self.inventory = ("\t".join(columns) + "\n" + "\n".join(rows[1:]) + "\n").encode()
            with self.assertRaises(policy.Refusal):
                self.before()
        self.http.assert_not_called()
        self.inventory = original
        before = self.before()
        count = len(self.requests)
        for completed in ([self.identities[0]] * 2, ["unknown"], list(reversed(self.identities)), [None]):
            with self.subTest(completed=completed), self.assertRaises(policy.Refusal):
                self.verify(before, completed)
        self.assertEqual(len(self.requests), count)

    def test_read_deadline_size_budget_provider_error_and_prior_copy_cleanup(self):
        path = self.index_path(self.identities[0])
        with mock.patch.object(remote, "INDEX_LIMIT", 64), self.assertRaises(policy.Refusal):
            self.before()
        self.headers[path] = []  # Also bound a streaming response with no Content-Length.
        with mock.patch.object(remote, "INDEX_LIMIT", 64), self.assertRaises(policy.Refusal):
            self.before()
        self.headers.clear()
        clock = [0]
        self.on_read = lambda: clock.__setitem__(0, 999)
        with mock.patch.object(remote.time, "monotonic", side_effect=lambda: clock[0]), self.assertRaises(policy.Refusal):
            self.before()
        self.on_read = None
        before = self.before()
        expected = copy.deepcopy(before)
        self.publish_indexes(self.identities)
        self.on_request = lambda: before[self.identities[0]]["versions"].append("mutated-caller-input")
        self.assertEqual(self.verify(before)["completed"], self.identities)
        self.on_request = None
        self.http.side_effect = OSError("synthetic_PRIVATE_provider_error")
        with self.assertRaisesRegex(policy.Refusal, "^Remote native readback refused$"):
            self.verify(expected)


if __name__ == "__main__":
    unittest.main()
