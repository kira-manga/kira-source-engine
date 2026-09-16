"""Actual adapter with synthetic HTTP responses; not live GitHub/provenance evidence."""

import io
import json
import ssl
import unittest
from unittest import mock

import github_publication_receipts as r
import publication_policy as p
from test_publication_policy import expected, receipts


class Response:
    def __init__(self, value, *, body=None, headers=None, status=200):
        body = json.dumps(value).encode() if body is None else body
        self.stream = io.BytesIO(body)
        self.headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        self.headers.update(headers or {})
        self.status = status
        self.closed = False

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read1(self, count):
        return self.stream.read(count)

    def close(self):
        self.closed = True
        self.stream.close()


class ReceiptAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.responses = [Response(receipts()[k]) for k in ("attempt", "jobs", "artifact", "current")]
        self.connections = []
        self.requests = []
        self.patch = mock.patch.object(r.http.client, "HTTPSConnection", side_effect=self.connect)
        self.factory = self.patch.start()
        self.addCleanup(self.patch.stop)

    def connect(self, host, *, timeout, context):
        self.assertEqual(host, "api.github.com")
        self.assertEqual(timeout, 15)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        connection = mock.Mock()
        connection.getresponse.return_value = self.responses[len(self.connections)]
        connection.request.side_effect = lambda *a, **kw: self.requests.append((a, kw))
        self.connections.append(connection)
        return connection

    def assertClosed(self):
        for index, connection in enumerate(self.connections):
            connection.close.assert_called_once()
            self.assertTrue(self.responses[index].closed)

    def test_exact_gets_latest_current_last_and_token_never_returned(self):
        token = "synthetic_READ_TOKEN"
        result = r.acquire_official_receipts(expected(), token)
        self.assertEqual(result, receipts())
        prefix = "/repos/kira-manga/kira-source-engine/actions/"
        self.assertEqual([a for a, _ in self.requests], [
            ("GET", prefix + "runs/31/attempts/2"),
            ("GET", prefix + "runs/31/attempts/2/jobs?per_page=100&page=1"),
            ("GET", prefix + "artifacts/42"), ("GET", prefix + "runs/31")])
        self.assertTrue(all(kw["headers"]["Authorization"] == "Bearer " + token for _, kw in self.requests))
        self.assertNotIn(token, json.dumps(result))
        self.assertClosed()

    def test_bad_identity_or_token_makes_no_network_call(self):
        for key, value in (("repository", "other/repo"), ("version", "0.1.0-SNAPSHOT"),
                           ("run_id", True), ("artifact_id", 2**64)):
            item = expected() | {key: value}
            with self.subTest(key=key), self.assertRaises(p.Refusal):
                r.acquire_official_receipts(item)
        for token in ("", "bad\r\nHost: other", "x" * 1025):
            with self.assertRaises(p.Refusal):
                r.acquire_official_receipts(expected(), token)
        self.factory.assert_not_called()

    def test_anonymous_is_explicit_and_proxy_environment_not_used(self):
        with mock.patch.dict("os.environ", {"HTTPS_PROXY": "https://untrusted.invalid", "GH_TOKEN": "ignored"}):
            r.acquire_official_receipts(expected())
        self.assertTrue(all("Authorization" not in kw["headers"] for _, kw in self.requests))
        self.assertClosed()

    def test_redirect_error_compression_and_pagination_fail_without_following(self):
        cases = [(302, {"Location": "https://other.invalid/token"}), (403, {}), (429, {}),
                 (200, {"Content-Encoding": "gzip"}), (200, {"Link": '<https://other.invalid>; rel="next"'}),
                 (200, {"Content-Type": "text/html"}), (200, {"Content-Length": str(p.MAX_META + 1)})]
        for status, headers in cases:
            with self.subTest(status=status, headers=headers):
                self.responses = [Response({}, headers=headers, status=status)]
                self.connections.clear()
                self.requests.clear()
                with self.assertRaises(p.Refusal):
                    r.acquire_official_receipts(expected())
                self.assertEqual(len(self.connections), 1)
                self.assertClosed()

    def test_truncated_duplicate_oversized_and_nonobject_json_fail_closed(self):
        cases = [(b'{}', {"Content-Length": "5"}), (b'{"id":1,"id":2}', {}),
                 (b'[]', {}), (b'broken', {}), (b'x' * (p.MAX_META + 1), {"Content-Length": None})]
        for body, headers in cases:
            with self.subTest(size=len(body)):
                self.responses = [Response({}, body=body, headers=headers)]
                self.connections.clear()
                with self.assertRaises(p.Refusal):
                    r.acquire_official_receipts(expected())
                self.assertClosed()

    def test_superseded_current_failed_jobs_and_expired_artifact_never_pass(self):
        for key, patch in (("current", {"run_attempt": 3}), ("current", {"conclusion": "failure"}),
                           ("jobs", {"total_count": 3}), ("artifact", {"expired": True})):
            with self.subTest(key=key, patch=patch):
                values = receipts()
                values[key].update(patch)
                self.responses = [Response(values[k]) for k in ("attempt", "jobs", "artifact", "current")]
                self.connections.clear()
                with self.assertRaises(p.Refusal):
                    r.acquire_official_receipts(expected())
                self.assertClosed()

    def test_provider_failure_is_content_free_and_connection_is_closed(self):
        with mock.patch.object(Response, "read1", side_effect=OSError("synthetic_SECRET_body")):
            with self.assertRaises(p.Refusal) as caught:
                r.acquire_official_receipts(expected(), "synthetic_SECRET_token")
        self.assertEqual(str(caught.exception), "Official receipt acquisition failed")
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertClosed()

    def test_read_budget_failure_still_closes_response(self):
        with mock.patch.object(r.time, "monotonic", side_effect=[0, 31]):
            with self.assertRaisesRegex(p.Refusal, "budget"):
                r.acquire_official_receipts(expected())
        self.assertClosed()

    def test_cache_revalidation_and_age_refusals(self):
        for age in (None, "0", "00", "1", "-1", "stale", "0,0", "", "0" * 9):
            with self.subTest(age=age):
                self.responses = [Response(receipts()[k], headers={"Age": age})
                                  for k in ("attempt", "jobs", "artifact", "current")]
                self.connections.clear()
                self.requests.clear()
                if age in (None, "0", "00"):
                    self.assertEqual(r.acquire_official_receipts(expected()), receipts())
                    self.assertEqual(len(self.connections), 4)
                else:
                    with self.assertRaisesRegex(p.Refusal, "Aged"):
                        r.acquire_official_receipts(expected())
                    self.assertEqual(len(self.connections), 1)
                self.assertTrue(all(kw["headers"]["Cache-Control"] == "no-cache" for _, kw in self.requests))
                self.assertClosed()


if __name__ == "__main__":
    unittest.main()
