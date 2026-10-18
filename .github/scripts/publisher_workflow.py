#!/usr/bin/env python3
"""Exact protected publisher composition; native guards own every original writer/readback."""

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import time

import github_publication_receipts as receipts
import publication_policy as policy
import publisher_publication_intake as intake


# Owner-selected settings, independently read back on 2026-10-18. Dispatch/API
# discovery cannot change these IDs or waive fresh policy/actual approval checks.
TRUSTED_POLICY = {
    "publisher_ref": policy.REF,
    "environment": {"id": 24588601457, "name": "source-publication", "branch_policy_id": 63403649,
                    "reviewer_ids": [104788132]},
    "branches": [{"name": policy.BRANCH, "approvals": 0, "checks": [{"context": "verify", "app_id": 15368}],
                  "bypass_rule_id": "BPR_kwDOTiA_Vs4FEN_x"}],
}
CONTEXT_KEYS = ("GITHUB_REPOSITORY", "GITHUB_REPOSITORY_ID", "GITHUB_EVENT_NAME", "GITHUB_REF",
                "GITHUB_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_WORKFLOW_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")
NATIVE_CREDENTIAL_KEYS = {"KIRA_PUBLICATION_API_READ_TOKEN", "KIRA_MAVEN_READ_USERNAME", "KIRA_MAVEN_READ_TOKEN",
                          "KIRA_MAVEN_WRITE_USERNAME", "KIRA_MAVEN_WRITE_TOKEN"}


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
                                                            "app_parity_ref", "backend_parity_ref", "release_window"}, "Dispatch fields refused")
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
                              "app_parity_ref": request["app_parity_ref"], "backend_parity_ref": request["backend_parity_ref"],
                              "release_window": request["release_window"]}, token)


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


def prepare(runtime, request, *, token, output=None):
    selected_policy()
    policy.source_snapshot(runtime.control, runtime.context["GITHUB_WORKFLOW_SHA"], fresh=True)
    selected = select(request, runtime.context, token=token)
    reserved = [runtime.root, runtime.producer]
    if runtime.phase == "recheck":
        reserved += [runtime.producer.with_name("tooling"), runtime.root.parent / "kira-libffi-source",
                     runtime.root.parent / "kira-libffi-provenance.json"]
    policy.require(all(not path.exists() and not path.is_symlink() for path in reserved), "Workflow runtime reuse refused")
    runtime.root.mkdir(mode=0o700)
    write_private(runtime.root / "owner.json", encoded(runtime.owner()))
    if output is not None:
        with output.open("a") as stream:
            stream.write(f"root={runtime.root}\n")  # Expose custody before any later setup failure.
    for name in ("artifact", "handoff", "diagnostics", "home", "tmp", "gradle"):
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


def preflight(runtime, selection_sha256, *, token, package_token=None, audit_token=None):
    policy.require(runtime.phase == "preflight", "Wrong workflow phase")
    audit_credentials(audit_token, api_token=token, package_token=package_token)
    selected = runtime.selection(selection_sha256, token=token)
    archive, asset, proof = inputs(runtime, selected, token=token)
    result = intake.publisher_preflight(runtime.producer, selected, archive, asset, proof, token=token)
    window = intake.observe_release_window(selected, api_token=token, package_token=package_token,
                                            deleted_observer=lambda: audit_deleted(selected, audit_token))
    data = encoded({"selection": selected, "preflight": result, "window": window})
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


def owned_helpers():
    # Reuse reviewed resource primitives, never that caller's old tuple/CLI/uploader.
    # Lazy import avoids its existing publisher_workflow utility import cycle.
    import owned_file_promotion
    return owned_file_promotion


def runtime_environment(runtime, env):
    # Only fixed owned homes and ordinary tool selectors; never ambient options or credentials.
    ordinary = {key: env[key] for key in ("JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT", "DEVELOPER_DIR") if key in env}
    result = owned_helpers().child_environment(runtime, ordinary)
    result.update(runtime.context)
    result.update({key: env[key] for key in ("GITHUB_ACTIONS", "RUNNER_OS", "RUNNER_ARCH", "GITHUB_ENV") if key in env})
    result["RUNNER_TEMP"] = str(runtime.root.parent)
    return result


