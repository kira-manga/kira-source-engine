"""Read-only official GitHub receipt acquisition; never publication/approval authority."""

from datetime import datetime, timezone
import http.client
import json
import re
import ssl
import time
from urllib.parse import quote

import publication_policy as policy


API_HOST = "api.github.com"
DEPRECATION_LINK = '<https://docs.github.com/en/rest/about-the-rest-api/api-versions>; rel="deprecation"; type="text/html"'
SOCKET_TIMEOUT = 15
READ_SECONDS = 30
PUBLISHER_WORKFLOW = ".github/workflows/publish.yml"
BYPASS_QUERY = (
    'query BranchBypass($qualifiedName: String!) { '
    'repository(owner:"kira-manga", name:"kira-source-engine") { databaseId nameWithOwner '
    'ref(qualifiedName:$qualifiedName) { name branchProtectionRule { id pattern '
    'bypassPullRequestAllowances(first:1) { totalCount nodes { id } pageInfo { hasNextPage } } } } } }'
)


def _request_json(path, token, *, bypass_branch=None):
    """Fixed-host GET or fixed bypass query; DNS has no separately enforced hard deadline."""
    body = None
    if bypass_branch is not None:
        policy.require(path == "/graphql" and token is not None, "Explicit bypass read token required")
        _validate_read_token(token)
        body = json.dumps({"query": BYPASS_QUERY,
                           "variables": {"qualifiedName": "refs/heads/" + _branch_name(bypass_branch)}},
                          sort_keys=True, separators=(",", ":")).encode("utf-8")
        policy.require(len(body) <= 4096, "Bypass request bound exceeded")
    connection = http.client.HTTPSConnection(
        API_HOST, timeout=SOCKET_TIMEOUT, context=ssl.create_default_context())
    response = None
    try:
        deadline = time.monotonic() + READ_SECONDS
        headers = {"Accept": "application/vnd.github+json", "Accept-Encoding": "identity", "Cache-Control": "no-cache",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "kira-publication-receipts"}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        if body is None:
            connection.request("GET", path, headers=headers)
        else:
            headers["Content-Type"] = "application/json"
            connection.request("POST", "/graphql", body=body, headers=headers)
        response = connection.getresponse()
        policy.require(response.status == 200, "Official receipt HTTP status refused")
        age = response.getheader("Age")
        policy.require(age is None or (re.fullmatch(r"[0-9]{1,8}", age) is not None and int(age) == 0),
                       "Aged or ambiguous receipt refused")
        policy.require(response.getheader("Content-Encoding", "identity").lower() == "identity",
                       "Compressed receipt refused")
        policy.require(response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
                       in ("application/json", "application/vnd.github+json"), "Non-JSON receipt refused")
        link = response.getheader("Link")
        policy.require(not link or link == DEPRECATION_LINK, "Paginated receipt refused")
        length = response.getheader("Content-Length")
        if length is not None:
            policy.require(re.fullmatch(r"[0-9]{1,8}", length) is not None
                           and 0 < int(length) <= policy.MAX_META, "Receipt length refused")
            length = int(length)
        data = bytearray()
        while True:
            policy.require(time.monotonic() < deadline, "Receipt read budget exceeded")
            chunk = response.read1(min(8192, policy.MAX_META + 1 - len(data)))
            policy.require(time.monotonic() < deadline, "Receipt read budget exceeded")
            if not chunk:
                break
            data.extend(chunk)
            policy.require(len(data) <= policy.MAX_META, "Receipt byte bound exceeded")
        policy.require(length is None or len(data) == length, "Truncated receipt refused")
        value = policy.json_data(bytes(data))
        policy.require(type(value) is dict, "Receipt object required")
        return value
    finally:
        try:
            if response is not None:
                response.close()
        finally:
            connection.close()


def _get_json(path, token):
    return _request_json(path, token)


def _validate_read_token(token):
    policy.require(token is None or (type(token) is str and 1 <= len(token) <= 1024
                   and re.fullmatch(r"[A-Za-z0-9_.-]+", token) is not None), "Read token syntax refused")


def validated_request(expected, token=None):
    """Validate and copy the selection before byte/native work or any HTTP request."""
    expected = policy.copy_expected_identity(expected)
    for key in ("run_id", "run_attempt", "workflow_id", "artifact_id", "producer_job_id", "attestation_job_id"):
        policy.require(expected[key] <= 2**63 - 1, "Selected identity bound exceeded")
    _validate_read_token(token)
    return expected


