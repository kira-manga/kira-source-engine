"""Synthetic fixed-query boundary tests, not live Engine rule/permission/approval evidence."""

import copy
import json
import unittest
from unittest import mock

import github_publication_receipts as r
import publication_policy as p
import test_github_publication_receipts as fixtures


TOKEN = "synthetic_EXPLICIT_READ_TOKEN"
RULE = ("data", "repository", "ref", "branchProtectionRule")
ALLOWANCES = RULE + ("bypassPullRequestAllowances",)
PROOF_CHANGES = (
    (("errors",), [{"message": "synthetic_SECRET_error"}]), (("errors",), []),
    (("extensions",), {}), (("data",), None), (("data", "alias"), {}),
    (("data", "repository"), None), (("data", "repository", "databaseId"), 74),
    (("data", "repository", "databaseId"), True),
    (("data", "repository", "nameWithOwner"), "other/repository"),
    (("data", "repository", "ref"), None), (("data", "repository", "ref", "name"), "other-branch"),
    (RULE, None), (RULE + ("id",), "BPR_synthetic_other_rule"), (RULE + ("pattern",), "remediation/*"),
    (ALLOWANCES, None), (ALLOWANCES + ("totalCount",), 1), (ALLOWANCES + ("totalCount",), False),
    (ALLOWANCES + ("totalCount",), 0.0), (ALLOWANCES + ("nodes",), [{"id": "synthetic_actor"}]),
    (ALLOWANCES + ("nodes",), None), (ALLOWANCES + ("pageInfo",), {}),
    (ALLOWANCES + ("pageInfo", "hasNextPage"), True), (ALLOWANCES + ("pageInfo", "hasNextPage"), 0),
)


def select_rule_ids(protection):
    for index, branch in enumerate(protection["branches"]):
        branch["bypass_rule_id"] = "BPR_synthetic_" + str(index)


def zero_proof(publisher, branch):
    return {"data": {"repository": {
        "databaseId": publisher["repository_id"], "nameWithOwner": p.REPOSITORY,
        "ref": {"name": branch["name"], "branchProtectionRule": {
            "id": branch["bypass_rule_id"], "pattern": branch["name"],
            "bypassPullRequestAllowances": {"totalCount": 0, "nodes": [], "pageInfo": {"hasNextPage": False}}}}}}}


def bypass_values(publisher, protection):
    rest = fixtures.publisher_policy_values(publisher, protection)
    values = rest[:5]
    for index, branch in enumerate(sorted(protection["branches"], key=lambda item: item["name"])):
        detail, settings = rest[5 + 2 * index:7 + 2 * index]
        del settings["required_pull_request_reviews"]["bypass_pull_request_allowances"]
        values.extend((detail, settings, zero_proof(publisher, branch)))
    return values + rest[-1:]


def change_proof(proof, path, value):
    target = proof
    for field in path[:-1]:
        target = target[field]
    target[path[-1]] = value


