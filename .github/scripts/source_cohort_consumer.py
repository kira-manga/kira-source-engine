#!/usr/bin/env python3
"""Focused consumers of one genuinely promoted owned cohort; no publication authority."""

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import types
import xml.etree.ElementTree as ET

import github_publication_receipts as receipts
import owned_file_promotion as owned
import publication_policy as policy
import publisher_workflow as common


WORKFLOW = ".github/workflows/source-cohort-consumer-check.yml"
SELECTION_SHA256 = "da4ccce8447b4d067ecdd45a6b73992e4e685f9fe87d7fcca95b308797db4c05"
APP_SHA = "88ec235bcc2530fe458a25221dcf16eee1e4535d"
BACKEND_SHA = "d1e69bee705265a9f08b38a0c221510159f8a4f9"
APP_INIT_SHA = "f7be0ba204524396bfd962d1a099157b7e14e5bc63a9f031a17896c922796b3c"
FINITE_FEEDS = "finite feeds survive canonical authoring and reject unsupported verbs"
ENGINE_PREFIX = "me.manga.kira.sources.engine."
RUNTIME_PREFIX = "me.manga.kira.sources.runtime."
BACKEND_PREFIX = "me.manga.kira.backend.sourceconfig."
ENGINE_TESTS = ["DefaultSourceConfigValidatorTest", "DefaultSourceConfigValidatorFilterTest",
                "DefaultSourceConfigValidatorHeaderFilterTest", "GenericSourceClientHtmlGoldenTest",
                "GenericSourceClientJsonGoldenTest", "GenericSourceClientScriptJsonTest"]
RUNTIME_TESTS = ["KtorHttpExecutorTest", "KtorHttpExecutorStreamingTest", "KtorRemoteSourceCatalogTest",
                 "KtorRemoteSourceCatalogStreamingTest", "KtorRemoteSourceCatalogCleanupTest",
                 "BundledSourceCatalogPolicyTest", "ConfigBackedSourceCompletenessTest"]
PILOTS = ["Asq", "Azora", "DilarV2", "EnglishSources", "MadaraHtml", "Mangamello", "SwatManga", "Tapas"]
LIFECYCLE = "me.manga.kira.di.SourceCatalogHttpClientLifecycleTest"
ANDROID_TRANSPORT = RUNTIME_PREFIX + "AndroidGenericSingleHopTest"
IOS_TRANSPORT = RUNTIME_PREFIX + "IosGenericSingleHopTest"
TRANSPORT_METHODS = {"getAndPostReturnOneHopWithoutFollowingTheCredentialedLocation",
                     "exposedAmbiguityFailsClosedAndRawWireCollapseIsReported",
                     "callerCancellationReleasesTheSocketAndOwnedCloseKeepsAnotherClientUsable"}
BACKEND_TESTS = ["parsing.ManhwatopPublishedEngineTest", "parsing.AuthoredSourceConfigTest", "parsing.SourceConfigParserTest",
                 "validation.SourceConfigValidatorTest", "admin.FullBundledParityIT", "admin.SourcePreviewIT"]
RECONCILIATION_TESTS = {BACKEND_PREFIX + name: count for name, count in (
    ("admin.SourceCatalogReconciliationIT", 7), ("api.GenericV2CutoverControllerTest", 10),
    ("FlywayMigrationIT", 3), ("FlywayIncrementalOrderIT", 14),
    ("admin.BootstrapPublicationGuardIT", 5), ("admin.BootstrapReplayIT", 2),
    ("admin.BootstrapEndpointIT", 6), ("admin.BootstrapLateRollbackIT", 2),
)}
READER_TEST = BACKEND_PREFIX + "validation.SourceConfigValidatorReaderCapabilityTest"
FLAGS = ["--no-daemon", "--no-parallel", "--max-workers=1", "--no-build-cache", "--no-configuration-cache",
         "--dependency-verification=strict", "--console=plain", "--stacktrace", "-Pkotlin.compiler.execution.strategy=in-process",
         "-Pandroid.builder.sdkDownload=false", "-Porg.gradle.java.installations.auto-download=false"]


def selection(value):
    # Canonical digest of the primary's original SELECTED-PROMOTION.json, not a
    # second receipt format. Dispatch cannot replace this independently selected run.
    policy.require(type(value) is dict and policy.digest(common.encoded(value)) == SELECTION_SHA256,
                   "Only the independently selected successful promotion is admitted")
    return policy.json_data(common.encoded(value))


def facts(path):
    path = policy.regular(path)
    with path.open("rb") as stream:
        return policy.file_facts(stream, path.stat().st_size)


