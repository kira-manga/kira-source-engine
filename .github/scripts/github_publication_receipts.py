"""Read-only official GitHub receipt acquisition; never publication/approval authority."""

from datetime import datetime, timezone
import http.client
import re
import ssl
import time
from urllib.parse import quote

import publication_policy as policy


API_HOST = "api.github.com"
SOCKET_TIMEOUT = 15
READ_SECONDS = 30


def _get_json(path, token):
    """Fixed-host, no-proxy/no-redirect GET. DNS uses the host resolver, not a hard deadline."""
    connection = http.client.HTTPSConnection(
        API_HOST, timeout=SOCKET_TIMEOUT, context=ssl.create_default_context())
    response = None
    try:
        deadline = time.monotonic() + READ_SECONDS
        headers = {"Accept": "application/vnd.github+json", "Accept-Encoding": "identity", "Cache-Control": "no-cache",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "kira-publication-receipts"}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        policy.require(response.status == 200, "Official receipt HTTP status refused")
        age = response.getheader("Age")
        policy.require(age is None or (re.fullmatch(r"[0-9]{1,8}", age) is not None and int(age) == 0),
                       "Aged or ambiguous receipt refused")
        policy.require(response.getheader("Content-Encoding", "identity").lower() == "identity",
                       "Compressed receipt refused")
        policy.require(response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
                       in ("application/json", "application/vnd.github+json"), "Non-JSON receipt refused")
        policy.require(not response.getheader("Link"), "Paginated receipt refused")
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
        path = "/repos/" + policy.REPOSITORY + "/environments/" + quote(name, safe="")
        first = _environment_identity(_get_json(path, token), name, environment_id)
        response = _get_json(path + "/deployment-branch-policies?per_page=100&page=1", token)
        policies = response.get("branch_policies")
        policy.require(type(response.get("total_count")) is int and type(policies) is list
                       and response["total_count"] == len(policies) == len(selected), "Incomplete policy list refused")
        observed = _branch_policy_inventory(policies)
        policy.require(observed == selected, "Selected policy inventory changed")
        final = _environment_identity(_get_json(path, token), name, environment_id)
        policy.require(final == first, "Environment configuration changed")
        return {"id": final[0], "name": final[1],
                "deployment_branch_policy": {"protected_branches": final[2], "custom_branch_policies": final[3]},
                "branch_policies": [{"id": identity, "name": policy_name, "type": kind}
                                    for identity, policy_name, kind in observed]}
    except Exception:
        raise policy.Refusal("Environment configuration observation refused") from None


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