def native_credentials(*, api_token, package_token, write_username, write_token):
    receipts._validate_read_token(api_token)
    receipts._validate_read_token(package_token)
    policy.require(api_token is not None and package_token is not None
                   and write_token not in (api_token, package_token), "Separate explicit read/write custody required")
    intake.remote.read_credentials(write_username, write_token)
    account = receipts._get_json("/user", package_token)
    username = account.get("login")
    policy.require(policy.positive(account.get("id")), "Actual package-read account required")
    intake.remote.read_credentials(username, package_token)
    return {"KIRA_PUBLICATION_API_READ_TOKEN": api_token, "KIRA_MAVEN_READ_USERNAME": username,
            "KIRA_MAVEN_READ_TOKEN": package_token, "KIRA_MAVEN_WRITE_USERNAME": write_username,
            "KIRA_MAVEN_WRITE_TOKEN": write_token}


def audit_credentials(audit_token, *, api_token, package_token, write_token=None):
    receipts._validate_read_token(audit_token)
    policy.require(audit_token is not None and api_token is not None and package_token is not None
                   and audit_token not in (api_token, package_token, write_token), "Separate explicit metadata-audit custody required")


def audit_deleted(selected, audit_token):
    window = intake.validate_release_window(selected)
    result = intake.remote.observe_deleted_versions(window["packages"], selected["publisher"]["repository_id"], token=audit_token)
    intake._window_open(window)
    return result


class MetadataAudit:
    """One owned socket serviced by the existing controller selector; no worker/daemon."""
    def __init__(self, runtime, selected, token, deadline):
        self.runtime, self.selected, self.token, self.deadline = runtime, selected, token, deadline
        self.path = intake.metadata_audit_socket(runtime.root / policy.ARCHIVE, selected)
        self.binding = intake.metadata_audit_binding(selected)
        self.listener, self.identity, self.calls = None, None, 0

    def __enter__(self):
        self.runtime.check()
        policy.require(not self.path.exists() and not self.path.is_symlink(), "Metadata-audit socket reuse refused")
        try:
            self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.listener.bind(str(self.path))
            self.identity = self.path.lstat()
            self.path.chmod(0o600)
            self.listener.listen(1)
            self.listener.settimeout(5)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def serve_once(self):
        policy.require(self.calls < 16 and time.monotonic() < self.deadline, "Metadata-audit call/time bound exceeded")
        self.runtime.check()
        self.calls += 1
        with self.listener.accept()[0] as connection:
            connection.settimeout(5)
            request = intake.audit_receive(connection, intake.AUDIT_REQUEST_LIMIT, 5)
            policy.require(type(request) is dict and set(request) == set(self.binding) | {"nonce"}
                           and all(request[key] == value for key, value in self.binding.items())
                           and type(request["nonce"]) is str and re.fullmatch(r"[0-9a-f]{32}", request["nonce"]) is not None,
                           "Metadata-audit request binding refused")
            observation = audit_deleted(self.selected, self.token)
            policy.require(time.monotonic() < self.deadline, "Metadata-audit time bound exceeded")
            data = encoded(request | {"observation": observation})
            policy.require(len(data) <= intake.AUDIT_RESPONSE_LIMIT, "Metadata-audit response bound exceeded")
            connection.sendall(data)

    def __exit__(self, *_):
        try:
            if self.listener is not None:
                self.listener.close()
            if self.identity is not None:
                current = self.path.lstat()
                policy.require(stat.S_ISSOCK(current.st_mode) and current.st_uid == os.getuid()
                               and (current.st_dev, current.st_ino) == (self.identity.st_dev, self.identity.st_ino),
                               "Metadata-audit cleanup owner changed")
                self.path.unlink()
        finally:
            self.token = None  # No claim of secure erasure of immutable Python strings.


