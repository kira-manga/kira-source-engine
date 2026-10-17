"""Actual adapter with synthetic HTTP responses; not live GitHub/provenance evidence."""

import copy
import io
import json
import ssl
import unittest
from unittest import mock

import github_publication_receipts as r
import publication_policy as p
from test_publication_policy import expected, receipts


def publisher_selection():
    """All IDs/refs are synthetic fixture choices, not operational release selections."""
    ref, sha = "refs/heads/remediation/publisher", "6" * 40
    publisher = {"repository": p.REPOSITORY, "repository_id": 73, "ref": ref,
                 "workflow_ref": p.REPOSITORY + "/" + r.PUBLISHER_WORKFLOW + "@" + ref,
                 "source_sha": sha, "workflow_sha": sha, "run_id": 61, "run_attempt": 1, "workflow_id": 18}
    protection = {"environment": {"id": 71, "name": "publisher/release gate", "branch_policy_id": 501,
                                   "reviewer_ids": [81, 82]},
                  "branches": [{"name": p.BRANCH, "tip": "e" * 40, "approvals": 2,
                                "checks": [{"context": "verify", "app_id": 121}]},
                               {"name": ref[len("refs/heads/"):], "tip": sha, "approvals": 2,
                                "checks": [{"context": "verify", "app_id": 121}]}],
                  "tag": {"name": "v1.2.3", "object": "9" * 40}}
    return publisher, protection


