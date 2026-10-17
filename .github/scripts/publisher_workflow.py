#!/usr/bin/env python3
"""Connect held publisher intake/staging. Never execute Gradle or grant package authority."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import sys
import tarfile

import github_publication_receipts as receipts
import publication_policy as policy
import publisher_publication_intake as intake


# Deliberately UNCONFIGURED. Only a separately reviewed control-source change may
# select {publisher_ref, environment, branches}; branch rules omit mutable tips.
# Neither dispatch JSON nor observed API policy can populate this authority.
TRUSTED_POLICY = None
CONTEXT_KEYS = ("GITHUB_REPOSITORY", "GITHUB_REPOSITORY_ID", "GITHUB_EVENT_NAME", "GITHUB_REF",
                "GITHUB_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_WORKFLOW_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")


class MissingAuthority(policy.Refusal):
    pass


def encoded(value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    policy.require(len(data) <= policy.MAX_META, "Workflow metadata bound exceeded")
    return data


def write_private(path, data):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(data)


def private_file(path):
    path = policy.regular(path)
    value = path.stat()
    policy.require(value.st_uid == os.getuid() and stat.S_IMODE(value.st_mode) == 0o600, "Private file custody refused")
    return path


def selected_policy():
    if TRUSTED_POLICY is None:
        raise MissingAuthority("Trusted publisher policy is unconfigured")
    value = policy.json_data(encoded(TRUSTED_POLICY))
    policy.require(type(value) is dict and set(value) == {"publisher_ref", "environment", "branches"},
                   "Trusted publisher policy fields refused")
    return value


def select(request, context, *, token):
    configured = selected_policy()  # No environment output or HTTP before independent selection.
    policy.require(type(request) is dict and set(request) == {"producer", "source_branch_tip", "tag_object",
                                                            "app_parity_ref", "backend_parity_ref"}, "Dispatch fields refused")
    request = policy.json_data(encoded(request))
    policy.require(context["GITHUB_REF"] == configured["publisher_ref"], "Unselected publisher ref")
    producer = receipts.validated_request(request["producer"], token)
    policy.require(policy.hex_value(request["source_branch_tip"], 40) and policy.hex_value(request["tag_object"], 40),
                   "Selected lineage identity refused")
    publisher = receipts.acquire_current_publisher(context, token=token)
    rules = configured["branches"]
    policy.require(type(rules) is list and 1 <= len(rules) <= 2, "Selected branch policy required")
    branches = []
    for rule in rules:
        fields = {"name", "approvals", "checks"}
        policy.require(type(rule) is dict and set(rule) in (fields, fields | {"bypass_rule_id"}), "Trusted branch fields refused")
        tip = request["source_branch_tip"] if rule["name"] == policy.BRANCH else publisher["source_sha"]
        branches.append(rule | {"tip": tip})
    return intake._selection({"producer": producer, "publisher": publisher,
                              "protection": {"environment": configured["environment"], "branches": branches,
                                             "tag": {"name": "v" + producer["version"], "object": request["tag_object"]}},
                              "app_parity_ref": request["app_parity_ref"], "backend_parity_ref": request["backend_parity_ref"]}, token)


@dataclass(frozen=True)
class Runtime:
    control: Path
    producer: Path
    root: Path
    context: dict
    phase: str

    def owner(self):
        return {"context": self.context, "phase": self.phase, "control": str(self.control), "producer": str(self.producer)}

    def check(self):
        policy.require(not any(p.is_symlink() for p in (self.root, *self.root.parents)) and self.root.is_dir()
                       and self.root.stat().st_uid == os.getuid() and stat.S_IMODE(self.root.stat().st_mode) == 0o700,
                       "Owned workflow root refused")
        policy.require(policy.json_data(policy.read_small(private_file(self.root / "owner.json"))) == self.owner(),
                       "Workflow owner changed")

    def selection(self, digest, *, token):
        self.check()
        data = policy.read_small(private_file(self.root / "selection.json"))
        policy.require(policy.hex_value(digest) and policy.digest(data) == digest, "Prepared selection changed")
        selected = intake._selection(policy.json_data(data), token)
        intake._invocation(self.context, selected["publisher"])
        configured = selected_policy()
        rules = [{k: v for k, v in item.items() if k != "tip"} for item in selected["protection"]["branches"]]
        policy.require(selected["publisher"]["ref"] == configured["publisher_ref"]
                       and selected["protection"]["environment"] == configured["environment"]
                       and rules == sorted(configured["branches"], key=lambda item: item["name"]), "Trusted policy changed")
        policy.source_snapshot(self.control, self.context["GITHUB_WORKFLOW_SHA"], fresh=True)
        return selected


def prepare(runtime, request, *, token):
    selected_policy()
    policy.source_snapshot(runtime.control, runtime.context["GITHUB_WORKFLOW_SHA"], fresh=True)
    selected = select(request, runtime.context, token=token)
    policy.require(not runtime.root.exists() and not runtime.root.is_symlink(), "Workflow runtime reuse refused")
    runtime.root.mkdir(mode=0o700)
    write_private(runtime.root / "owner.json", encoded(runtime.owner()))
    for name in ("artifact", "handoff", "diagnostics"):
        (runtime.root / name).mkdir(mode=0o700)
    data = encoded(selected)
    write_private(runtime.root / "selection.json", data)
    return {"root": str(runtime.root), "selection_sha256": policy.digest(data),
            "producer_sha": selected["producer"]["source_sha"], "producer_run_id": selected["producer"]["run_id"],
            "producer_artifact_id": selected["producer"]["artifact_id"]}


def downloaded(runtime, directory, name, limit):
    folder = runtime.root / directory
    policy.require(not folder.is_symlink() and {p.name for p in folder.iterdir()} == {name}, "Downloaded membership refused")
    path = policy.regular(folder / name)
    policy.require(path.stat().st_uid == os.getuid() and 0 < path.stat().st_size <= limit, "Downloaded file refused")
    target = runtime.root / name
    policy.require(not target.exists() and not target.is_symlink(), "Downloaded input reuse refused")
    path.chmod(0o600)
    path.rename(target)
    return target


def inputs(runtime, selected, *, token):
    policy.source_snapshot(runtime.producer, selected["producer"]["source_sha"], fresh=True)
    # checkout@<SHA> is detached. Materialize only the already fetched campaign
    # ref, not an input-supplied/guessed local proof. Official policy is read later.
    wanted = next(item["tip"] for item in selected["protection"]["branches"] if item["name"] == policy.BRANCH)
    observed = policy.git(runtime.producer, "show-ref", "--verify", "--hash", "refs/remotes/origin/" + policy.BRANCH).decode("ascii").strip()
    policy.require(observed == wanted, "Fetched campaign tip changed")
    local = policy.git(runtime.producer, "for-each-ref", "--format=%(objectname)", policy.REF).decode("ascii").strip()
    policy.require(local in ("", observed), "Existing local campaign ref changed")
    if not local:
        policy.git(runtime.producer, "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                   "update-ref", policy.REF, observed, "0" * 40)
    archive = downloaded(runtime, "artifact", policy.ARCHIVE, policy.MAX_BYTES)
    asset = private_file(runtime.root / policy.GH_DARWIN_ASSET)
    policy.require(asset.stat().st_size <= 8 * policy.MAX_META, "Verifier asset bound exceeded")
    bundle = receipts.acquire_attestation_bundle(selected["producer"], token=token)
    proof = runtime.root / "attestation.json"
    write_private(proof, bundle)
    return archive, asset, proof


def preflight(runtime, selection_sha256, *, token):
    policy.require(runtime.phase == "preflight", "Wrong workflow phase")
    selected = runtime.selection(selection_sha256, token=token)
    archive, asset, proof = inputs(runtime, selected, token=token)
    result = intake.publisher_preflight(runtime.producer, selected, archive, asset, proof, token=token)
    data = encoded({"selection": selected, "preflight": result})
    write_private(runtime.root / "comparison.json", data)
    policy.source_snapshot(runtime.control, runtime.context["GITHUB_WORKFLOW_SHA"], fresh=True)
    return {"record_sha256": policy.digest(data), "environment": selected["protection"]["environment"]["name"],
            "environment_id": selected["protection"]["environment"]["id"]}


def stage_raw(runtime, selected, archive):
    """Only admitted raw regular members; never extractall, permissions, links or caches."""
    metadata = intake._candidate_bytes(runtime.producer, selected, archive, fresh=True)
    expected = {name: (len(data), policy.digest(data)) for name, data in metadata.items()}
    for line in policy.text_lines(metadata["inventory.tsv"]):
        row = line.split("\t")
        if not row[5].startswith("checksum:"):
            expected["payloads/" + row[6]] = (int(row[7]), row[8])
    target = runtime.producer / "build/publication-bytes/input"
    policy.require(not (runtime.producer / "build").exists() and not (runtime.producer / "build").is_symlink(),
                   "Fresh staging root required")
    write_private(runtime.root / "staging.json", encoded({"target": str(target), "source_sha": selected["producer"]["source_sha"]}))
    target.mkdir(parents=True, mode=0o700)
    with policy.regular(archive).open("rb") as stream:
        policy.require(policy.file_facts(stream, archive.stat().st_size)["sha256"] == selected["producer"]["archive_sha256"],
                       "Archive changed before staging")
        stream.seek(0)
        seen = set()
        # Parse fixed headers, not TarFile's implicit GNU/PAX extension reader. The
        # final existing archive intake still verifies the complete raw end/padding.
        for _ in expected:
            header = stream.read(512)
            policy.require(len(header) == 512 and header[257:265] == b"ustar\x0000", "Raw header changed")
            member = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
            policy.require(member.name in expected and member.name not in seen and member.type == tarfile.REGTYPE
                           and not member.linkname and member.size == expected[member.name][0], "Raw archive member changed")
            seen.add(member.name)
            path = target / policy.relative_path(member.name)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            policy.require(not any(p.is_symlink() for p in (path, *path.parents)), "Staging symlink refused")
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
                digest, count = hashlib.sha256(), 0
                while count < member.size:
                    chunk = stream.read(min(65536, member.size - count))
                    policy.require(bool(chunk), "Truncated raw member")
                    count += len(chunk)
                    digest.update(chunk)
                    output.write(chunk)
            policy.require((count, digest.hexdigest()) == expected[member.name], "Staged bytes changed")
            padding = (-member.size) % 512
            policy.require(stream.read(padding) == bytes(padding), "Raw padding changed")
        policy.require(seen == set(expected), "Missing raw member")
        stream.seek(0)
        policy.require(policy.file_facts(stream, archive.stat().st_size)["sha256"] == selected["producer"]["archive_sha256"],
                       "Archive changed during staging")
    policy.require(intake._candidate_bytes(runtime.producer, selected, archive, fresh=False) == metadata,
                   "Source/archive changed after staging")
    return metadata


def guarded_command(runtime, selected, metadata, record_sha256):
    producers = policy.text_lines(metadata["producers.txt"])
    policy.require(producers == sorted(set(producers)) and bool(producers) and policy.hex_value(record_sha256),
                   "Exact admitted producer command required")
    # Construction is not native execution or authority. No credential is ever an argument.
    return [str(runtime.producer / "gradlew"), "--offline", "--no-daemon", "--no-parallel", "--max-workers=1",
            "--no-build-cache", "--no-configuration-cache", "--console=plain", "-PkiraPublicationMode=promote-remote",
            "-PkiraPublicationSealSha256=" + selected["producer"]["seal_sha256"],
            "-PkiraPublisherRecordSha256=" + record_sha256, ":promoteKiraPublicationBytes"] + [part for task in producers for part in ("-x", task)]


def recheck(runtime, selection_sha256, record_sha256, *, token):
    policy.require(runtime.phase == "recheck", "Wrong workflow phase")
    selected = runtime.selection(selection_sha256, token=token)
    record = downloaded(runtime, "handoff", "comparison.json", policy.MAX_META)
    data = policy.read_small(private_file(record))
    policy.require(policy.hex_value(record_sha256) and policy.digest(data) == record_sha256, "Preflight handoff changed")
    before = policy.json_data(data)
    policy.require(type(before) is dict and set(before) == {"selection", "preflight"} and before["selection"] == selected,
                   "Cross-invocation or substituted preflight refused")
    archive, asset, proof = inputs(runtime, selected, token=token)
    intake.publisher_recheck(runtime.producer, selected, before["preflight"], archive, asset, proof, token=token)
    metadata = stage_raw(runtime, selected, archive)
    command = guarded_command(runtime, selected, metadata, record_sha256)
    policy.source_snapshot(runtime.control, runtime.context["GITHUB_WORKFLOW_SHA"], fresh=True)
    write_private(runtime.root / "diagnostics/held-command.json", encoded({"scope": "constructed-not-executed",
                                                                         "command": command, "write_credentials": "not-provisioned"}))
    # Real all-writer/unused-cohort and write-custody authority is absent. Even a future
    # separately reviewed caller must still satisfy the unchanged Gradle/policy holds.
    raise MissingAuthority("Native command held: no writer/unused-cohort/write-custody authority")


def cleanup(runtime):
    if not runtime.root.exists() and not runtime.root.is_symlink():
        return
    runtime.check()
    marker = runtime.root / "staging.json"
    if marker.exists() or marker.is_symlink():
        data = policy.json_data(policy.read_small(private_file(marker)))
        target = runtime.producer / "build/publication-bytes/input"
        policy.require(set(data) == {"target", "source_sha"} and data["target"] == str(target)
                       and policy.hex_value(data["source_sha"], 40), "Staging owner refused")
        policy.require(not any(p.is_symlink() for p in (target, *target.parents)), "Redirected staging cleanup refused")
        if target.exists():
            shutil.rmtree(target)
        for directory in (target.parent, target.parent.parent):
            if directory.exists():
                directory.rmdir()  # Refuse unrelated contents; never remove another output tree.
    shutil.rmtree(runtime.root)


def main(argv=None):
    try:
        arguments = sys.argv[1:] if argv is None else argv
        commands = {"prepare-preflight", "prepare-recheck", "preflight", "recheck", "cleanup-preflight", "cleanup-recheck"}
        policy.require(len(arguments) == 1 and arguments[0] in commands, "Workflow command refused")
        command = arguments[0]
        env = dict(os.environ)
        policy.require(not any(name in env for name in ("KIRA_MAVEN_WRITE_USERNAME", "KIRA_MAVEN_WRITE_TOKEN")), "Write custody forbidden")
        token = env.pop("KIRA_PUBLICATION_API_READ_TOKEN", None)
        context = {name: env.get(name, "") for name in CONTEXT_KEYS}
        policy.require(context["GITHUB_RUN_ID"].isascii() and context["GITHUB_RUN_ID"].isdigit()
                       and 0 < int(context["GITHUB_RUN_ID"]) <= 2**63 - 1 and context["GITHUB_RUN_ATTEMPT"] == "1",
                       "Exact first workflow attempt required")
        policy.require(env.get("GITHUB_ACTIONS") == "true" and env.get("RUNNER_OS") == "macOS"
                       and env.get("RUNNER_ARCH") == "ARM64" and platform.system() == "Darwin" and platform.machine() == "arm64",
                       "Actual hosted Darwin ARM64 required")
        workspace, temp = Path(env["GITHUB_WORKSPACE"]), Path(env["RUNNER_TEMP"])
        policy.require(workspace.is_absolute() and temp.is_absolute() and workspace.resolve(strict=True) == workspace
                       and temp.resolve(strict=True) == temp and not temp.is_relative_to(workspace), "Owned workflow paths refused")
        root = temp / ("kira-publisher-" + context["GITHUB_RUN_ID"] + "-1")
        runtime = Runtime(workspace / "control", workspace / "producer", root, context, command.split("-")[-1])
        os.umask(0o077)
        os.environ.clear()  # Read token is programmatic only; Git/native children never inherit it.
        os.environ.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", HOME=str(root), TMPDIR=str(root), LANG="C", LC_ALL="C")
        if command.startswith("cleanup-"):
            cleanup(runtime)
            print("Owned publisher inputs/staging absent; no Gradle/bootstrap worker was started.")
            return 0
        if command.startswith("prepare-"):
            result = prepare(runtime, policy.json_data(env.get("KIRA_PUBLISHER_REQUEST", "").encode("utf-8")), token=token)
        elif command == "preflight":
            result = preflight(runtime, env.get("KIRA_SELECTION_SHA256", ""), token=token)
        else:
            recheck(runtime, env.get("KIRA_SELECTION_SHA256", ""), env.get("KIRA_RECORD_SHA256", ""), token=token)
            policy.require(False, "Recheck cannot grant publication authority")
        with Path(env["GITHUB_OUTPUT"]).open("a") as output:
            for key, value in result.items():
                policy.require(not any(c in str(value) for c in "\r\n\x00"), "Unsafe workflow output")
                output.write(f"{key}={value}\n")
        print("Held publisher comparison completed; no release authority or package writes.")
        return 0
    except MissingAuthority:
        print("KIRA_PUBLISHER_HELD: selected policy or writer authority unavailable. No package writes.", file=sys.stderr)
        return 3
    except Exception:
        print("Publisher workflow refused. No package writes permitted.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