def redacted_line(data, credentials, audit_token):
    secrets = [credentials[key].encode("ascii") for key in
               ("KIRA_PUBLICATION_API_READ_TOKEN", "KIRA_MAVEN_READ_TOKEN", "KIRA_MAVEN_WRITE_TOKEN")]
    secrets.append(audit_token.encode("ascii"))
    for role in ("READ", "WRITE"):
        pair = credentials["KIRA_MAVEN_" + role + "_USERNAME"] + ":" + credentials["KIRA_MAVEN_" + role + "_TOKEN"]
        secrets.append(base64.b64encode(pair.encode("ascii")))
    for secret in sorted(secrets, key=len, reverse=True):
        data = data.replace(secret, b"[redacted]")
    return re.sub(rb"https://[^\s]*\?[^\s]*", b"[redacted-query-url]", data)


def native_output(process, log, credentials, deadline, audit):
    pending, total = b"", 0
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        selector.register(audit.listener, selectors.EVENT_READ)
        reading = True
        while reading:
            policy.require(time.monotonic() < deadline, "Native publication time bound exceeded")
            for key, _ in selector.select(timeout=0.5):
                if key.fileobj is audit.listener:
                    audit.serve_once()
                    continue
                chunk = os.read(key.fd, 65536)
                total += len(chunk)
                policy.require(total <= 64 * 1024 * 1024, "Native output bound exceeded")
                pending += chunk
                lines = pending.split(b"\n")
                pending = lines.pop()
                if not chunk:
                    selector.unregister(process.stdout)
                    reading = False
                    lines.append(pending)
                    pending = b""
                policy.require(len(pending) <= 1024 * 1024, "Native output line bound exceeded")
                for line in lines:
                    policy.require(len(line) <= 1024 * 1024, "Native output line bound exceeded")
                    safe = redacted_line(line, credentials, audit.token) + b"\n"
                    policy.require(log.tell() + len(safe) <= 64 * 1024 * 1024, "Native diagnostic bound exceeded")
                    log.write(safe)
    while process.poll() is None:
        policy.require(time.monotonic() < deadline, "Native publication time bound exceeded")
        time.sleep(0.5)
    return total