def _configuration_name(value):
    policy.require(type(value) is str and 1 <= len(value) <= 255 and value.isprintable()
                   and value == value.strip(), "Configuration name refused")
    policy.require(len(value.encode("utf-8")) <= 1024, "Configuration name bound exceeded")
    return value


def _branch_policy_inventory(values, *, selected=False):
    policy.require(type(values) in (list, tuple) and 1 <= len(values) <= 100, "Policy inventory bound refused")
    items = []
    for value in tuple(values):
        policy.require(type(value) is dict, "Policy record refused")
        value = value.copy()
        if selected:
            policy.require(set(value) == {"id", "name", "type"}, "Selected policy fields refused")
        identity, name, kind = value.get("id"), value.get("name"), value.get("type")
        policy.require(policy.positive(identity) and identity <= 2**63 - 1, "Policy identity refused")
        policy.require(type(kind) is str and kind in ("branch", "tag"), "Policy type refused")
        items.append((identity, _configuration_name(name), kind))
    policy.require(len({item[0] for item in items}) == len(items)
                   and len({item[1:] for item in items}) == len(items), "Duplicate policy refused")
    return tuple(sorted(items))


def _environment_identity(value, name, identity):
    policy.require(type(value.get("id")) is int and value["id"] == identity
                   and type(value.get("name")) is str and value["name"] == name, "Environment identity refused")
    flags = value.get("deployment_branch_policy")
    policy.require(type(flags) is dict and flags.get("protected_branches") is False
                   and flags.get("custom_branch_policies") is True, "Explicit branch-policy mode required")
    return value["id"], value["name"], flags["protected_branches"], flags["custom_branch_policies"]


def _environment_observation(name, environment_id, selected, token):
    path = "/repos/" + policy.REPOSITORY + "/environments/" + quote(name, safe="")
    first_value = _get_json(path, token)
    first = _environment_identity(first_value, name, environment_id)
    response = _get_json(path + "/deployment-branch-policies?per_page=100&page=1", token)
    policies = response.get("branch_policies")
    policy.require(type(response.get("total_count")) is int and type(policies) is list
                   and response["total_count"] == len(policies) == len(selected), "Incomplete policy list refused")
    observed = _branch_policy_inventory(policies)
    policy.require(observed == selected, "Selected policy inventory changed")
    final_value = _get_json(path, token)
    final = _environment_identity(final_value, name, environment_id)
    policy.require(final == first, "Environment configuration changed")
    projection = {"id": final[0], "name": final[1],
                  "deployment_branch_policy": {"protected_branches": final[2], "custom_branch_policies": final[3]},
                  "branch_policies": [{"id": identity, "name": policy_name, "type": kind}
                                      for identity, policy_name, kind in observed]}
    return projection, first_value, final_value


def acquire_environment_configuration(name, environment_id, expected_policies, *, token=None):
    """Observe an independently selected environment and exact explicit branch/tag policy list.

    This is NOT protected approval: reviewer/self-review/bypass settings are not proved.
    The list is read once; the final environment read neither freezes nor revalidates it.
    No response URL, saved receipt, environment token or caller-chosen host is consumed.
    A future StageC caller must reacquire under its separate authority at its own boundary.
    """
    try:
        name = _configuration_name(name)
        policy.require(not any(character in name for character in "\\?#%")
                       and all(part not in ("", ".", "..") for part in name.split("/")), "Environment path refused")
        policy.require(policy.positive(environment_id) and environment_id <= 2**63 - 1, "Environment ID refused")
        selected = _branch_policy_inventory(expected_policies, selected=True)
        _validate_read_token(token)
        return _environment_observation(name, environment_id, selected, token)[0]
    except Exception:
        raise policy.Refusal("Environment configuration observation refused") from None


def _selected_id(value):
    policy.require(policy.positive(value) and value <= 2**63 - 1, "Selected identity refused")
    return value


def _branch_name(value):
    _configuration_name(value)
    policy.require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value) is not None and ".." not in value
                   and all(part and not part.startswith(".") and not part.endswith((".", ".lock"))
                           for part in value.split("/")), "Literal branch name required")
    return value