def verify_promotion(selected, token):
    receipts._validate_read_token(token)
    prefix = "/repos/" + policy.REPOSITORY + "/actions"
    run = prefix + "/runs/" + str(selected["run_id"])
    result = {"attempt": receipts._get_json(run + "/attempts/1", token),
              "jobs": receipts._get_json(run + "/attempts/1/jobs?per_page=100&page=1", token),
              "artifact": receipts._get_json(prefix + "/artifacts/" + str(selected["artifact_id"]), token),
              "current": receipts._get_json(run, token)}
    expected = {"id": selected["run_id"], "run_attempt": 1, "head_sha": selected["source_sha"],
                "head_branch": selected["ref"].removeprefix("refs/heads/"), "path": selected["workflow_path"],
                "workflow_id": selected["workflow_id"], "event": "push", "status": "completed", "conclusion": "success"}
    for observed in (result["attempt"], result["current"]):
        policy.require(all(observed.get(k) == v and type(observed.get(k)) is type(v) for k, v in expected.items()) and
                       all(observed.get(k, {}).get("full_name") == policy.REPOSITORY for k in ("repository", "head_repository")),
                       "Promotion is not the selected completed successful first attempt")
    jobs = result["jobs"].get("jobs", [])
    policy.require(type(result["jobs"].get("total_count")) is int and result["jobs"]["total_count"] == len(jobs) == 1,
                   "Promotion jobs incomplete")
    expected_job = {"id": selected["job_id"], "name": "owned-file", "run_id": selected["run_id"], "run_attempt": 1,
                    "head_sha": selected["source_sha"], "status": "completed", "conclusion": "success"}
    policy.require(all(jobs[0].get(k) == v and type(jobs[0].get(k)) is type(v) for k, v in expected_job.items()), "Promotion job changed")
    artifact = result["artifact"]
    expected_artifact = {"id": selected["artifact_id"], "name": selected["artifact_name"], "expired": False,
                         "size_in_bytes": selected["artifact_size"], "digest": "sha256:" + selected["artifact_sha256"]}
    policy.require(all(artifact.get(k) == v and type(artifact.get(k)) is type(v) for k, v in expected_artifact.items()) and
                   policy.timestamp(artifact.get("expires_at")) > datetime.now(timezone.utc), "Promotion artifact changed/expired")
    link = artifact.get("workflow_run", {})
    policy.require(all(link.get(k) == v and type(link.get(k)) is type(v) for k, v in {"id": selected["run_id"], "head_sha": selected["source_sha"],
                   "repository_id": selected["repository_id"], "head_repository_id": selected["repository_id"],
                   "head_branch": expected["head_branch"]}.items()), "Promotion artifact belongs to another run")
    return result


def owned_tar(archive, manifest, selected, target=None):
    """Same plain-USTAR bounds as publication_policy/stage_raw, no implicit extensions."""
    policy.require(type(manifest) is dict and 0 < len(manifest) == selected["files"] <= policy.MAX_ENTRIES, "Owned tree cardinality")
    for name, item in manifest.items():
        policy.relative_path(name)
        policy.require(name.startswith(policy.GROUP.replace(".", "/") + "/") and type(item) is dict and
                       set(item) == {"size", "sha256"} and type(item["size"]) is int and 0 < item["size"] <= 64 * 1024 * 1024
                       and policy.hex_value(item["sha256"]), "Owned tree path/facts refused")
    policy.require(facts(archive)["sha256"] == selected["sha256"] and archive.stat().st_size == selected["size"] and
                   1024 <= selected["size"] <= policy.MAX_BYTES and selected["size"] % 512 == 0, "Owned archive digest/size changed")
    if target is not None:
        policy.require(not target.exists() and not target.is_symlink(), "Owned destination must be fresh")
        policy.require(not any(p.is_symlink() for p in target.parents), "Redirected destination")
        target.mkdir(mode=0o700)
    actual = {}
    with archive.open("rb") as stream:
        for name in sorted(manifest):
            header = stream.read(512)
            policy.require(len(header) == 512 and header[257:265] == b"ustar\x0000", "Only plain USTAR is admitted")
            member = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
            policy.require(member.name == name and member.type == tarfile.REGTYPE and not member.linkname and
                           not member.pax_headers and member.size == manifest[name]["size"], "Owned archive membership/type/size changed")
            actual[name] = copy_member(stream, member.size, None if target is None else target / name)
            policy.require({k: actual[name][k] for k in ("size", "sha256")} == manifest[name], "Owned member hash changed")
            padding = (-member.size) % 512
            policy.require(stream.read(padding) == bytes(padding), "Owned tar padding changed")
        policy.require(stream.read(1024) == bytes(1024), "Owned archive has extra/missing members/end blocks")
        while data := stream.read(65536):
            policy.require(not any(data), "Data after owned tar end")
    policy.require(facts(archive)["sha256"] == selected["sha256"], "Owned archive changed during reading")
    return actual


def copy_member(stream, size, target):
    if target is None:
        return policy.file_facts(stream, size)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    policy.require(not any(p.is_symlink() for p in (target, *target.parents)), "Redirected extraction path")
    with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
        remaining = size
        while remaining:
            data = stream.read(min(65536, remaining))
            policy.require(bool(data), "Truncated owned member")
            output.write(data)
            remaining -= len(data)
    return facts(target)


