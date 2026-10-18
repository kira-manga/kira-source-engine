"""Connected intake and finite native-writer admission behind the real protected workflow."""

import json
import os
from pathlib import Path
import re
import sys
import time

import connected_publication_intake as connected
import github_publication_receipts as receipts
import publication_policy as policy
import remote_maven_readback as remote


PRODUCER_FIELDS = {"repository", "ref", "workflow_ref", "workflow_sha", "source_sha", "source_tree",
                   "workflow_sha256", "version", "run_id", "run_attempt", "workflow_id", "artifact_id",
                   "producer_job_id", "attestation_job_id", "archive_sha256", "inventory_sha256",
                   "seal_sha256", "transport_sha256"}


def _parity_reference(value):
    policy.require(type(value) is str and 1 <= len(value) <= 1024 and value.isprintable()
                   and value == value.strip() and len(value.encode("utf-8")) <= 1024,
                   "Independent parity reference required")
    return value


def _selection(value, token):
    fields = {"producer", "publisher", "protection", "app_parity_ref", "backend_parity_ref"}
    policy.require(type(value) is dict and set(value) in (fields, fields | {"release_window"}),
                   "Publisher intake selection fields refused")
    producer = receipts.validated_request(value["producer"], token)
    policy.require(set(producer) == PRODUCER_FIELDS, "Unexpected producer selection fields")
    publisher, protection = receipts.validated_publisher_request(value["publisher"], value["protection"], token=token)
    policy.require(publisher["run_id"] != producer["run_id"] and publisher["workflow_id"] != producer["workflow_id"],
                   "Publisher must be a separate invocation/workflow")
    policy.matching_tag(producer["version"], protection["tag"]["name"])
    selected = {"producer": producer, "publisher": publisher, "protection": protection,
                "app_parity_ref": _parity_reference(value["app_parity_ref"]),
                "backend_parity_ref": _parity_reference(value["backend_parity_ref"])}
    if "release_window" in value:
        selected["release_window"] = validate_release_window(selected | {"release_window": value["release_window"]})
    return selected


def _window_open(window):
    policy.require(window["not_before"] <= int(time.time()) < window["expires_at"], "Release window is not open")