def _checks(values):
    policy.require(type(values) is list and 1 <= len(values) <= 100, "Selected checks required")
    result = []
    for value in values:
        policy.require(type(value) is dict and set(value) == {"context", "app_id"}, "Check fields refused")
        result.append({"context": _configuration_name(value["context"]), "app_id": _selected_id(value["app_id"])})
    policy.require(len({item["context"] for item in result}) == len(result), "Duplicate check context refused")
    return sorted(result, key=lambda item: item["context"])


def _bypass_rule_id(value):
    # Opaque node identity: do not decode it or infer the selected rule from a response.
    policy.require(type(value) is str and re.fullmatch(r"[\x21-\x7e]{1,256}", value) is not None,
                   "Selected bypass rule identity refused")
    return value


def validated_publisher_request(publisher, protection, *, token=None):
    """Copy independently selected facts; no policy discovery or approval is performed."""
    try:
        fields = {"repository", "repository_id", "ref", "workflow_ref", "workflow_sha", "source_sha",
                  "run_id", "run_attempt", "workflow_id"}
        policy.require(type(publisher) is dict and set(publisher) == fields, "Publisher fields refused")
        publisher = publisher.copy()
        policy.require(publisher["repository"] == policy.REPOSITORY and type(publisher["ref"]) is str
                       and publisher["ref"].startswith("refs/heads/"), "Publisher repository/ref refused")
        branch = _branch_name(publisher["ref"][len("refs/heads/"):])
        policy.require(publisher["workflow_ref"] == policy.REPOSITORY + "/" + PUBLISHER_WORKFLOW + "@" + publisher["ref"]
                       and policy.hex_value(publisher["source_sha"], 40)
                       and publisher["workflow_sha"] == publisher["source_sha"], "Publisher workflow/source refused")
        for key in ("repository_id", "run_id", "run_attempt", "workflow_id"):
            _selected_id(publisher[key])
        policy.require(publisher["run_attempt"] == 1, "Publisher rerun refused")
        policy.require(type(protection) is dict and set(protection) == {"environment", "branches", "tag"},
                       "Protection selection fields refused")
        env = protection["environment"]
        policy.require(type(env) is dict and set(env) == {"id", "name", "branch_policy_id", "reviewer_ids"},
                       "Environment selection fields refused")
        name = _configuration_name(env["name"])
        policy.require(not any(c in name for c in "\\?#%")
                       and all(part not in ("", ".", "..") for part in name.split("/")), "Environment path refused")
        reviewers = env["reviewer_ids"]
        policy.require(type(reviewers) is list and 1 <= len(reviewers) <= 6, "Selected reviewers required")
        reviewers = sorted(_selected_id(value) for value in reviewers)
        policy.require(len(set(reviewers)) == len(reviewers), "Duplicate reviewer refused")
        env = {"id": _selected_id(env["id"]), "name": name,
               "branch_policy_id": _selected_id(env["branch_policy_id"]), "reviewer_ids": reviewers}
        branches = protection["branches"]
        policy.require(type(branches) is list and 1 <= len(branches) <= 2, "Selected branches required")
        copied = []
        branch_fields = {"name", "tip", "approvals", "checks"}
        for item in branches:
            policy.require(type(item) is dict and set(item) in (branch_fields, branch_fields | {"bypass_rule_id"})
                           and policy.hex_value(item["tip"], 40), "Branch selection refused")
            policy.require(_selected_id(item["approvals"]) <= 6, "Review count refused")
            branch_selection = {"name": _branch_name(item["name"]), "tip": item["tip"],
                                "approvals": item["approvals"], "checks": _checks(item["checks"])}
            if "bypass_rule_id" in item:
                branch_selection["bypass_rule_id"] = _bypass_rule_id(item["bypass_rule_id"])
            copied.append(branch_selection)
        policy.require(len({item["name"] for item in copied}) == len(copied)
                       and {item["name"] for item in copied} == {policy.BRANCH, branch}, "Lineage branches refused")
        policy.require(next(item["tip"] for item in copied if item["name"] == branch) == publisher["source_sha"],
                       "Publisher branch/source mismatch")
        tag = protection["tag"]
        policy.require(type(tag) is dict and set(tag) == {"name", "object"} and type(tag["name"]) is str
                       and 1 <= len(tag["name"]) <= 65 and policy.hex_value(tag["object"], 40), "Tag selection refused")
        policy.matching_tag(tag["name"][1:], tag["name"])
        _validate_read_token(token)
        return publisher, {"environment": env, "branches": sorted(copied, key=lambda item: item["name"]),
                           "tag": tag.copy()}
    except Exception:
        raise policy.Refusal("Publisher selection refused") from None


