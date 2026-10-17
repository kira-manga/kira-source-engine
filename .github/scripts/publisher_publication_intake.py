"""Connected fresh publisher observations; never approval, reservation or publication authority."""

import json
from pathlib import Path

import connected_publication_intake as connected
import github_publication_receipts as receipts
import publication_policy as policy


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
    policy.require(type(value) is dict and set(value) == {"producer", "publisher", "protection",
                                                        "app_parity_ref", "backend_parity_ref"},
                   "Publisher intake selection fields refused")
    producer = receipts.validated_request(value["producer"], token)
    policy.require(set(producer) == PRODUCER_FIELDS, "Unexpected producer selection fields")
    publisher, protection = receipts.validated_publisher_request(value["publisher"], value["protection"], token=token)
    policy.require(publisher["run_id"] != producer["run_id"] and publisher["workflow_id"] != producer["workflow_id"],
                   "Publisher must be a separate invocation/workflow")
    policy.matching_tag(producer["version"], protection["tag"]["name"])
    return {"producer": producer, "publisher": publisher, "protection": protection,
            "app_parity_ref": _parity_reference(value["app_parity_ref"]),
            "backend_parity_ref": _parity_reference(value["backend_parity_ref"])}


def _final_bytes_and_lineage(root, selection, archive):
    """Policy HTTP occurs after connected intake's own reread; cover that later mutation window too."""
    producer = selection["producer"]
    source = policy.source_snapshot(root, producer["source_sha"], fresh=True)
    policy.require(all(source[key] == producer[key] for key in ("source_tree", "workflow_sha256")),
                   "Final source identity changed")
    policy.require(policy.committed_version(policy.read_small(Path(root) / "gradle.properties")) == producer["version"],
                   "Final committed version changed")
    measured, _ = policy.archive_intake(archive, producer["version"], source["files"])
    policy.require(all(producer[key] == value for key, value in measured.items()), "Final candidate bytes changed")
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

    Explicit token goes only to the fixed-host GET adapter, never native verification/Git.
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