def publisher_policy_values(publisher=None, protection=None):
    publisher, protection = (publisher, protection) if publisher is not None else publisher_selection()
    branch = publisher["ref"][len("refs/heads/"):]
    run = {"id": publisher["run_id"], "run_attempt": 1, "workflow_id": publisher["workflow_id"],
           "head_sha": publisher["source_sha"], "head_branch": branch, "path": r.PUBLISHER_WORKFLOW,
           "event": "workflow_dispatch", "status": "in_progress", "conclusion": None,
           "repository": {"id": publisher["repository_id"], "full_name": p.REPOSITORY},
           "head_repository": {"id": publisher["repository_id"], "full_name": p.REPOSITORY}}
    selected = protection["environment"]
    env = {"id": selected["id"], "name": selected["name"], "can_admins_bypass": False,
           "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
           "protection_rules": [{"type": "required_reviewers", "prevent_self_review": True,
                                 "reviewers": [{"type": "User", "reviewer": {"type": "User", "id": identity}}
                                               for identity in selected["reviewer_ids"]]}, {"type": "branch_policy"}],
           "url": "https://untrusted.invalid/ignored", "note": "synthetic_PRIVATE_response"}
    inventory = {"total_count": 1,
                 "branch_policies": [{"id": selected["branch_policy_id"], "name": branch, "type": "branch"}]}
    tag = {"ref": "refs/tags/" + protection["tag"]["name"],
           "object": {"sha": protection["tag"]["object"], "type": "tag"}}
    values = [run, env, inventory, copy.deepcopy(env), tag]
    for item in sorted(protection["branches"], key=lambda value: value["name"]):
        values.append({"name": item["name"], "protected": True, "commit": {"sha": item["tip"]}})
        values.append({"enforce_admins": {"enabled": True}, "allow_force_pushes": {"enabled": False},
                       "allow_deletions": {"enabled": False},
                       "required_pull_request_reviews": {"required_approving_review_count": item["approvals"],
                                                         "dismiss_stale_reviews": True, "require_last_push_approval": True,
                                                         "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []}},
                       "required_status_checks": {"strict": True, "checks": copy.deepcopy(item["checks"]),
                                                  "contexts": [check["context"] for check in item["checks"]]}})
    return values + [copy.deepcopy(run)]


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


class PublisherPolicyAcquisitionTests(unittest.TestCase):
    connect = ReceiptAcquisitionTests.connect
    assertClosed = ReceiptAcquisitionTests.assertClosed
    closeResponses = EnvironmentConfigurationAcquisitionTests.closeResponses

    def setUp(self):
        ReceiptAcquisitionTests.setUp(self)
        self.publisher, self.protection = publisher_selection()
        self.resetResponses()
        self.addCleanup(self.closeResponses)

    def resetResponses(self, values=None):
        self.closeResponses()
        values = publisher_policy_values(self.publisher, self.protection) if values is None else values
        self.responses = [Response(value) for value in values]
        self.connections.clear()
        self.requests.clear()

    def acquire(self, token=None):
        return r.acquire_publisher_policy(self.publisher, self.protection, token=token)

    def assertRefused(self, token=None):
        with self.assertRaises(p.Refusal) as caught:
            self.acquire(token)
        self.assertEqual(str(caught.exception), "Publisher policy observation refused")
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertClosed()

    def test_exact_current_last_gets_copied_selections_and_token_only_in_transport(self):
        selected_publisher, selected_protection = copy.deepcopy((self.publisher, self.protection))

        def mutate(host, **kwargs):
            connection = self.connect(host, **kwargs)
            self.publisher["run_id"] = 999
            self.protection["environment"]["reviewer_ids"].append(999)
            self.protection["branches"][0]["checks"][0]["app_id"] = 999
            return connection

        self.factory.side_effect = mutate
        result = self.acquire("synthetic_READ_TOKEN")
        prefix = "/repos/" + p.REPOSITORY
        env = prefix + "/environments/publisher%2Frelease%20gate"
        branch = prefix + "/branches/remediation%2Fproduction-readiness-2026-09-04"
        publisher_branch = prefix + "/branches/remediation%2Fpublisher"
        self.assertEqual([args for args, _ in self.requests], [("GET", path) for path in (
            prefix + "/actions/runs/61/attempts/1", env, env + "/deployment-branch-policies?per_page=100&page=1",
            env, prefix + "/git/ref/tags/v1.2.3", branch, branch + "/protection", publisher_branch,
            publisher_branch + "/protection", prefix + "/actions/runs/61")])
        self.assertEqual(result["publisher"], selected_publisher)
        self.assertEqual(result["branches"], selected_protection["branches"])
        self.assertEqual(result["environment"]["reviewer_ids"], [81, 82])
        self.assertNotIn("approved", json.dumps(result))
        self.assertNotIn("synthetic_", json.dumps(result))
        self.assertTrue(all(kwargs["headers"]["Authorization"] == "Bearer synthetic_READ_TOKEN"
                            for _, kwargs in self.requests))
        self.assertClosed()

    def test_invalid_independent_selection_refuses_before_any_get(self):
        original = copy.deepcopy((self.publisher, self.protection))
        edits = [("publisher", "run_attempt", 2), ("publisher", "run_id", True),
                 ("publisher", "repository_id", 2**63), ("publisher", "workflow_sha", "0" * 40),
                 ("publisher", "ref", "refs/tags/v1.2.3"), ("publisher", "extra", "untrusted"),
                 ("environment", "reviewer_ids", []), ("environment", "reviewer_ids", [81, 81]),
                 ("environment", "reviewer_ids", [True]), ("environment", "name", "bad%2Fname"),
                 ("environment", "branch_policy_id", 0), ("branch", "name", "other"),
                 ("branch", "checks", []), ("branch", "checks", [{"context": "verify", "app_id": None}]),
                 ("tag", "name", "../v1.2.3"), ("tag", "object", "short")]
        for group, key, value in edits:
            with self.subTest(group=group, key=key, value=value):
                self.publisher, self.protection = copy.deepcopy(original)
                target = {"publisher": self.publisher, "environment": self.protection["environment"],
                          "branch": self.protection["branches"][0], "tag": self.protection["tag"]}[group]
                target[key] = value
                self.assertRefused()
        self.publisher, self.protection = original
        self.assertRefused("synthetic\r\nSECRET")
        self.factory.assert_not_called()

    def test_both_publisher_reads_require_this_in_progress_dispatch_attempt_one(self):
        for index in (0, 9):
            for patch in ({"status": "completed", "conclusion": "success"}, {"status": "queued"},
                          {"run_attempt": 2}, {"run_attempt": True}, {"event": "push"},
                          {"head_sha": "0" * 40}, {"head_branch": p.BRANCH}, {"workflow_id": 17},
                          {"path": p.WORKFLOW}, {"repository": {"id": 74, "full_name": p.REPOSITORY}},
                          {"head_repository": {"id": 73, "full_name": "other/repo"}}):
                with self.subTest(index=index, patch=patch):
                    values = publisher_policy_values()
                    values[index].update(patch)
                    self.resetResponses(values)
                    self.assertRefused()
                    self.assertEqual(len(self.requests), index + 1)

    def test_both_environment_reads_require_explicit_named_nonbypass_nonself_proof(self):
        for index in (1, 3):
            for kind in ("missing-bypass", "bypass", "self-review", "reviewer", "team", "missing-rules", "unknown-rule"):
                with self.subTest(index=index, kind=kind):
                    values = publisher_policy_values()
                    env = values[index]
                    if kind == "missing-bypass":
                        del env["can_admins_bypass"]
                    elif kind == "bypass":
                        env["can_admins_bypass"] = True
                    elif kind == "self-review":
                        env["protection_rules"][0]["prevent_self_review"] = False
                    elif kind == "reviewer":
                        env["protection_rules"][0]["reviewers"][0]["reviewer"]["id"] = 83
                    elif kind == "team":
                        env["protection_rules"][0]["reviewers"][0]["type"] = "Team"
                    elif kind == "missing-rules":
                        del env["protection_rules"]
                    else:
                        env["protection_rules"][1]["type"] = "custom"
                    self.resetResponses(values)
                    self.assertRefused()
                    self.assertEqual(len(self.requests), 4)

    def test_protected_lineage_checks_refuse_missing_bypass_or_ambiguous_check_authority(self):
        for index in (6, 8):
            for kind in ("admins", "force", "delete", "count", "stale", "last-push", "missing-bypass",
                         "bypass", "strict", "check-app", "contexts"):
                with self.subTest(index=index, kind=kind):
                    values = publisher_policy_values()
                    branch = values[index]
                    reviews = branch["required_pull_request_reviews"]
                    if kind in ("admins", "force", "delete"):
                        key = {"admins": "enforce_admins", "force": "allow_force_pushes", "delete": "allow_deletions"}[kind]
                        branch[key]["enabled"] = kind != "admins"
                    elif kind in ("count", "stale", "last-push"):
                        key = {"count": "required_approving_review_count", "stale": "dismiss_stale_reviews",
                               "last-push": "require_last_push_approval"}[kind]
                        reviews[key] = 1 if kind == "count" else False
                    elif kind == "missing-bypass":
                        del reviews["bypass_pull_request_allowances"]
                    elif kind == "bypass":
                        reviews["bypass_pull_request_allowances"]["apps"] = [{"id": 121}]
                    elif kind == "strict":
                        branch["required_status_checks"]["strict"] = False
                    elif kind == "check-app":
                        branch["required_status_checks"]["checks"][0]["app_id"] = None
                    else:
                        branch["required_status_checks"]["contexts"] = ["extra"]
                    self.resetResponses(values)
                    self.assertRefused()
                    self.assertEqual(len(self.requests), index + 1)

    def test_remote_tag_branch_and_policy_inventory_must_remain_exact(self):
        for index, patch in ((2, {"total_count": 2}), (4, {"object": {"sha": "0" * 40, "type": "tag"}}),
                             (5, {"protected": False}), (5, {"commit": {"sha": "0" * 40}}),
                             (7, {"name": "other"})):
            with self.subTest(index=index, patch=patch):
                values = publisher_policy_values()
                values[index].update(patch)
                self.resetResponses(values)
                self.assertRefused()
                self.assertEqual(len(self.requests), index + 1)

    def test_unknown_policy_http_and_provider_errors_never_fall_back_or_echo(self):
        for status, headers in ((403, {}), (404, {}), (302, {"Location": "https://untrusted.invalid"}),
                                (200, {"Link": '<https://untrusted.invalid>; rel="next"'})):
            with self.subTest(status=status, headers=headers):
                self.resetResponses()
                self.responses[6].close()
                self.responses[6] = Response({}, status=status, headers=headers)
                self.assertRefused()
                self.assertEqual(len(self.requests), 7)
        self.resetResponses()
        with mock.patch.object(Response, "read1", side_effect=OSError("synthetic_SECRET_response")):
            self.assertRefused("synthetic_SECRET_token")
        self.assertEqual(len(self.requests), 1)

    def test_one_selected_branch_can_bind_both_sources_without_conflating_invocations(self):
        self.publisher["ref"] = p.REF
        self.publisher["workflow_ref"] = p.REPOSITORY + "/" + r.PUBLISHER_WORKFLOW + "@" + p.REF
        self.publisher["source_sha"] = self.publisher["workflow_sha"] = self.protection["branches"][0]["tip"]
        self.protection["branches"] = self.protection["branches"][:1]
        self.resetResponses()
        with mock.patch.dict("os.environ", {"GH_TOKEN": "ignored", "HTTPS_PROXY": "https://untrusted.invalid"}):
            result = self.acquire()
        self.assertEqual(len(result["branches"]), 1)
        self.assertEqual(len(self.requests), 8)
        self.assertTrue(all("Authorization" not in kwargs["headers"] for _, kwargs in self.requests))
        self.assertClosed()


if __name__ == "__main__":
    unittest.main()