def _publisher_run(value, selected):
    fields = {"id": selected["run_id"], "run_attempt": 1, "workflow_id": selected["workflow_id"],
              "head_sha": selected["source_sha"], "head_branch": selected["ref"][len("refs/heads/"):],
              "path": PUBLISHER_WORKFLOW, "event": "workflow_dispatch", "status": "in_progress", "conclusion": None}
    policy.require(all(key in value and type(value[key]) is type(expected) and value[key] == expected
                       for key, expected in fields.items()), "Publisher invocation changed or ineligible")
    for key in ("repository", "head_repository"):
        repo = value.get(key)
        policy.require(type(repo) is dict and type(repo.get("id")) is int and repo["id"] == selected["repository_id"]
                       and repo.get("full_name") == policy.REPOSITORY, "Publisher repository identity refused")


def _reviewer_protection(value, selected):
    policy.require(value.get("can_admins_bypass") is False, "Explicit environment bypass denial required")
    rules = value.get("protection_rules")
    policy.require(type(rules) is list and 1 <= len(rules) <= 2
                   and all(type(rule) is dict for rule in rules), "Environment protection proof required")
    kinds = [rule.get("type") for rule in rules]
    policy.require(kinds.count("required_reviewers") == 1 and kinds.count("branch_policy") <= 1
                   and all(kind in ("required_reviewers", "branch_policy") for kind in kinds), "Unknown protection rule")
    rule = next(rule for rule in rules if rule["type"] == "required_reviewers")
    # Owner permits the authorized initiator to approve; an actual named approval
    # is still required by the native environment boundary, never inferred here.
    policy.require(rule.get("prevent_self_review") is False, "Explicit owner-authorized self-approval policy required")
    reviewers = rule.get("reviewers")
    policy.require(type(reviewers) is list and len(reviewers) == len(selected["reviewer_ids"]), "Reviewer list changed")
    identities = []
    for item in reviewers:
        policy.require(type(item) is dict and item.get("type") == "User" and type(item.get("reviewer")) is dict
                       and item["reviewer"].get("type") == "User", "Named user reviewer required")
        identities.append(_selected_id(item["reviewer"].get("id")))
    policy.require(sorted(identities) == selected["reviewer_ids"], "Selected reviewer identity changed")
    return {"reviewer_ids": sorted(identities), "prevent_self_review": False,
            "can_admins_bypass": False, "rule_types": sorted(kinds)}


def _branch_protection(value, selected):
    """Validate REST protection; return whether the absent bypass field needs fresh proof."""
    for key, enabled in (("enforce_admins", True), ("allow_force_pushes", False), ("allow_deletions", False)):
        policy.require(type(value.get(key)) is dict and value[key].get("enabled") is enabled, "Branch policy refused")
    reviews = value.get("required_pull_request_reviews")
    policy.require(type(reviews) is dict and reviews.get("dismiss_stale_reviews") is True
                   and reviews.get("require_last_push_approval") is True
                   and type(reviews.get("required_approving_review_count")) is int
                   and reviews["required_approving_review_count"] == selected["approvals"], "Branch review policy refused")
    needs_proof = "bypass_pull_request_allowances" not in reviews
    if not needs_proof:
        bypass = reviews["bypass_pull_request_allowances"]
        policy.require(type(bypass) is dict and set(bypass) == {"users", "teams", "apps"}
                       and all(type(items) is list and not items for items in bypass.values()),
                       "Explicit zero bypass required")
    checks = value.get("required_status_checks")
    policy.require(type(checks) is dict and checks.get("strict") is True
                   and _checks(checks.get("checks")) == selected["checks"], "Required check policy changed")
    contexts = checks.get("contexts")
    policy.require(type(contexts) is list and all(type(item) is str for item in contexts)
                   and sorted(contexts) == [item["context"] for item in selected["checks"]], "Ambiguous check contexts")
    return needs_proof