class BranchBypassProofTests(unittest.TestCase):
    # Reuse HTTP fixtures, not inherited test methods or supplied policy PASSs.
    connect = fixtures.ReceiptAcquisitionTests.connect
    assertClosed = fixtures.ReceiptAcquisitionTests.assertClosed
    closeResponses = fixtures.EnvironmentConfigurationAcquisitionTests.closeResponses
    resetResponses = fixtures.PublisherPolicyAcquisitionTests.resetResponses
    acquire = fixtures.PublisherPolicyAcquisitionTests.acquire
    assertRefused = fixtures.PublisherPolicyAcquisitionTests.assertRefused

    def setUp(self):
        fixtures.ReceiptAcquisitionTests.setUp(self)
        self.publisher, self.protection = fixtures.publisher_selection()
        select_rule_ids(self.protection)
        self.resetResponses(bypass_values(self.publisher, self.protection))
        self.addCleanup(self.closeResponses)

    def test_fixed_authenticated_query_for_each_copied_exact_branch(self):
        selected = copy.deepcopy(self.protection["branches"])

        def mutate(host, **kwargs):
            connection = self.connect(host, **kwargs)
            self.protection["branches"][0]["bypass_rule_id"] = "changed-during-http"
            return connection

        self.factory.side_effect = mutate
        result = self.acquire(TOKEN)
        self.assertEqual(result["branches"], selected)
        self.assertEqual([args[0] for args, _ in self.requests], ["GET"] * 7 + ["POST", "GET", "GET", "POST", "GET"])
        posts = [(args, kwargs) for args, kwargs in self.requests if args[0] == "POST"]
        for (args, kwargs), branch in zip(posts, selected):
            self.assertEqual(args, ("POST", "/graphql"))
            self.assertEqual(json.loads(kwargs["body"]), {"query": r.BYPASS_QUERY,
                             "variables": {"qualifiedName": "refs/heads/" + branch["name"]}})
            self.assertLessEqual(len(kwargs["body"]), 4096)
            self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
            self.assertNotIn(TOKEN.encode(), kwargs["body"])
        self.assertTrue(all(kwargs["headers"]["Authorization"] == "Bearer " + TOKEN
                            and kwargs["headers"]["Cache-Control"] == "no-cache" for _, kwargs in self.requests))
        self.assertTrue(self.requests[-1][0][1].endswith("/actions/runs/61"))
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertNotIn("synthetic_PRIVATE_response", json.dumps(result))
        self.assertClosed()

    def test_present_rest_field_never_uses_graphql_even_when_malformed_or_nonempty(self):
        self.resetResponses()  # Explicit-empty REST needs neither GraphQL nor a token.
        self.acquire()
        self.assertEqual(len(self.requests), 10)
        self.assertTrue(all(args[0] == "GET" for args, _ in self.requests))
        self.assertClosed()
        cases = [None, False, [], {}, {"users": [], "teams": []},
                 {"users": [], "teams": [], "apps": [], "extra": []},
                 {"users": [], "teams": [], "apps": None}]
        cases += [{"users": [], "teams": [], "apps": []} | {kind: [{"id": 1}]}
                  for kind in ("users", "teams", "apps")]
        for index in (6, 8):
            for bypass in cases:
                with self.subTest(index=index, bypass=bypass):
                    values = fixtures.publisher_policy_values(self.publisher, self.protection)
                    values[index]["required_pull_request_reviews"]["bypass_pull_request_allowances"] = bypass
                    self.resetResponses(values)
                    self.assertRefused(TOKEN)
                    self.assertEqual(len(self.requests), index + 1)
                    self.assertTrue(all(args[0] == "GET" for args, _ in self.requests))

    def test_only_absent_branch_needs_selected_rule_and_explicit_token(self):
        del self.protection["branches"][0]["bypass_rule_id"]
        values = fixtures.publisher_policy_values(self.publisher, self.protection)
        del values[8]["required_pull_request_reviews"]["bypass_pull_request_allowances"]
        values.insert(9, zero_proof(self.publisher, self.protection["branches"][1]))
        self.resetResponses(values)
        self.acquire(TOKEN)
        self.assertEqual([args for args, _ in self.requests if args[0] == "POST"], [("POST", "/graphql")])
        self.assertEqual(len(self.requests), 11)
        self.assertClosed()
        select_rule_ids(self.protection)
        values = bypass_values(self.publisher, self.protection)
        for missing in ("selection", "explicit-token"):
            with self.subTest(missing=missing):
                select_rule_ids(self.protection)
                if missing == "selection":
                    del self.protection["branches"][0]["bypass_rule_id"]
                self.resetResponses(values)
                with mock.patch.dict("os.environ", {"GH_TOKEN": TOKEN, "GITHUB_TOKEN": TOKEN,
                                                    "HTTPS_PROXY": "https://untrusted.invalid"}):
                    self.assertRefused(TOKEN if missing == "selection" else None)
                self.assertEqual(len(self.requests), 7)
                self.assertTrue(all(args[0] == "GET" for args, _ in self.requests))
                if missing == "explicit-token":
                    self.assertTrue(all("Authorization" not in kwargs["headers"] for _, kwargs in self.requests))

    def test_invalid_optional_rule_selection_refuses_before_http(self):
        for identity in (None, True, 1, "", " ", "bad rule", "x\n", "é", "x" * 257):
            with self.subTest(identity=identity):
                self.protection["branches"][0]["bypass_rule_id"] = identity
                self.assertRefused(TOKEN)
        self.factory.assert_not_called()

    def test_closed_zero_proof_rejects_errors_incomplete_types_and_identity_changes(self):
        for path, value in PROOF_CHANGES:
            with self.subTest(path=path, value=value):
                values = bypass_values(self.publisher, self.protection)
                change_proof(values[7], path, value)
                self.resetResponses(values)
                self.assertRefused(TOKEN)
                self.assertEqual(len(self.requests), 8)
        values = bypass_values(self.publisher, self.protection)
        values[10] = copy.deepcopy(values[7])  # A proof for the first branch cannot authorize the second.
        self.resetResponses(values)
        self.assertRefused(TOKEN)
        self.assertEqual(len(self.requests), 11)

    def test_post_reuses_status_cache_json_bounds_and_no_redirect_no_retry_cleanup(self):
        cases = [dict(status=302, headers={"Location": "https://untrusted.invalid/secret"}),
                 dict(status=401), dict(status=403), dict(headers={"Age": "1"}),
                 dict(headers={"Content-Encoding": "gzip"}), dict(headers={"Content-Type": "text/html"}),
                 dict(headers={"Link": '<https://untrusted.invalid>; rel="next"'}),
                 dict(headers={"Content-Length": str(p.MAX_META + 1)}),
                 dict(body=b'{"data":null,"data":{}}'), dict(body=b'[]'),
                 dict(body=b'{}', headers={"Content-Length": "5"})]
        for response in cases:
            with self.subTest(response=response):
                self.resetResponses(bypass_values(self.publisher, self.protection))
                self.responses[7].close()
                self.responses[7] = fixtures.Response({}, **response)
                self.assertRefused(TOKEN)
                self.assertEqual(len(self.requests), 8)
                self.assertEqual(self.requests[-1][0], ("POST", "/graphql"))

    def test_post_provider_error_and_elapsed_budget_are_content_free_and_close(self):
        with mock.patch.object(self.responses[7], "read1", side_effect=OSError("synthetic_SECRET_response")):
            self.assertRefused(TOKEN)
        self.assertEqual(len(self.requests), 8)
        self.resetResponses(bypass_values(self.publisher, self.protection))

        def clock():
            return 31 if self.requests and self.requests[-1][0][0] == "POST" else 0

        with mock.patch.object(r.time, "monotonic", side_effect=clock):
            self.assertRefused(TOKEN)
        self.assertEqual(len(self.requests), 8)


if __name__ == "__main__":
    unittest.main()
