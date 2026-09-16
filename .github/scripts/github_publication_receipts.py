"""Read-only official GitHub receipt acquisition; never publication/approval authority."""

from datetime import datetime, timezone
import http.client
import re
import ssl
import time

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


def acquire_official_receipts(expected, token=None):
    """Acquire exact attempt/jobs/artifact/current metadata; current is observed LAST.

    The caller independently selects the full tuple. Optional token is explicit, never
    discovered from environment or returned. All requests are GETs to one literal host;
    response URLs and proxy settings never drive requests. No retry, redirect, download,
    publication, approval or native-attestation claim exists here. Reacquire at the later
    decision boundary: this observation cannot reserve a run/artifact against future races.
    """
    try:
        policy.require(type(expected) is dict and len(expected) <= 64, "Expected tuple required")
        expected = dict(expected)
        policy.expected_identity(expected)
        for key in ("run_id", "run_attempt", "workflow_id", "artifact_id", "producer_job_id", "attestation_job_id"):
            policy.require(expected[key] <= 2**63 - 1, "Selected identity bound exceeded")
        policy.require(token is None or (type(token) is str and 1 <= len(token) <= 1024
                       and re.fullmatch(r"[A-Za-z0-9_.-]+", token) is not None), "Read token syntax refused")
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