def _bypass_object(value, fields):
    policy.require(type(value) is dict and set(value) == fields, "Incomplete bypass proof refused")
    return value


def _acquire_branch_bypass(selected, repository_id, token):
    policy.require("bypass_rule_id" in selected, "Independently selected bypass rule required")
    proof = _request_json("/graphql", token, bypass_branch=selected["name"])
    proof = _bypass_object(proof, {"data"})  # Errors, extensions and partial/aliased data refuse.
    data = _bypass_object(proof["data"], {"repository"})
    repo = _bypass_object(data["repository"], {"databaseId", "nameWithOwner", "ref"})
    policy.require(type(repo["databaseId"]) is int and repo["databaseId"] == repository_id
                   and repo["nameWithOwner"] == policy.REPOSITORY, "Bypass repository identity refused")
    ref = _bypass_object(repo["ref"], {"name", "branchProtectionRule"})
    policy.require(ref["name"] == selected["name"], "Bypass ref identity refused")
    rule = _bypass_object(ref["branchProtectionRule"], {"id", "pattern", "bypassPullRequestAllowances"})
    policy.require(rule["id"] == selected["bypass_rule_id"] and rule["pattern"] == selected["name"],
                   "Exact selected bypass rule required")
    allowances = _bypass_object(rule["bypassPullRequestAllowances"], {"totalCount", "nodes", "pageInfo"})
    page = _bypass_object(allowances["pageInfo"], {"hasNextPage"})
    policy.require(type(allowances["totalCount"]) is int and allowances["totalCount"] == 0
                   and type(allowances["nodes"]) is list and not allowances["nodes"]
                   and page["hasNextPage"] is False, "Explicit complete zero bypass proof required")


def acquire_publisher_policy(publisher, protection, *, token=None):
    """Fresh comparison-only facts. Neither API equality nor this projection proves approval.

    Only an absent REST bypass field may use the fixed, selected-rule GraphQL proof.
    Missing/malformed proof refuses; sequential reads cannot reserve policy/ref/invocation.
    """
    try:
        publisher, protection = validated_publisher_request(publisher, protection, token=token)
        prefix = "/repos/" + policy.REPOSITORY
        run = prefix + "/actions/runs/" + str(publisher["run_id"])
        _publisher_run(_get_json(run + "/attempts/1", token), publisher)
        env = protection["environment"]
        branch = publisher["ref"][len("refs/heads/"):]
        selected = ((env["branch_policy_id"], branch, "branch"),)
        observed, first, final = _environment_observation(env["name"], env["id"], selected, token)
        first = _reviewer_protection(first, env)
        final = _reviewer_protection(final, env)
        policy.require(first == final, "Environment protection changed during observation")
        observed.update(final)
        tag = protection["tag"]
        actual = _get_json(prefix + "/git/ref/tags/" + quote(tag["name"], safe=""), token)
        obj = actual.get("object")
        policy.require(actual.get("ref") == "refs/tags/" + tag["name"] and type(obj) is dict
                       and obj.get("sha") == tag["object"] and obj.get("type") in ("commit", "tag"), "Remote tag changed")
        for item in protection["branches"]:
            path = prefix + "/branches/" + quote(item["name"], safe="")
            actual = _get_json(path, token)
            policy.require(actual.get("name") == item["name"] and actual.get("protected") is True
                           and type(actual.get("commit")) is dict and actual["commit"].get("sha") == item["tip"],
                           "Remote protected branch changed")
            if _branch_protection(_get_json(path + "/protection", token), item):
                _acquire_branch_bypass(item, publisher["repository_id"], token)
        _publisher_run(_get_json(run, token), publisher)
        return {"publisher": publisher, "environment": observed, "branches": protection["branches"], "tag": tag}
    except Exception:
        raise policy.Refusal("Publisher policy observation refused") from None