def intake(job, selected, token):
    official = verify_promotion(selected, token)
    folder = job.root / "artifact"
    read = lambda name: policy.json_data(policy.read_small(folder / name))
    result = read("result.json")
    policy.require(result.get("scope") == "actual-native-owned-file-only" and result.get("native_exit_code") == 0 and
                   result.get("producer") == selected["producer"] and result.get("owned_maven") == selected["owned_maven"], "Promotion result changed")
    policy.require(read("cleanup-ready.json") == {"owned_gradle_stop": "complete", "bootstrap_absent": True}, "Promotion cleanup incomplete")
    policy.require(read("selected-producer.json") == read("intake.json") == selected["producer"], "Original producer admission changed")
    before = read("source-before.json")
    policy.require(before == read("source-after.json") and before["source_sha"] == selected["producer"]["source_sha"] and
                   before["source_tree"] == selected["producer"]["source_tree"], "Original producer source changed")
    metadata = {name: policy.read_small(folder / name) for name in policy.METADATA}
    manifest = read("owned-maven.json")
    actual = owned_tar(folder / "owned-maven.tar", manifest, selected["owned_maven"])
    payloads = {row[6]: actual[row[6]] for line in policy.text_lines(metadata["inventory.tsv"])
                if not (row := line.split("\t"))[5].startswith("checksum:")}
    checked = policy.check_bundle(metadata, payloads, "0.2.1", before["files"])
    policy.require(all(checked[k] == selected["producer"][k] for k in ("inventory_sha256", "seal_sha256")), "Original sealed metadata changed")
    for line in policy.text_lines(metadata["inventory.tsv"]):
        row = line.split("\t")
        policy.require(manifest.get(row[6]) == {"size": int(row[7]), "sha256": row[8]}, "Owned sidecar/inventory mismatch")
    policy.require(owned_tar(folder / "owned-maven.tar", manifest, selected["owned_maven"], job.root / "maven") == actual,
                   "Owned bytes changed between validation and extraction")
    common.write_private(job.root / "inventory.tsv", metadata["inventory.tsv"])
    for name, data in metadata.items():
        common.write_private(job.root / "diagnostics" / name, data)
    owned.write_json(job.root / "diagnostics/official-promotion.json", official)
    owned.write_json(job.root / "diagnostics/admission.json", selected)


def runtime(env):
    context = {key: env.get(key, "") for key in common.CONTEXT_KEYS}
    phase = env.get("GITHUB_JOB")
    policy.require(context["GITHUB_REPOSITORY"] == policy.REPOSITORY and context["GITHUB_REPOSITORY_ID"] == "1310736214" and
                   context["GITHUB_REF"] == owned.CONTROL_REF and context["GITHUB_EVENT_NAME"] in ("push", "workflow_dispatch") and
                   context["GITHUB_WORKFLOW_REF"] == policy.REPOSITORY + "/" + WORKFLOW + "@" + owned.CONTROL_REF and
                   policy.hex_value(context["GITHUB_SHA"], 40) and context["GITHUB_SHA"] == context["GITHUB_WORKFLOW_SHA"] and
                   context["GITHUB_RUN_ATTEMPT"] == "1" and re.fullmatch(r"[1-9][0-9]{0,18}", context["GITHUB_RUN_ID"]), "Unselected consumer invocation")
    host = (env.get("RUNNER_OS"), env.get("RUNNER_ARCH"), platform.system(), platform.machine())
    policy.require(env.get("GITHUB_ACTIONS") == "true" and
                   ((phase == "linux" and host == ("Linux", "X64", "Linux", "x86_64")) or
                    (phase == "apple" and host == ("macOS", "ARM64", "Darwin", "arm64"))), "Actual selected hosted runner required")
    workspace, temp = Path(env["GITHUB_WORKSPACE"]), Path(env["RUNNER_TEMP"])
    policy.require(all(p.is_absolute() and p.resolve(strict=True) == p and re.fullmatch(r"/[A-Za-z0-9._/-]+", str(p))
                       for p in (workspace, temp)) and not temp.is_relative_to(workspace) and not workspace.is_relative_to(temp), "Separated owned paths required")
    return common.Runtime(workspace / "control", workspace / "app", temp / ("kira-source-consumer-" + context["GITHUB_RUN_ID"] + "-1-" + phase), context, phase)


def backend_only(job):
    if job.phase != "linux" or job.context["GITHUB_EVENT_NAME"] != "push":
        return False
    message = policy.git(job.control, "show", "-s", "--format=%B", job.context["GITHUB_WORKFLOW_SHA"])
    policy.require(len(message) <= 64 * 1024, "Consumer selector message bound")
    message = message.decode("utf-8").lower()  # Match GitHub's case-insensitive contains().
    return {tag for tag in ("apple", "linux", "backend") if "[consumer " + tag + "]" in message} == {"backend"}