def run_native(runtime, env, command, credentials, *, selected, audit_token):
    policy.require(set(credentials) == NATIVE_CREDENTIAL_KEYS and "KIRA_PACKAGES_METADATA_AUDIT_TOKEN" not in env,
                   "Native credential projection refused")
    audit_credentials(audit_token, api_token=credentials["KIRA_PUBLICATION_API_READ_TOKEN"],
                      package_token=credentials["KIRA_MAVEN_READ_TOKEN"], write_token=credentials["KIRA_MAVEN_WRITE_TOKEN"])
    write_private(runtime.root / "diagnostics/native-command.json", encoded(command))
    process, started = None, time.monotonic()
    try:
        path = runtime.root / "diagnostics/native.log"
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as log, \
                MetadataAudit(runtime, selected, audit_token, started + 1200) as audit:
            child = owned_helpers().child_environment(runtime, env) | runtime.context
            child.update({key: env[key] for key in ("GITHUB_ACTIONS", "RUNNER_OS", "RUNNER_ARCH", "RUNNER_TEMP")})
            process = subprocess.Popen(command, cwd=runtime.producer, env=child | credentials, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
            count = native_output(process, log, credentials, started + 1200, audit)
            policy.require(process.returncode == 0, "Native publication failed; partial remote state may exist")
            policy.require(audit.calls == 16, "Complete native metadata-audit sequence required")
        return {"exit_code": 0, "seconds": round(time.monotonic() - started, 3), "output_bytes": count}
    finally:
        try:
            if process is not None:
                # This new session is ours, including descendants after the wrapper exits.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)
                    process.stdout.close()
        finally:
            owned_helpers().stop_owned(runtime, env, "native")


def tooling(runtime, selected, env):
    owned = owned_helpers()
    owned.toolchain(runtime, selected["producer"], env)
    write_private(runtime.root / "bootstrap-started.json", encoded({"source_sha": selected["producer"]["source_sha"]}))
    helper = owned.bootstrap(runtime)
    helper.prepare(env, runtime.producer)
    # prepare also emits these fixed values to GITHUB_ENV for later cleanup steps.
    # Apply the exact authenticated profile in this process; never evaluate exports.
    root = helper.owned_root(env)
    env = env | {"GRADLE_OPTS": helper.NATIVE_HOME + str(root / "compiler/kotlin-native-prebuilt-macos-aarch64-2.2.21")
                 + " " + helper.CACHE_OFF, "KONAN_DATA_DIR": str(root / "data")}
    owned.warmup(runtime, selected["producer"], env)
    owned.profile(runtime, env)
    return env


def capture_readback(runtime, selected, record_sha256):
    path = runtime.producer / "build/publication-bytes/remote-readback.json"
    data = policy.read_small(private_file(path))
    result = policy.json_data(data)
    publications = sorted(":" + module + ":" + publication
                          for module in policy.MODULES for publication in policy.PUBLICATIONS)
    producer = {key: selected["producer"][key] for key in ("run_id", "source_sha", "seal_sha256", "inventory_sha256")}
    policy.require(type(result) is dict and set(result) == {"scope", "record_sha256", "producer", "publisher_run_id",
                                                           "publications", "readback", "window"}
                   and result["scope"] == "native-remote-maven-readback" and result["record_sha256"] == record_sha256
                   and result["producer"] == producer and type(result["publisher_run_id"]) is int
                   and result["publisher_run_id"] == selected["publisher"]["run_id"]
                   and result["publications"] == publications, "Native result identity differs")
    readback, window = result["readback"], result["window"]
    policy.require(type(readback) is dict and set(readback) == {"completed", "inventory_sha256", "indexes"}
                   and readback["completed"] == publications and readback["inventory_sha256"] == producer["inventory_sha256"]
                   and type(readback["indexes"]) is dict and set(readback["indexes"]) == set(publications)
                   and type(window) is dict and set(window) == {"control", "registry"}, "Incomplete native result refused")
    # The sealed native finalizer verified every immutable byte/index and fresh
    # window. Bind/copy its actual result; do not invent a second acceptance receipt.
    write_private(runtime.root / "diagnostics/remote-readback.json", data)


def recheck(runtime, selection_sha256, record_sha256, *, token, package_token=None, audit_token=None, env=None,
            write_username=None, write_token=None):
    policy.require(runtime.phase == "recheck", "Wrong workflow phase")
    audit_credentials(audit_token, api_token=token, package_token=package_token, write_token=write_token)
    selected = runtime.selection(selection_sha256, token=token)
    record = downloaded(runtime, "handoff", "comparison.json", policy.MAX_META)
    data = policy.read_small(private_file(record))
    policy.require(policy.hex_value(record_sha256) and policy.digest(data) == record_sha256, "Preflight handoff changed")
    before = policy.json_data(data)
    policy.require(type(before) is dict and set(before) == {"selection", "preflight", "window"} and before["selection"] == selected,
                   "Cross-invocation or substituted preflight refused")
    archive, asset, proof = inputs(runtime, selected, token=token)
    intake.publisher_recheck(runtime.producer, selected, before["preflight"], archive, asset, proof, token=token)
    deleted_observer = lambda: audit_deleted(selected, audit_token)
    intake.observe_release_window(selected, api_token=token, package_token=package_token,
                                  deleted_observer=deleted_observer, before=before["window"])
    credentials = native_credentials(api_token=token, package_token=package_token,
                                     write_username=write_username, write_token=write_token)
    diagnostics = runtime.root / "diagnostics"
    write_private(diagnostics / "comparison.json", data)
    write_private(diagnostics / "selection.json", encoded(selected))
    write_private(diagnostics / "intake.json", encoded(selected["producer"]))
    source_before = policy.source_snapshot(runtime.producer, selected["producer"]["source_sha"], fresh=True)
    write_private(diagnostics / "source-before.json", encoded(source_before))
    env = tooling(runtime, selected, runtime_environment(runtime, env or {}))
    intake.observe_release_window(selected, api_token=token, package_token=package_token,
                                  deleted_observer=deleted_observer, before=before["window"])
    metadata = stage_raw(runtime, selected, archive)
    for name, value in metadata.items():
        write_private(diagnostics / name, value)
    command = guarded_command(runtime, selected, metadata, record_sha256)
    policy.source_snapshot(runtime.control, runtime.context["GITHUB_WORKFLOW_SHA"], fresh=True)
    native = run_native(runtime, env, command, credentials, selected=selected, audit_token=audit_token)
    owned_helpers().profile(runtime, env)
    source_after = policy.source_snapshot(runtime.producer, selected["producer"]["source_sha"], fresh=False)
    policy.require(source_after == source_before
                   and intake._candidate_bytes(runtime.producer, selected, archive, fresh=False) == metadata,
                   "Source/archive changed during native publication")
    policy.source_snapshot(runtime.control, runtime.context["GITHUB_WORKFLOW_SHA"], fresh=True)
    capture_readback(runtime, selected, record_sha256)
    write_private(diagnostics / "source-after.json", encoded(source_after))
    write_private(diagnostics / "native-result.json", encoded(native))
    return {"result": "complete-native-remote-readback"}


def cleanup(runtime):
    """No-worker input cleanup. Live native jobs use finalize then receipt-gated dispose."""
    if not runtime.root.exists() and not runtime.root.is_symlink():
        return
    runtime.check()
    marker = runtime.root / "bootstrap-started.json"
    policy.require(not marker.exists() and not marker.is_symlink(), "Native cleanup requires worker-stop barrier")
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


def refusal_sites(error):
    """Bounded trusted code locations only, including deliberately suppressed causes.

    Never render exceptions, frames/locals, source lines, external filenames or HTTP
    bodies. This diagnoses a refusal; it neither changes nor retries any release gate.
    """
    directory = os.path.dirname(os.path.abspath(__file__))
    allowed = {os.path.join(directory, name): name for name in (
        "publisher_workflow.py", "publisher_publication_intake.py", "connected_publication_intake.py",
        "github_publication_receipts.py", "publication_policy.py", "remote_maven_readback.py")}
    sites, seen = [], set()
    for _ in range(8):
        if error is None or id(error) in seen:
            break
        seen.add(id(error))
        trace = error.__traceback__
        for _ in range(64):
            if trace is None:
                break
            name = allowed.get(os.path.abspath(trace.tb_frame.f_code.co_filename))
            if name is not None:
                sites.append(name + ":" + str(trace.tb_lineno))
            trace = trace.tb_next
        error = error.__cause__ if error.__cause__ is not None else error.__context__
    return " > ".join(sites[-32:]) or "unavailable"


def main(argv=None):
    previous_signal = None
    try:
        arguments = sys.argv[1:] if argv is None else argv
        commands = {"prepare-preflight", "prepare-recheck", "preflight", "recheck", "finalize-recheck",
                    "cleanup-preflight", "cleanup-recheck"}
        policy.require(len(arguments) == 1 and arguments[0] in commands, "Workflow command refused")
        command = arguments[0]
        env = dict(os.environ)
        write_username, write_token = env.pop("KIRA_MAVEN_WRITE_USERNAME", None), env.pop("KIRA_MAVEN_WRITE_TOKEN", None)
        policy.require(command == "recheck" or (write_username is None and write_token is None), "Write custody forbidden in this phase")
        token = env.pop("KIRA_PUBLICATION_API_READ_TOKEN", None)
        package_token = env.pop("KIRA_PACKAGES_READ_TOKEN", None)
        audit_token = env.pop("KIRA_PACKAGES_METADATA_AUDIT_TOKEN", None)
        policy.require(command in ("preflight", "recheck") or audit_token is None, "Audit custody forbidden in this phase")
        context = {name: env.get(name, "") for name in CONTEXT_KEYS}
        policy.require(context["GITHUB_RUN_ID"].isascii() and context["GITHUB_RUN_ID"].isdigit()
                       and 0 < int(context["GITHUB_RUN_ID"]) <= 2**63 - 1 and context["GITHUB_RUN_ATTEMPT"] == "1",
                       "Exact first workflow attempt required")
        policy.require(env.get("GITHUB_ACTIONS") == "true" and env.get("RUNNER_OS") == "macOS"
                       and env.get("RUNNER_ARCH") == "ARM64" and platform.system() == "Darwin" and platform.machine() == "arm64",
                       "Actual hosted Darwin ARM64 required")
        workspace, temp = Path(env["GITHUB_WORKSPACE"]), Path(env["RUNNER_TEMP"])
        policy.require(workspace.is_absolute() and temp.is_absolute() and workspace.resolve(strict=True) == workspace
                       and temp.resolve(strict=True) == temp and not temp.is_relative_to(workspace)
                       and not workspace.is_relative_to(temp)
                       and all(re.fullmatch(r"/[A-Za-z0-9._/-]+", str(path)) for path in (workspace, temp)),
                       "Owned workflow paths refused")
        root = temp / ("kira-publisher-" + context["GITHUB_RUN_ID"] + "-1")
        runtime = Runtime(workspace / "control", workspace / "producer", root, context, command.split("-")[-1])
        os.umask(0o077)
        os.environ.clear()  # No Git, bootstrap, tooling or stop child inherits any credential.
        os.environ.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", HOME=str(root / "home"), TMPDIR=str(root / "tmp"), LANG="C", LC_ALL="C")
        previous_signal = signal.signal(signal.SIGTERM, lambda *_: policy.require(False, "Interrupted; owned cleanup required"))
        if command == "cleanup-preflight":
            cleanup(runtime)
            print("Owned preflight inputs absent.")
            return 0
        if command in ("finalize-recheck", "cleanup-recheck"):
            if root.exists() or root.is_symlink():
                if command == "finalize-recheck":
                    owned_helpers().finalize(runtime, runtime_environment(runtime, env))
                else:
                    owned_helpers().dispose(runtime)  # Requires the genuine owned stop/bootstrap cleanup receipt.
            print("Owned publisher resource phase completed; not a publication result.")
            return 0
        policy.check_environment(env)
        policy.require(token is not None, "Explicit API-read channel required")
        if command.startswith("prepare-"):
            result = prepare(runtime, policy.json_data(env.get("KIRA_PUBLISHER_REQUEST", "").encode("utf-8")), token=token,
                             output=Path(env["GITHUB_OUTPUT"]))
        elif command == "preflight":
            result = preflight(runtime, env.get("KIRA_SELECTION_SHA256", ""), token=token,
                               package_token=package_token, audit_token=audit_token)
        else:
            result = recheck(runtime, env.get("KIRA_SELECTION_SHA256", ""), env.get("KIRA_RECORD_SHA256", ""),
                             token=token, package_token=package_token, audit_token=audit_token, env=env,
                             write_username=write_username, write_token=write_token)
        with Path(env["GITHUB_OUTPUT"]).open("a") as output:
            for key, value in result.items():
                policy.require(not any(c in str(value) for c in "\r\n\x00"), "Unsafe workflow output")
                if key != "root":  # Prepared ownership was already exposed before later setup could fail.
                    output.write(f"{key}={value}\n")
        print("Publisher phase completed; native success and resource cleanup are separate evidence.")
        return 0
    except MissingAuthority:
        print("KIRA_PUBLISHER_HELD: selected policy or writer authority unavailable.", file=sys.stderr)
        return 3
    except Exception as error:
        print("Publisher workflow refused. If native execution began, remote state may be partial; do not retry.", file=sys.stderr)
        print("Publisher refusal code sites: " + refusal_sites(error), file=sys.stderr)
        return 1
    finally:
        if previous_signal is not None:
            signal.signal(signal.SIGTERM, previous_signal)


if __name__ == "__main__":
    sys.exit(main())