def acquire_official_receipts(expected, token=None):
    """Acquire exact attempt/jobs/artifact/current metadata; current is observed LAST.

    The caller independently selects the full tuple. Optional token is explicit, never
    discovered from environment or returned. All requests are GETs to one literal host;
    response URLs and proxy settings never drive requests. No retry, redirect, download,
    publication, approval or native-attestation claim exists here. Reacquire at the later
    decision boundary: this observation cannot reserve a run/artifact against future races.
    """
    try:
        expected = validated_request(expected, token)
        prefix = "/repos/" + policy.REPOSITORY + "/actions"
        run = prefix + "/runs/" + str(expected["run_id"])
        attempt = run + "/attempts/" + str(expected["run_attempt"])
        receipts = {
            "attempt": _get_json(attempt, token),
            "jobs": _get_json(attempt + "/jobs?per_page=100&page=1", token),
            "artifact": _get_json(prefix + "/artifacts/" + str(expected["artifact_id"]), token),
            "current": _get_json(run, token),
        }
        policy.compare_official_receipts(expected, receipts["attempt"], receipts["current"],
                                        receipts["jobs"], receipts["artifact"], datetime.now(timezone.utc))
        return receipts
    except policy.Refusal:
        raise
    except Exception:
        # No token, response content, provider message, URL or nested cause crosses the boundary.
        raise policy.Refusal("Official receipt acquisition failed") from None


def acquire_current_publisher(context, *, token=None):
    """Resolve only this actual invocation's numeric workflow ID, never approval/policy."""
    try:
        fields = {"GITHUB_REPOSITORY": "repository", "GITHUB_REPOSITORY_ID": "repository_id",
                  "GITHUB_REF": "ref", "GITHUB_WORKFLOW_REF": "workflow_ref",
                  "GITHUB_WORKFLOW_SHA": "workflow_sha", "GITHUB_SHA": "source_sha",
                  "GITHUB_RUN_ID": "run_id", "GITHUB_RUN_ATTEMPT": "run_attempt"}
        policy.require(type(context) is dict and set(context) == set(fields) | {"GITHUB_EVENT_NAME"}
                       and all(type(v) is str for v in context.values()), "Actual publisher context required")
        context = context.copy()
        policy.require(context["GITHUB_EVENT_NAME"] == "workflow_dispatch"
                       and context["GITHUB_REPOSITORY"] == policy.REPOSITORY
                       and context["GITHUB_REF"].startswith("refs/heads/"), "Publisher context refused")
        _branch_name(context["GITHUB_REF"][len("refs/heads/"):])
        policy.require(context["GITHUB_WORKFLOW_REF"] == policy.REPOSITORY + "/" + PUBLISHER_WORKFLOW + "@" + context["GITHUB_REF"]
                       and policy.hex_value(context["GITHUB_SHA"], 40)
                       and context["GITHUB_WORKFLOW_SHA"] == context["GITHUB_SHA"]
                       and context["GITHUB_RUN_ATTEMPT"] == "1", "Publisher workflow/attempt refused")
        value = {field: context[key] for key, field in fields.items()}
        for key in ("repository_id", "run_id", "run_attempt"):
            policy.require(re.fullmatch(r"[1-9][0-9]{0,18}", value[key]) is not None, "Publisher numeric identity refused")
            value[key] = _selected_id(int(value[key]))
        _validate_read_token(token)
        observed = _get_json("/repos/" + policy.REPOSITORY + "/actions/runs/" + str(value["run_id"]) + "/attempts/1", token)
        value["workflow_id"] = _selected_id(observed.get("workflow_id"))
        _publisher_run(observed, value)
        return value
    except Exception:
        raise policy.Refusal("Current publisher acquisition refused") from None


def acquire_attestation_bundle(expected, *, token=None):
    """Transport one inline official bundle; the caller MUST still run native verification.

    Ignore response URLs: only the already selected archive subject drives the fixed GET.
    No decoded predicate, supplied PASS, pagination, newest-match selection or fallback.
    """
    try:
        expected = validated_request(expected, token)
        path = "/repos/" + policy.REPOSITORY + "/attestations/sha256:" + expected["archive_sha256"] + "?per_page=1&page=1"
        response = _get_json(path, token)
        entries = response.get("attestations")
        policy.require(type(entries) is list and len(entries) == 1 and type(entries[0]) is dict,
                       "Exactly one inline attestation bundle required")
        bundle = entries[0].get("bundle")
        policy.require(type(bundle) is dict and bool(bundle), "Inline attestation bundle required")
        data = json.dumps(bundle, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        policy.require(len(data) <= policy.MAX_META, "Attestation bundle bound exceeded")
        return data
    except Exception:
        raise policy.Refusal("Attestation bundle acquisition refused") from None