def prepare(job, value, env):
    policy.check_environment(env)
    selected = selection(value)
    policy.source_snapshot(job.control, job.context["GITHUB_WORKFLOW_SHA"], fresh=True)
    for path in (job.root, job.producer, job.producer.with_name("backend"), fixture_root(job), job.root.parent / "kira-libffi-source",
                 job.root.parent / "kira-libffi-provenance.json"):
        policy.require(not path.exists() and not path.is_symlink(), "Consumer locations must be fresh")
    job.root.mkdir(mode=0o700)
    owned.write_json(job.root / "owner.json", job.owner())
    only_backend = backend_only(job)
    with Path(env["GITHUB_OUTPUT"]).open("a") as stream:
        stream.write(f"root={job.root}\nrun_id={selected['run_id']}\nartifact_id={selected['artifact_id']}\nbackend_only={str(only_backend).lower()}\n")
    labels = ("backend",) if only_backend else ("app", "backend")
    for name in ("artifact", "diagnostics", "tmp", *(label + "/" + folder for label in labels for folder in ("gradle", "home", "tmp"))):
        (job.root / name).mkdir(mode=0o700, parents=True, exist_ok=True)
    owned.write_json(job.root / "selection.json", selected)
    owned.write_json(job.root / "diagnostics/context.json", job.owner())
    free = shutil.disk_usage(job.root).free
    owned.write_json(job.root / "diagnostics/resources.json", {"free_disk_bytes": free, "host": platform.platform()})
    policy.require(free >= 12 * 1024**3, "Insufficient fresh hosted disk; do not purge shared caches")
    with Path(env["GITHUB_ENV"]).open("a") as stream:
        home = "backend" if only_backend else "app"
        stream.write(f"GRADLE_USER_HOME={job.root}/{home}/gradle\nTMPDIR={job.root}/tmp\n")


def source_state(root, sha):
    policy.require(policy.git(root, "rev-parse", "HEAD").decode().strip() == sha, "Consumer checkout SHA changed")
    policy.git(root, "diff", "--cached", "--quiet", sha, "--")
    policy.require(not policy.git(root, "ls-files", "--others", "--exclude-standard", "-z"), "Unexpected untracked consumer source")
    files, total = {}, 0
    entries = policy.git(root, "ls-tree", "-rz", "--full-tree", sha).split(b"\x00")
    policy.require(len(entries) <= 8193, "Consumer source entry bound")
    for entry in filter(None, entries):
        header, name = entry.decode("utf-8").split("\t", 1)
        mode, kind, blob = header.split(" ")
        policy.require(mode in ("100644", "100755") and kind == "blob", "Regular consumer source required")
        path = root / name
        policy.require(path.resolve(strict=True).is_relative_to(root), "Redirected consumer source")
        data = policy.read_small(path)
        total += len(data)
        actual_mode = "100755" if path.stat().st_mode & 0o111 else "100644"
        policy.require(total <= policy.MAX_BYTES and actual_mode == mode and
                       hashlib.sha1(b"blob " + str(len(data)).encode() + b"\x00" + data, usedforsecurity=False).hexdigest() == blob,
                       "Committed consumer bytes/mode changed")
        files[name] = {"size": len(data), "sha256": policy.digest(data), "mode": mode}
    return {"source_sha": sha, "source_tree": policy.git(root, "rev-parse", sha + "^{tree}").decode().strip(),
            "files": len(files), "file_manifest_sha256": policy.digest(common.encoded(files))}


def child_environment(job, label, env):
    result = {key: env[key] for key in ("JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT", "DEVELOPER_DIR") if key in env}
    if job.phase == "apple":
        result.update({key: env[key] for key in ("GRADLE_OPTS", "KONAN_DATA_DIR")})
    result.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", HOME=str(job.root / label / "home"), TMPDIR=str(job.root / label / "tmp"),
                  GRADLE_USER_HOME=str(job.root / label / "gradle"), CI="true", LANG="C.UTF-8" if job.phase == "linux" else "en_US.UTF-8")
    if job.phase == "apple":
        result["HOME"] = env["HOME"]  # CoreSimulator uses the actual runner user; caches/toolchain remain explicitly owned.
    result.update(KIRA_SOURCE_COHORT_REPOSITORY=str(job.root / "maven"), KIRA_SOURCE_COHORT_INVENTORY=str(job.root / "inventory.tsv"),
                  KIRA_SOURCE_COHORT_INVENTORY_SHA256=selection(policy.json_data(policy.read_small(job.root / "selection.json")))["producer"]["inventory_sha256"])
    return result


def app_bootstrap(job):
    path = job.producer / "scripts/release/prepare-libffi-source.py"
    data = policy.read_small(path)
    policy.require(data == policy.git(job.producer, "show", APP_SHA + ":scripts/release/prepare-libffi-source.py"),
                   "Uncommitted App bootstrap refused")
    helper = types.ModuleType("kira_app_consumer_bootstrap")
    helper.__file__ = str(path)
    exec(compile(data, str(path), "exec"), helper.__dict__)
    return helper


def fixture_root(job):
    mode = "ios" if job.phase == "apple" else "android"
    return job.root.parent / ("kira-single-hop-" + job.context["GITHUB_RUN_ID"] + "-1-" + mode)