def _metadata_copy(value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    policy.require(len(data) <= policy.MAX_META, "Window metadata bound exceeded")
    return policy.json_data(data)


def validate_release_window(selection):
    """Independent selection, not a grant flag or a claim that repo admins cannot act.

    The trusted operator separately controls their out-of-band writer custody and the
    observed org-installation inventory for this finite window. No org token is inferred.
    """
    window = selection.get("release_window")
    policy.require(type(window) is dict and set(window) == {"not_before", "expires_at", "owner",
                                                           "disabled_workflow_ids", "packages"},
                   "Explicit release window required")
    start, end = window["not_before"], window["expires_at"]
    policy.require(type(start) is int and type(end) is int and 0 < start < end < 2**53
                   and end - start <= 14400, "Release window time bound refused")
    owner = window["owner"]
    policy.require(type(owner) is dict and set(owner) == {"id", "login"} and policy.positive(owner["id"])
                   and owner["id"] <= 2**63 - 1 and type(owner["login"]) is str
                   and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", owner["login"]) is not None
                   and selection["protection"]["environment"]["reviewer_ids"] == [owner["id"]],
                   "Selected sole owner/reviewer refused")
    disabled = window["disabled_workflow_ids"]
    policy.require(type(disabled) is list and 1 <= len(disabled) < 100
                   and all(policy.positive(item) and item <= 2**63 - 1 for item in disabled)
                   and disabled == sorted(set(disabled)) and selection["publisher"]["workflow_id"] not in disabled
                   and selection["producer"]["workflow_id"] in disabled, "Selected workflow exclusion refused")
    result = {"not_before": start, "expires_at": end, "owner": owner.copy(), "disabled_workflow_ids": disabled.copy(),
              "packages": remote.package_bindings(window["packages"], selection["publisher"]["repository_id"])}
    _window_open(result)
    return result


def _repository_window(selection, window, token):
    prefix = "/repos/" + policy.REPOSITORY
    collaborators = receipts._get_collection(prefix + "/collaborators?affiliation=all&per_page=100&page=1", token)["items"]
    policy.require(len(collaborators) == 1 and type(collaborators[0]) is dict, "Unexpected repository writers")
    owner = collaborators[0]
    policy.require(type(owner.get("id")) is int and owner["id"] == window["owner"]["id"]
                   and owner.get("login") == window["owner"]["login"] and owner.get("type") == "User"
                   and type(owner.get("permissions")) is dict and owner["permissions"].get("admin") is True,
                   "Sole repository owner changed")
    policy.require(receipts._get_collection(prefix + "/teams?per_page=100&page=1", token)["items"] == [],
                   "Repository team writers present")
    permissions = receipts._get_json(prefix + "/actions/permissions/workflow", token)
    policy.require(permissions.get("default_workflow_permissions") == "read", "Actions default permission changed")
    workflows = receipts._get_json(prefix + "/actions/workflows?per_page=100&page=1", token)
    values = workflows.get("workflows")
    wanted = set(window["disabled_workflow_ids"]) | {selection["publisher"]["workflow_id"]}
    policy.require(type(values) is list and type(workflows.get("total_count")) is int
                   and workflows["total_count"] == len(values) == len(wanted), "Incomplete workflow inventory")
    observed = []
    for item in values:
        policy.require(type(item) is dict and type(item.get("id")) is int and item["id"] in wanted
                       and type(item.get("path")) is str and 1 <= len(item["path"]) <= 255
                       and item["path"].isprintable(), "Workflow identity refused")
        if item["id"] == selection["publisher"]["workflow_id"]:
            policy.require(item.get("state") == "active" and item["path"] == receipts.PUBLISHER_WORKFLOW,
                           "Selected publisher workflow is unavailable")
        else:
            policy.require(item.get("state") in ("disabled_manually", "disabled_inactivity", "disabled_fork", "deleted"),
                           "Another workflow remains enabled")
        observed.append({"id": item["id"], "path": item["path"], "state": item["state"]})
    policy.require({item["id"] for item in observed} == wanted, "Duplicate or missing workflow refused")
    for status in ("requested", "waiting", "pending", "queued", "in_progress"):
        runs = receipts._get_json(prefix + "/actions/runs?status=" + status + "&per_page=100&page=1", token)
        values = runs.get("workflow_runs")
        policy.require(type(values) is list and type(runs.get("total_count")) is int
                       and runs["total_count"] == len(values) == (1 if status == "in_progress" else 0),
                       "Another nonterminal workflow run exists")
        if values:
            receipts._publisher_run(values[0], selection["publisher"])
    return {"repository_id": selection["publisher"]["repository_id"],
            "owner": {"id": owner["id"], "login": owner["login"]}, "default_workflow_permissions": "read",
            "workflows": sorted(observed, key=lambda item: item["id"]), "active_run_id": selection["publisher"]["run_id"]}


def observe_release_window(selection, *, api_token, package_token, before=None, completed=(), progress=None):
    """Fresh repo/window and package facts. Native approval is the actual workflow boundary."""
    try:
        selected = _selection(selection, api_token)
        window = validate_release_window(selected)
        if before is not None:
            policy.require(type(before) is dict and set(before) == {"control", "registry"}, "Window baseline refused")
            before = _metadata_copy(before)
        if progress is not None:
            policy.require(before is not None and type(progress) is dict and set(progress) == {"control", "registry"},
                           "Window progress refused")
            progress = _metadata_copy(progress)
        control = _repository_window(selected, window, api_token)
        policy.require(before is None or control == before["control"], "Repository window controls changed")
        policy.require(progress is None or control == progress["control"], "Window progress controls changed")
        registry = remote.observe_registry(window["packages"], selected["publisher"]["repository_id"],
                                           selected["producer"]["version"], token=package_token,
                                           before=None if before is None else before["registry"], completed=completed,
                                           progress=None if progress is None else progress["registry"])
        _window_open(window)
        return {"control": control, "registry": registry}
    except Exception:
        raise policy.Refusal("Release window observation refused") from None


def _candidate_bytes(root, selection, archive, *, fresh):
    producer = selection["producer"]
    source = policy.source_snapshot(root, producer["source_sha"], fresh=fresh)
    policy.require(all(source[key] == producer[key] for key in ("source_tree", "workflow_sha256")),
                   "Final source identity changed")
    policy.require(policy.committed_version(policy.read_small(Path(root) / "gradle.properties")) == producer["version"],
                   "Final committed version changed")
    measured, metadata = policy.archive_intake(archive, producer["version"], source["files"])
    policy.require(all(producer[key] == value for key, value in measured.items()), "Final candidate bytes changed")
    return metadata


def _final_bytes_and_lineage(root, selection, archive, *, fresh=True):
    """Cover mutations after HTTP; fresh=False is only the held, restored native-writer seam."""
    _candidate_bytes(root, selection, archive, fresh=fresh)
    producer = selection["producer"]
    protection = selection["protection"]
    tip = next(item["tip"] for item in protection["branches"] if item["name"] == policy.BRANCH)
    return policy.release_lineage(root, version=producer["version"], tag=protection["tag"]["name"],
                                  source_sha=producer["source_sha"], source_tree=producer["source_tree"],
                                  tag_object=protection["tag"]["object"], branch_tip=tip)


def _observe(root, selection, archive, gh_asset, attestation, token):
    connected.connected_intake(root, selection["producer"], archive, gh_asset, attestation, token=token)
    observed = receipts.acquire_publisher_policy(selection["publisher"], selection["protection"], token=token)
    lineage = _final_bytes_and_lineage(root, selection, archive)
    # Parity references bind the selected evidence, not its validity or acceptance.
    return {"selection": selection, "observations": observed, "lineage": lineage}


def _projection_bytes(value):
    policy.require(type(value) is dict and set(value) == {"selection", "observations", "lineage"},
                   "Preflight comparison projection required")
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    policy.require(len(encoded) <= policy.MAX_META, "Comparison projection bound exceeded")
    return encoded


def publisher_preflight(root, selection, archive, gh_asset, attestation, *, token=None):
    """Validate actual connected intake and live policy; return only a comparison projection.

    Explicit token goes only to the fixed-host API adapter, never native verification/Git.
    This requires a fresh checkout. No workflow, environment approval or writer is invoked.
    """
    try:
        selected = _selection(selection, token)
        result = _observe(root, selected, archive, gh_asset, attestation, token)
        _projection_bytes(result)
        return result
    except Exception:
        raise policy.Refusal("Publisher preflight refused") from None


def publisher_recheck(root, selection, preflight, archive, gh_asset, attestation, *, token=None):
    """Repeat actual native/official intake and mutable observations, then compare preflight.

    A supplied projection is not authenticated approval. The future trusted caller must place
    this behind the real GitHub environment boundary, before generating/restoring build outputs.
    Success is not a lease: later writer-time checks and all release holds remain necessary.
    """
    try:
        selected = _selection(selection, token)
        before = _projection_bytes(preflight)  # Copy before native/HTTP callbacks can mutate caller inputs.
        result = _observe(root, selected, archive, gh_asset, attestation, token)
        policy.require(_projection_bytes(result) == before, "Publisher preflight facts changed")
        return result
    except Exception:
        raise policy.Refusal("Publisher recheck refused") from None


def publisher_writer_recheck(root, selection, preflight, archive, *, token=None):
    """Fresh late observations, NOT a substitute for either fresh native-attested intake.

    The Gradle caller must have restored/verified this invocation's original outputs and
    must repeat its graph/model/action/whole-output check immediately after return. Only
    generated roots may now exist; every committed source byte and archive is still checked
    before/after HTTP. This return value cannot lift any release hold or authorize a writer.
    """
    try:
        selected = _selection(selection, token)
        before = _projection_bytes(preflight)
        _candidate_bytes(root, selected, archive, fresh=False)
        receipts.acquire_official_receipts(selected["producer"], token)
        observed = receipts.acquire_publisher_policy(selected["publisher"], selected["protection"], token=token)
        lineage = _final_bytes_and_lineage(root, selected, archive, fresh=False)
        result = {"selection": selected, "observations": observed, "lineage": lineage}
        policy.require(_projection_bytes(result) == before, "Publisher boundary facts changed")
        return result
    except Exception:
        raise policy.Refusal("Publisher writer recheck refused") from None


def _invocation(context, selected):
    """Bind the comparison to this separate publisher invocation, not the producer's run."""
    fields = {"GITHUB_REPOSITORY": "repository", "GITHUB_REPOSITORY_ID": "repository_id",
              "GITHUB_REF": "ref", "GITHUB_SHA": "source_sha", "GITHUB_WORKFLOW_REF": "workflow_ref",
              "GITHUB_WORKFLOW_SHA": "workflow_sha", "GITHUB_RUN_ID": "run_id", "GITHUB_RUN_ATTEMPT": "run_attempt"}
    policy.require(type(context) is dict and set(context) == set(fields) | {"GITHUB_EVENT_NAME"}
                   and context["GITHUB_EVENT_NAME"] == "workflow_dispatch"
                   and all(type(context[key]) is str and context[key] == str(selected[field])
                           for key, field in fields.items()), "Actual publisher context differs")


def native_boundary(request):
    """Fixed Gradle stdin bridge. Credential-bearing request is never saved or returned.

    The supplied comparison is not native approval. Only the actual protected caller
    supplies write custody; this bridge observes each finite window without uploading.
    """
    try:
        fields = {"phase", "root", "archive", "record", "context", "seal_sha256", "inventory_sha256",
                  "completed", "active", "indexes_before", "progress", "window_progress", "credentials"}
        policy.require(type(request) is dict and set(request) == fields
                       and request["phase"] in ("writer", "readback"), "Boundary request fields refused")
        credentials = request["credentials"]
        policy.require(type(credentials) is dict and set(credentials) == {"api_token", "username", "token"},
                       "Explicit read custody required")
        credentials = credentials.copy()
        remote.read_credentials(credentials["username"], credentials["token"])
        receipts._validate_read_token(credentials["api_token"])
        record = _metadata_copy(request["record"])
        policy.require(type(record) is dict and set(record) == {"selection", "preflight", "window"}, "Comparison record refused")
        selected = _selection(record["selection"], credentials["api_token"])
        window = validate_release_window(selected)
        preflight = policy.json_data(_projection_bytes(record["preflight"]))
        policy.require(preflight["selection"] == selected, "Comparison selection differs")
        _invocation(request["context"], selected["publisher"])
        producer = selected["producer"]
        policy.require(request["seal_sha256"] == producer["seal_sha256"]
                       and request["inventory_sha256"] == producer["inventory_sha256"], "Restored byte binding differs")
        root, archive = Path(request["root"]), Path(request["archive"])
        policy.require(root.is_absolute() and archive.is_absolute() and not archive.resolve().is_relative_to(root.resolve()),
                       "Archive must remain outside source checkout")
        metadata = _candidate_bytes(root, selected, archive, fresh=False)
        inventory = metadata["inventory.tsv"]
        publications = remote.cohort(inventory, producer["version"])
        completed = request["completed"]
        policy.require(type(completed) is list and all(type(value) is str for value in completed)
                       and completed == sorted(set(completed)) and set(completed) <= set(publications),
                       "Actual completed native subset refused")
        completed = list(completed)
        active = request["active"]
        policy.require((request["phase"] == "writer" and active in publications and active not in completed)
                       or (request["phase"] == "readback" and active is None), "Active native publication refused")
        previous = request["indexes_before"]
        progress = request["progress"]
        window_progress = request["window_progress"]
        policy.require(request["phase"] != "readback" or progress is None, "Finalizer must reread all immutable bytes")
        if previous is None:
            policy.require(request["phase"] == "writer" and not completed and progress is None and window_progress is None,
                           "Pre-write observation cannot be reconstructed")
            previous = remote.prewrite_indexes(inventory, producer["version"],
                                              username=credentials["username"], token=credentials["token"])
            remote.refuse_existing_paths(inventory, producer["version"],
                                         username=credentials["username"], token=credentials["token"])
            observed = None  # No immutable payload was read; do not invent expected readback.
        else:
            policy.require(window_progress is not None, "Window progress cannot be reconstructed")
            previous = remote._previous_indexes(previous, publications, producer["version"])
            observed = remote.verify_remote(inventory, producer["version"], previous, completed,
                                            username=credentials["username"], token=credentials["token"], progress=progress)
            if active is not None:
                remote.refuse_existing_paths(inventory, producer["version"], username=credentials["username"],
                                             token=credentials["token"], active=active)
        actual_window = observe_release_window(selected, api_token=credentials["api_token"],
                                               package_token=credentials["token"], before=record["window"],
                                               completed=completed, progress=window_progress)
        # Read mutable official facts LAST, then source/archive/lineage. No stale preflight PASS.
        result = publisher_writer_recheck(root, selected, preflight, archive, token=credentials["api_token"])
        _window_open(window)
        return {"scope": "native-release-boundary-admitted", "completed": list(completed), "active": active,
                "seal_sha256": producer["seal_sha256"], "inventory_sha256": producer["inventory_sha256"],
                "comparison_sha256": policy.digest(_projection_bytes(result)), "indexes_before": previous,
                "readback": observed, "window": actual_window, "expires_at": window["expires_at"],
                "producer": {key: producer[key] for key in ("run_id", "source_sha", "seal_sha256", "inventory_sha256")},
                "publisher_run_id": selected["publisher"]["run_id"]}
    except Exception:
        raise policy.Refusal("Native publisher boundary refused") from None


def main(argv=None):
    """Only sanitized stdin is supported: no tokens, paths or JSON in command arguments."""
    try:
        policy.require(not (sys.argv[1:] if argv is None else argv), "Boundary arguments refused")
        policy.require(set(os.environ) <= {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE"},
                       "Unsanitized boundary environment refused")
        data = sys.stdin.buffer.read(policy.MAX_META + 1)
        policy.require(len(data) <= policy.MAX_META, "Boundary input bound exceeded")
        result = native_boundary(policy.json_data(data))
        output = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        policy.require(len(output) <= policy.MAX_META, "Boundary output bound exceeded")
        sys.stdout.buffer.write(output + b"\n")
        return 0
    except Exception:
        print("Native publisher boundary refused. No release authority.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
