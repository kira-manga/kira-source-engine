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


class EnvironmentConfigurationAcquisitionTests(unittest.TestCase):
    connect = ReceiptAcquisitionTests.connect
    assertClosed = ReceiptAcquisitionTests.assertClosed

    def setUp(self):
        ReceiptAcquisitionTests.setUp(self)
        self.name = "publisher/release gate"
        self.identity = 71
        self.selected = [{"id": 501, "name": "remediation/publisher", "type": "branch"},
                         {"id": 502, "name": "v*", "type": "tag"}]
        self.environment = {"id": self.identity, "name": self.name,
                            "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
                            "url": "https://untrusted.invalid/ignored", "note": "synthetic_READ_TOKEN"}
        self.inventory = {"total_count": 2,
                          "branch_policies": [item | {"node_id": "ignored"} for item in reversed(self.selected)]}
        self.resetResponses()
        self.addCleanup(self.closeResponses)

    def closeResponses(self):
        for response in self.responses:
            if not response.closed:
                response.close()

    def resetResponses(self):
        self.closeResponses()
        self.responses = [Response(self.environment), Response(self.inventory), Response(self.environment)]
        self.connections.clear()
        self.requests.clear()

    def replaceResponse(self, index, response):
        self.responses[index].close()
        self.responses[index] = response

    def acquire(self, token=None):
        return r.acquire_environment_configuration(self.name, self.identity, self.selected, token=token)

    def test_exact_gets_copy_selection_and_return_only_observations(self):
        selected = [item.copy() for item in self.selected]

        def mutate_after_preflight(host, **kwargs):
            connection = self.connect(host, **kwargs)
            if len(self.connections) == 1:
                self.selected[0]["name"] = "changed-during-http"
                self.selected.append({"id": 503, "name": "extra", "type": "branch"})
            return connection

        self.factory.side_effect = mutate_after_preflight
        token = "synthetic_READ_TOKEN"
        result = self.acquire(token)
        path = "/repos/kira-manga/kira-source-engine/environments/publisher%2Frelease%20gate"
        self.assertEqual([args for args, _ in self.requests], [
            ("GET", path), ("GET", path + "/deployment-branch-policies?per_page=100&page=1"), ("GET", path)])
        self.assertEqual(result, {"id": self.identity, "name": self.name,
                                 "deployment_branch_policy": self.environment["deployment_branch_policy"],
                                 "branch_policies": selected})
        self.assertTrue(all(kwargs["headers"]["Authorization"] == "Bearer " + token for _, kwargs in self.requests))
        self.assertNotIn(token, json.dumps(result))
        self.assertNotIn("approved", result)
        self.assertNotIn("can_admins_bypass", result)
        self.assertClosed()

    def test_bad_selection_and_shared_token_validation_make_no_network_call(self):
        names = ("", " ", ".", "..", "../release", "/release", "release/", "release//gate",
                 "release?query=1", "release#fragment", "release%2Fgate", "release\\gate",
                 "bad\nname", "\ud800", "n" * 256)
        cases = [(name, self.identity, self.selected, None) for name in names]
        cases += [(self.name, identity, self.selected, None) for identity in (True, 0, -1, 2**63, "71")]
        bad_policies = [None, {}, [], self.selected * 51, [self.selected[0], self.selected[0]],
                        [self.selected[0], self.selected[0] | {"id": 504}]]
        for field, value in (("id", True), ("id", 2**63), ("name", ""), ("name", "x\n"),
                             ("type", "Branch"), ("type", True), ("extra", "untrusted")):
            bad_policies.append([self.selected[0] | {field: value}])
        bad_policies.append([{"id": 501, "name": "missing-type"}])
        cases += [(self.name, self.identity, items, None) for items in bad_policies]
        tokens = ("", "x" * 1025, "bad\r\nheader", False)
        cases += [(self.name, self.identity, self.selected, token) for token in tokens]
        for index, (name, identity, selected, token) in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(p.Refusal) as caught:
                r.acquire_environment_configuration(name, identity, selected, token=token)
            self.assertEqual(str(caught.exception), "Environment configuration observation refused")
        for token in tokens:
            with self.assertRaises(p.Refusal):
                r.validated_request(expected(), token)
        self.assertEqual(r.validated_request(expected(), "synthetic_token"), expected())
        self.factory.assert_not_called()

    def test_environment_identity_and_explicit_policy_mode_refuse_at_both_reads(self):
        changes = [{"id": True}, {"id": 72}, {"name": "another-environment"},
                   {"deployment_branch_policy": None}, {"deployment_branch_policy": {}},
                   {"deployment_branch_policy": {"protected_branches": True, "custom_branch_policies": False}},
                   {"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": False}},
                   {"deployment_branch_policy": {"protected_branches": True, "custom_branch_policies": True}},
                   {"deployment_branch_policy": {"protected_branches": 0, "custom_branch_policies": True}},
                   {"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": 1}}]
        for index in (0, 2):
            for change in changes:
                with self.subTest(index=index, change=change):
                    self.resetResponses()
                    self.replaceResponse(index, Response(self.environment | change))
                    with self.assertRaises(p.Refusal):
                        self.acquire()
                    self.assertEqual(len(self.connections), index + 1)
                    self.assertClosed()

    def test_complete_typed_policy_inventory_is_required(self):
        lists = [[], self.selected[:1], self.selected + [{"id": 503, "name": "extra", "type": "branch"}],
                 [self.selected[0], self.selected[0]],
                 [self.selected[0], self.selected[0] | {"id": 504}],
                 [self.selected[0], False],
                 [self.selected[0], {"id": 502, "name": "missing-type"}]]
        for field, value in (("id", True), ("id", 0), ("id", 2**63), ("name", "changed"), ("name", ""),
                             ("name", "x" * 256), ("type", "branch"), ("type", "Tag"), ("type", None)):
            lists.append([self.selected[0], self.selected[1] | {field: value}])
        payloads = [{"total_count": len(items), "branch_policies": items} for items in lists]
        payloads += [self.inventory | {"total_count": count} for count in (True, "2", 1, 101)]
        payloads += [self.inventory | {"branch_policies": value} for value in (None, {}, "policies")]
        for index, payload in enumerate(payloads):
            with self.subTest(index=index):
                self.resetResponses()
                self.replaceResponse(1, Response(payload))
                with self.assertRaises(p.Refusal):
                    self.acquire()
                self.assertEqual(len(self.connections), 2)
                self.assertClosed()

    def test_http_and_header_failures_are_unknown_not_absence_or_fallback(self):
        cases = [(status, {}) for status in (401, 403, 404, 429, 500)]
        cases += [(302, {"Location": "https://untrusted.invalid/redirect"}),
                  (200, {"Link": '<https://untrusted.invalid>; rel="next"'}),
                  (200, {"Content-Encoding": "gzip"}), (200, {"Content-Type": "text/html"}),
                  (200, {"Age": "1"}), (200, {"Content-Length": str(p.MAX_META + 1)})]
        for status, headers in cases:
            with self.subTest(status=status, headers=headers):
                self.resetResponses()
                self.replaceResponse(1, Response(self.inventory, status=status, headers=headers))
                with self.assertRaises(p.Refusal):
                    self.acquire()
                self.assertEqual(len(self.requests), 2)
                self.assertClosed()

    def test_invalid_json_is_content_free_and_every_started_connection_closes(self):
        for body, headers in ((b'{"id":1,"id":2}', {}), (b"[]", {}), (b"invalid", {}),
                              (b"{}", {"Content-Length": "5"})):
            with self.subTest(body=body):
                self.resetResponses()
                self.replaceResponse(1, Response({}, body=body, headers=headers))
                with self.assertRaises(p.Refusal) as caught:
                    self.acquire()
                self.assertEqual(str(caught.exception), "Environment configuration observation refused")
                self.assertEqual(len(self.connections), 2)
                self.assertClosed()

    def test_provider_failure_and_read_budget_keep_content_free_owned_cleanup(self):
        with mock.patch.object(Response, "read1", side_effect=OSError("synthetic_SECRET_response")):
            with self.assertRaises(p.Refusal) as caught:
                self.acquire("synthetic_SECRET_token")
        self.assertEqual(str(caught.exception), "Environment configuration observation refused")
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertEqual(len(self.connections), 1)
        self.assertClosed()
        self.resetResponses()
        with mock.patch.object(r.time, "monotonic", side_effect=[0, 31]):
            with self.assertRaises(p.Refusal):
                self.acquire()
        self.assertEqual(len(self.connections), 1)
        self.assertClosed()

    def test_anonymous_observation_ignores_ambient_credentials_and_proxies(self):
        with mock.patch.dict("os.environ", {"GH_TOKEN": "synthetic_SECRET_token",
                                          "HTTPS_PROXY": "https://untrusted.invalid"}):
            result = self.acquire()
        self.assertEqual(result["branch_policies"], self.selected)
        self.assertTrue(all("Authorization" not in kwargs["headers"] for _, kwargs in self.requests))
        self.assertEqual(len(self.requests), 3)
        self.assertClosed()


if __name__ == "__main__":
    unittest.main()