def fixture(job, env, action):
    # The reviewed App helper, not this caller, owns TLS and simulator lifecycle.
    root = fixture_root(job)
    if action == "cleanup" and not root.exists() and not root.is_symlink():
        return None
    mode = "ios" if job.phase == "apple" else "android"
    helper = job.producer / "scripts/ci/single-hop-loopback.py"
    policy.require(policy.read_small(helper) == policy.git(job.producer, "show", APP_SHA + ":scripts/ci/single-hop-loopback.py"),
                   "Uncommitted fixture helper refused")
    child = child_environment(job, "app", env)
    child.update({key: env[key] for key in (*common.CONTEXT_KEYS, "GITHUB_ACTIONS", "GITHUB_WORKSPACE", "RUNNER_TEMP", "RUNNER_OS", "RUNNER_ARCH")})
    log = job.root / "diagnostics" / ("fixture-" + action + ".log")
    if log.exists():
        log = log.with_name("fixture-" + action + "-final.log")
    seconds = 780 if action == "prepare" and mode == "ios" else 240
    started = time.monotonic()
    try:
        code = run_command(["/usr/bin/python3", "-B", str(helper), action, mode], job.producer, child, log, seconds)
    finally:
        owned.write_json(log.with_suffix(".json"), {"action": action, "platform": mode, "elapsed_seconds": round(time.monotonic() - started, 3)})
        if action == "cleanup":
            for name in ("owner.json", "ready.json", "simulator.json", "stats.json", "cleanup.json", "server.log"):
                path = root / name
                if path.exists():
                    common.write_private(job.root / "diagnostics" / (log.stem + "-" + name), policy.read_small(path))
    policy.require(code == 0, "Owned transport fixture phase failed")
    if action == "prepare":
        ready = policy.json_data(policy.read_small(root / "ready.json"))
        owner = {key: job.context[key] for key in ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA")}
        owner.update(platform=mode, script_sha256=facts(helper)["sha256"])
        policy.require(ready.get("owner") == owner and policy.json_data(policy.read_small(root / "owner.json")) == owner and
                       ready.get("platform") == mode and re.fullmatch(r"https://localhost:[1-9][0-9]{0,4}", ready.get("origin", "")) and
                       re.fullmatch(r"[0-9a-f]{32}", ready.get("fixture_id", "")), "Unexpected localhost fixture")
        return ready
    policy.require(policy.json_data(policy.read_small(root / "cleanup.json")) ==
                   {"server_stopped": True, "simulator_deleted": True, "secret_files_removed": True, "failures": []}, "Fixture cleanup incomplete")
    policy.require(not (root / "ready.json").exists() or (root / "stats.json").is_file(), "Final fixture socket evidence missing")
    policy.require(not root.is_symlink() and shutil.rmtree.avoids_symlink_attacks, "Unsafe fixture disposal")
    shutil.rmtree(root)


def worker_marker(job, label):
    return "-Dkira.source.consumer.owner=" + job.context["GITHUB_RUN_ID"] + "-" + job.phase + "-" + label


def reap_owned(job, label):
    # Same exact-marker ownership technique as the existing App Native gate;
    # private argument files and a Native binary's own build path bind children.
    roots = [str(job.root / label) + "/", str(job.producer.with_name(label)) + "/"]
    native_roots = [job.producer / path for path in ("composeApp/build", "sources/engine/build")] if label == "app" else []

    def matches(parts):
        executable = Path(parts[1])
        native = executable.is_absolute() and any(executable.resolve().is_relative_to(root) for root in native_roots)
        compiler = re.fullmatch(r"java|konanc|cinterop|clang(?:\+\+)?(?:-[0-9]+)?|ld", executable.name)
        return (worker_marker(job, label) in parts[1:] or native or
                (compiler and any(root in part for root in roots for part in parts[1:])))

    def pids():
        output = subprocess.check_output(["/bin/ps", "-axo", "pid=,command="], timeout=15)
        policy.require(len(output) <= policy.MAX_META, "Process observation bound")
        return [int(parts[0]) for line in output.decode().splitlines() if len(parts := line.split()) > 1 and matches(parts)]

    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids():
            policy.require(pid != os.getpid(), "Refusing to reap the controller")
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        if not pids():
            return
        time.sleep(3)
    policy.require(not pids(), "Owned consumer worker remains")


def stop_owned(job, label, env):
    home = job.root / label / "gradle"
    policy.require(not home.is_symlink(), "Redirected consumer Gradle home")
    version = "8.14.5" if label == "backend" else "9.6.1"
    launchers = list(home.glob(f"wrapper/dists/gradle-{version}-bin/*/gradle-{version}/bin/gradle"))
    policy.require(len(launchers) <= 1, "Ambiguous owned Gradle launcher")
    try:
        if launchers:
            with (job.root / "diagnostics" / (label + "-stop.log")).open("ab") as output:
                subprocess.run([str(policy.regular(launchers[0])), "--offline", "--no-daemon", "--stop"], cwd=job.root,
                               env=child_environment(job, label, env), stdin=subprocess.DEVNULL, stdout=output,
                               stderr=subprocess.STDOUT, timeout=60, check=True)
        else:
            policy.require(not (home / "daemon").exists(), "Owned daemon registry without a launcher")
    finally:
        reap_owned(job, label)


def run_command(command, cwd, env, output, seconds):
    process = None
    try:
        with output.open("xb") as log:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + seconds
            while process.poll() is None:
                policy.require(time.monotonic() < deadline and os.fstat(log.fileno()).st_size <= 64 * 1024 * 1024, "Batch time/output limit")
                time.sleep(0.5)
            policy.require(os.fstat(log.fileno()).st_size <= 64 * 1024 * 1024, "Batch output limit")
            return process.returncode
    finally:
        if process is not None:
            # Terminate the owned group even when its launcher has just exited.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        if output.exists() and output.stat().st_size > 64 * 1024 * 1024:
            with output.open("r+b") as log:
                log.truncate(64 * 1024 * 1024)


def scope(job, label, ready=None):
    only_backend = backend_only(job)
    policy.require(not only_backend or label == "backend", "Backend-only cannot select App tasks")
    if label == "backend":
        if only_backend:
            return ["test", *[part for name in RECONCILIATION_TESTS for part in ("--tests", name)], "-x", "jacocoTestReport"], {
                "build/test-results/test": set(RECONCILIATION_TESTS)}
        names = [BACKEND_PREFIX + name for name in BACKEND_TESTS] + [READER_TEST + "." + FINITE_FEEDS]
        return ["test", *[part for name in names for part in ("--tests", name)], "-x", "jacocoTestReport"], {
            "build/test-results/test": {BACKEND_PREFIX + name for name in BACKEND_TESTS} | {READER_TEST}}
    if job.phase == "apple":
        return [":composeApp:compileKotlinIosArm64", ":composeApp:compileKotlinIosSimulatorArm64",
                ":composeApp:iosSimulatorArm64Test", "--device", ready["simulator_uuid"], "--tests", IOS_TRANSPORT], {
                    "composeApp/build/test-results/iosSimulatorArm64Test": {IOS_TRANSPORT}}
    selectors = [ENGINE_PREFIX + name for name in ("DefaultSourceConfigValidator*Test", "GenericSourceClient*GoldenTest", "GenericSourceClientScriptJsonTest")]
    tasks = [":sources:engine:desktopTest", *[part for name in selectors for part in ("--tests", name)], ":composeApp:desktopTest"]
    names = [RUNTIME_PREFIX + name for name in RUNTIME_TESTS] + [LIFECYCLE, RUNTIME_PREFIX + "*PilotParityTest"]
    tasks += [part for name in names for part in ("--tests", name)] + [":composeApp:compileAndroidMain",
              ":composeApp:testAndroidHostTest", "--tests", ANDROID_TRANSPORT]
    return tasks, {"sources/engine/build/test-results/desktopTest": {ENGINE_PREFIX + name for name in ENGINE_TESTS},
                   "composeApp/build/test-results/desktopTest": {RUNTIME_PREFIX + name for name in RUNTIME_TESTS} |
                   {RUNTIME_PREFIX + name + "PilotParityTest" for name in PILOTS} | {LIFECYCLE},
                   "composeApp/build/test-results/testAndroidHostTest": {ANDROID_TRANSPORT}}


def capture_xml(job, label, source, expected):
    target = job.root / "diagnostics" / (label + "-xml")
    target.mkdir(mode=0o700)
    observed, valid = {}, True
    for directory, wanted in expected.items():
        paths = list((source / directory).glob("TEST-*.xml"))
        policy.require(len(paths) <= 64, "XML entry bound")
        found = set()
        for path in paths:
            data = policy.read_small(path)
            policy.require(len(data) <= 2 * 1024 * 1024 and b"<!DOCTYPE" not in data and b"<!ENTITY" not in data, "XML size/entity refused")
            common.write_private(target / path.name, data)
            suite = ET.fromstring(data)
            cases = suite.findall("testcase")
            # KMP JVM uses a display-only suite name (Class[desktop]); Native
            # prefixes testcase class identity with the exact Gradle task name.
            prefix = "iosSimulatorArm64Test." if Path(directory).name == "iosSimulatorArm64Test" else ""
            names = {case.attrib.get("classname", "").removeprefix(prefix) for case in cases}
            name = next(iter(names)) if len(names) == 1 else None
            methods = [case.attrib.get("name", "") for case in cases]
            counts = {key: int(suite.attrib[key]) for key in ("tests", "failures", "errors", "skipped")}
            valid &= suite.tag == "testsuite" and name in wanted and name not in found and counts["tests"] == len(cases) > 0 and all(counts[key] == 0 for key in ("failures", "errors", "skipped"))
            valid &= len(set(methods)) == len(methods) and all(methods)
            valid &= not any(case.find(kind) is not None for case in cases for kind in ("failure", "error", "skipped"))
            if name == READER_TEST:
                valid &= len(cases) == 1 and cases[0].attrib.get("name", "").removesuffix("()") == FINITE_FEEDS
            if name in RECONCILIATION_TESTS:
                valid &= counts["tests"] == RECONCILIATION_TESTS[name]
            if name in (ANDROID_TRANSPORT, IOS_TRANSPORT):
                suffix = "[iosSimulatorArm64]" if name == IOS_TRANSPORT else "[android]"
                valid &= len(cases) == 3 and {method.removesuffix(suffix) for method in methods} == TRANSPORT_METHODS
            found.add(name)
            observed[name] = counts
        valid &= found == wanted
    return {"valid": bool(valid), "classes": observed}


def docker_empty(env, settle=False):
    deadline = time.monotonic() + (60 if settle else 0)
    while True:
        result = subprocess.run(["/usr/bin/docker", "ps", "-aq", "--filter", "label=org.testcontainers=true"], env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=10)
        if not result.stdout.strip():
            return
        policy.require(time.monotonic() < deadline, "Testcontainers remain; no global Docker deletion is permitted")
        time.sleep(1)  # Ryuk cleanup follows Test JVM exit; never remove unrelated containers.


def dispose_scope(job, label):
    policy.require(shutil.rmtree.avoids_symlink_attacks, "Safe owned disposal unavailable")
    for path in (job.producer.with_name(label), job.root / label):
        policy.require(not path.is_symlink(), "Redirected consumer disposal")
        if path.exists():
            shutil.rmtree(path)
        policy.require(not path.exists() and not path.is_symlink(), "Consumer disposal incomplete")


def native_cleanup(job, env):
    root = job.root.parent / "kira-libffi-source"
    if root.exists() or root.is_symlink():
        app_bootstrap(job).cleanup(env)
    receipt = job.root.parent / "kira-libffi-provenance.json"
    if receipt.exists():
        helper = app_bootstrap(job)
        value = policy.json_data(policy.read_small(common.private_file(receipt)))
        policy.require(value.get("owner") == helper.owner(helper.owned_root(env), env) and value.get("cleanup") == "complete", "Unowned/incomplete bootstrap cleanup")
        common.write_private(job.root / "diagnostics/libffi-provenance.json", policy.read_small(receipt))
        receipt.unlink()
    policy.require(not root.exists() and not root.is_symlink(), "Bootstrap remains")


def backend_prerequisites(job):
    if backend_only(job):
        policy.require(not list((job.root / "diagnostics").glob("app-*")), "Backend-only cannot reuse App evidence")
    else:
        app_result = policy.json_data(policy.read_small(job.root / "diagnostics/app-result.json"))
        policy.require(app_result.get("workers_stopped") is True, "App workers must be stopped before Backend")
    policy.require(all(not path.exists() and not path.is_symlink() for path in
                       (job.producer, job.root / "app", fixture_root(job))), "App checkout/fixture/outputs must be absent before Backend")


def batch(job, label, env):
    policy.require((label == "app" and not backend_only(job)) or (label == "backend" and job.phase == "linux"), "Unselected batch")
    selected = selection(policy.json_data(policy.read_small(job.root / "selection.json")))
    policy.require(policy.json_data(policy.read_small(job.root / "diagnostics/admission.json")) == selected, "Admitted owned cohort required")
    source = job.producer.with_name(label)
    sha = BACKEND_SHA if label == "backend" else APP_SHA
    before = source_state(source, sha)
    owned.write_json(job.root / "diagnostics" / (label + "-source-before.json"), before)
    child = child_environment(job, label, env)
    policy.check_environment({k: v for k, v in child.items() if k != "GRADLE_OPTS"})
    policy.require(run_command([str(Path(child["JAVA_HOME"]) / "bin/java"), "-version"], source, child,
                   job.root / "diagnostics" / (label + "-java.log"), 30) == 0, "Consumer Java identity unavailable")
    if label == "app":
        policy.existing_sdk37(child)
        policy.require(facts(source / "scripts/ci/source-cohort.init.gradle")["sha256"] == APP_INIT_SHA, "Reviewed App adapter changed")
    if job.phase == "apple":
        app_bootstrap(job).verify(env)
    if label == "backend":
        # A failed App assertion must not hide independent Backend findings, but
        # never overlap it with an incompletely stopped/disposed App batch.
        backend_prerequisites(job)
        docker_empty(child)
        policy.require(run_command(["/usr/bin/docker", "version"], source, child,
                       job.root / "diagnostics/docker-version.log", 30) == 0, "Actual Docker unavailable")
    ready = fixture(job, env, "prepare") if label == "app" else None
    if ready:
        child.update(KIRA_SINGLE_HOP_ORIGIN=ready["origin"], KIRA_SINGLE_HOP_FIXTURE_ID=ready["fixture_id"])
        if job.phase == "apple":
            child["KIRA_SINGLE_HOP_SIMULATOR"] = ready["simulator_uuid"]
        else:
            child.update(KIRA_SINGLE_HOP_TRUSTSTORE=ready["truststore"], KIRA_SINGLE_HOP_TRUSTSTORE_SHA256=ready["truststore_sha256"])
    tasks, expected = scope(job, label, ready)
    init = job.control / ".github/scripts/backend-source-cohort.init.gradle" if label == "backend" else source / "scripts/ci/source-cohort.init.gradle"
    heap = "2g" if label == "backend" else "3g"
    command = [str(source / "gradlew"), *FLAGS, "-Dorg.gradle.jvmargs=-Xmx" + heap +
               " -XX:MaxMetaspaceSize=768m -Dfile.encoding=UTF-8 " + worker_marker(job, label), "-I", str(init), *tasks]
    owned.write_json(job.root / "diagnostics" / (label + "-command.json"), command)
    result, error = {"scope": "focused-consumer-and-owned-single-hop-not-release"}, None
    started = time.monotonic()
    try:
        seconds = 2400 if job.phase == "apple" else 900 if label == "backend" else 1500
        result["exit_code"] = run_command(command, source, child, job.root / "diagnostics" / (label + ".log"), seconds)
    except Exception as caught:
        error = caught
    finally:
        result["gradle_elapsed_seconds"] = round(time.monotonic() - started, 3)
        try:
            stop_owned(job, label, env)
            result["workers_stopped"] = True
        finally:
            result["xml"] = capture_xml(job, label, source, expected)
            owned.write_json(job.root / "diagnostics" / (label + "-result.json"), result)
    after = source_state(source, sha)
    owned.write_json(job.root / "diagnostics" / (label + "-source-after.json"), after)
    policy.require(before == after, "Consumer source changed during batch")
    policy.require(owned.tree_hashes(job.root / "maven") == policy.json_data(policy.read_small(job.root / "artifact/owned-maven.json")), "Owned Maven tree changed during batch")
    if label == "app":
        fixture(job, env, "cleanup")
    if job.phase == "apple":
        try:
            app_bootstrap(job).verify(env)
        finally:
            native_cleanup(job, env)
    if label == "backend":
        docker_empty(child, settle=True)
    data = policy.regular(job.root / "diagnostics" / (label + ".log")).read_bytes()
    complete = [line for line in data.splitlines() if line.startswith(b"KIRA_SOURCE_COHORT_COMPLETE ")]
    dispose_scope(job, label)  # Capture and verified shutdown precede every batch's disposal.
    policy.require(error is None and result.get("exit_code") == 0 and result["xml"]["valid"] and len(complete) == 1,
                   "Consumer batch failed; inspect actual logs/XML/witnesses")


def finalize(job, env):
    job.check()
    failures, stopped = [], {}

    def attempt(category, action):
        try:
            action()
            return True
        except Exception:
            failures.append(category)  # Static categories only, never submitted values or credentials.
            return False

    for label in ("app", "backend"):
        stopped[label] = not (job.root / label).exists() or attempt(label + "-workers", lambda: stop_owned(job, label, env))
    attempt("fixture-cleanup", lambda: fixture(job, env, "cleanup"))
    if job.phase == "apple":
        if stopped["app"]:
            attempt("native-bootstrap", lambda: native_cleanup(job, env))
        else:
            failures.append("native-blocked-by-app-workers")  # Never delete a possibly in-use compiler tree.
    elif (job.root / "diagnostics/backend-command.json").exists():
        attempt("docker-settlement", lambda: docker_empty(child_environment(job, "backend", env), settle=True))
    owned.write_json(job.root / "diagnostics/finalize-result.json", {"failures": failures})
    policy.require(not failures, "Owned cleanup incomplete; inspect static failure categories")
    owned.write_json(job.root / "diagnostics/cleanup-ready.json", {"owned_gradle_stop": "complete", "bootstrap_absent": True})


def main():
    try:
        policy.require(len(sys.argv) == 2 and sys.argv[1] in ("prepare", "intake", "bootstrap", "app", "backend", "finalize", "dispose"), "Consumer command refused")
        mode, env = sys.argv[1], dict(os.environ)
        token = env.pop("KIRA_INTAKE_READ_TOKEN", None)
        job = runtime(env)
        os.umask(0o077)
        os.environ.clear()
        os.environ.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", HOME=str(job.root / "tmp"), TMPDIR=str(job.root / "tmp"), LANG="C")
        home = "backend" if backend_only(job) else "app"
        os.environ["HOME"] = str(job.root / home / "home")
        signal.signal(signal.SIGTERM, lambda *_: policy.require(False, "Interrupted"))
        if mode == "prepare":
            prepare(job, policy.json_data(env.get("KIRA_PROMOTION_SELECTION", "").encode()), env)
        else:
            job.check()
            if mode == "intake":
                intake(job, selection(policy.json_data(policy.read_small(job.root / "selection.json"))), token)
            elif mode == "bootstrap":
                policy.require(job.phase == "apple", "Bootstrap requires Apple job")
                policy.require(policy.json_data(policy.read_small(job.root / "diagnostics/admission.json")) ==
                               selection(policy.json_data(policy.read_small(job.root / "selection.json"))), "Admission required before bootstrap")
                source_state(job.producer, APP_SHA)
                app_bootstrap(job).prepare(env)
            elif mode in ("app", "backend"):
                batch(job, mode, env)
            elif mode == "finalize":
                finalize(job, env)
            else:
                policy.require(policy.json_data(policy.read_small(job.root / "diagnostics/cleanup-ready.json")) ==
                               {"owned_gradle_stop": "complete", "bootstrap_absent": True}, "Verified worker cleanup required")
                for label in ("app", "backend"):
                    dispose_scope(job, label)
                shutil.rmtree(job.root)
        print("Focused phase completed; actual batch logs/XML determine coverage. No publication or Dilar qualification claim.")
        return 0
    except Exception:
        print("Focused consumer phase refused; inspect bounded diagnostics. No qualification waiver.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
