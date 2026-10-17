#!/usr/bin/env python3
"""One fresh owned-file roundtrip of the selected real cohort; never remote publication."""

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

import connected_publication_intake as connected
import github_publication_receipts as receipts
import publication_policy as policy
import publisher_publication_intake as intake
import publisher_workflow as common


CONTROL_REF = "refs/heads/remediation/priority-sources-20261017"
WORKFLOW = ".github/workflows/owned-file-promotion-check.yml"
PRODUCER_SHA = "e614df497d0a598feb097fd6d18d67051fd3e531"
PRODUCER_RUN = 39028369302
FIELDS = intake.PRODUCER_FIELDS
FLAGS = ["--no-daemon", "--no-parallel", "--max-workers=1", "--no-build-cache", "--no-configuration-cache",
         "--console=plain", "--info", "-Dorg.gradle.jvmargs=-Xmx3g -XX:MaxMetaspaceSize=512m -Dfile.encoding=UTF-8",
         "-Pkotlin.compiler.execution.strategy=in-process", "-Pandroid.builder.sdkDownload=false",
         "-Porg.gradle.java.installations.auto-download=false"]


def selection(value):
    policy.require(type(value) is dict and set(value) == FIELDS, "Exact selected producer tuple required")
    value = receipts.validated_request(value, None)
    policy.require((value["source_sha"], value["run_id"], value["run_attempt"]) ==
                   (PRODUCER_SHA, PRODUCER_RUN, 1), "Only the selected first-attempt cohort is admitted")
    return value


def runtime(env):
    context = {key: env.get(key, "") for key in common.CONTEXT_KEYS}
    policy.require(context["GITHUB_REPOSITORY"] == policy.REPOSITORY and context["GITHUB_REPOSITORY_ID"] == "1310736214"
                   and context["GITHUB_REF"] == CONTROL_REF and context["GITHUB_EVENT_NAME"] in ("push", "workflow_dispatch")
                   and context["GITHUB_WORKFLOW_REF"] == policy.REPOSITORY + "/" + WORKFLOW + "@" + CONTROL_REF
                   and policy.hex_value(context["GITHUB_SHA"], 40) and context["GITHUB_WORKFLOW_SHA"] == context["GITHUB_SHA"]
                   and context["GITHUB_RUN_ATTEMPT"] == "1" and re.fullmatch(r"[1-9][0-9]{0,18}", context["GITHUB_RUN_ID"])
                   and int(context["GITHUB_RUN_ID"]) != PRODUCER_RUN,
                   "Unselected control invocation")
    policy.require(env.get("GITHUB_ACTIONS") == "true" and env.get("RUNNER_OS") == "macOS" and env.get("RUNNER_ARCH") == "ARM64"
                   and platform.system() == "Darwin" and platform.machine() == "arm64", "Actual hosted Darwin ARM64 required")
    workspace, temp = Path(env["GITHUB_WORKSPACE"]), Path(env["RUNNER_TEMP"])
    policy.require(workspace.is_absolute() and temp.is_absolute() and workspace.resolve(strict=True) == workspace
                   and temp.resolve(strict=True) == temp and not temp.is_relative_to(workspace)
                   and not workspace.is_relative_to(temp)
                   and all(re.fullmatch(r"/[A-Za-z0-9._/-]+", str(path)) for path in (workspace, temp)),
                   "Separated owned paths required")
    root = temp / ("kira-owned-promotion-" + context["GITHUB_RUN_ID"] + "-1")
    return common.Runtime(workspace / "control", workspace / "producer", root, context, "owned-file")


def write_json(path, value):
    common.write_private(path, common.encoded(value))


def prepare(job, request, env):
    policy.check_environment(env)
    selected = selection(request)
    policy.source_snapshot(job.control, job.context["GITHUB_WORKFLOW_SHA"], fresh=True)
    for path in (job.root, job.producer, job.producer.with_name("tooling"),
                 job.root.parent / "kira-libffi-source", job.root.parent / "kira-libffi-provenance.json"):
        policy.require(not path.exists() and not path.is_symlink(), "Fresh owned locations required")
    job.root.mkdir(mode=0o700)
    write_json(job.root / "owner.json", job.owner())
    # Expose custody before later setup can fail; always-cleanup can then find it.
    with Path(env["GITHUB_OUTPUT"]).open("a") as stream:
        stream.write(f"root={job.root}\n")
    for name in ("artifact", "diagnostics", "gradle", "home", "tmp"):
        (job.root / name).mkdir(mode=0o700)
    data = common.encoded(selected)
    common.write_private(job.root / "selection.json", data)
    write_json(job.root / "diagnostics/context.json", job.owner())
    write_json(job.root / "diagnostics/selected-producer.json", selected)
    return {"root": str(job.root), "selection_sha256": policy.digest(data), "producer_sha": selected["source_sha"],
            "producer_run_id": selected["run_id"], "producer_artifact_id": selected["artifact_id"]}


def load_selection(job, digest):
    job.check()
    policy.source_snapshot(job.control, job.context["GITHUB_WORKFLOW_SHA"], fresh=True)
    data = policy.read_small(common.private_file(job.root / "selection.json"))
    policy.require(policy.hex_value(digest) and policy.digest(data) == digest, "Selected producer changed")
    return selection(policy.json_data(data))


def metadata(job, selected, *, fresh=True):
    policy.require(policy.json_data(policy.read_small(job.root / "diagnostics/intake.json")) == selected,
                   "Real connected intake must precede tooling or staging")
    return intake._candidate_bytes(job.producer, {"producer": selected}, job.root / policy.ARCHIVE, fresh=fresh)


def acquire(job, selected, token):
    archive = common.downloaded(job, "artifact", policy.ARCHIVE, policy.MAX_BYTES)
    asset = common.private_file(job.root / policy.GH_DARWIN_ASSET)
    proof = job.root / "attestation.json"
    common.write_private(proof, receipts.acquire_attestation_bundle(selected, token=token))
    connected.connected_intake(job.producer, selected, archive, asset, proof, token=token)
    source = policy.source_snapshot(job.producer, selected["source_sha"], fresh=True)
    write_json(job.root / "diagnostics/source-before.json", source)
    write_json(job.root / "diagnostics/intake.json", selected)
    for name, data in metadata(job, selected).items():
        common.write_private(job.root / "diagnostics" / name, data)


def bootstrap(job):
    # Control and producer checkouts stay distinct; the unchanged helper gets the
    # actual producer path explicitly. Never spoof GITHUB_SHA or call ci-start.
    scripts = {name: policy.read_small(job.control / name) for name in policy.BOOTSTRAP_FILES}
    policy.require(all(policy.digest(scripts[name]) == digest for name, digest in policy.BOOTSTRAP_FILES.items()),
                   "Bootstrap review drift")
    path = job.control / "scripts/release/prepare-libffi-source.py"
    helper = types.ModuleType("kira_owned_file_bootstrap")
    helper.__file__ = str(path)
    exec(compile(scripts["scripts/release/prepare-libffi-source.py"], str(path), "exec"), helper.__dict__)
    return helper


def profile(job, env):
    policy.check_environment({key: value for key, value in env.items() if key != "GRADLE_OPTS"})
    helper = bootstrap(job)
    owned = helper.owned_root(env)
    home = owned / "compiler/kotlin-native-prebuilt-macos-aarch64-2.2.21"
    policy.require(env.get("GRADLE_USER_HOME") == str(job.root / "gradle")
                   and env.get("GRADLE_OPTS") == helper.NATIVE_HOME + str(home) + " " + helper.CACHE_OFF
                   and env.get("KONAN_DATA_DIR") == str(owned / "data"), "Exact owned verified native profile required")
    helper.verify(env, job.producer)


def child_environment(job, env):
    # No ambient GitHub/Actions/package credential, JVM option, init script or
    # project-property discovery in any Gradle/Java/stop subprocess.
    result = {key: env[key] for key in ("JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT", "DEVELOPER_DIR",
                                      "GRADLE_OPTS", "KONAN_DATA_DIR") if key in env}
    result.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", HOME=str(job.root / "home"), TMPDIR=str(job.root / "tmp"),
                  GRADLE_USER_HOME=str(job.root / "gradle"), LANG="C", LC_ALL="C")
    return result


def toolchain(job, selected, env):
    tools = {}
    for line in policy.text_lines(metadata(job, selected)["model.tsv"]):
        row = line.split("\t")
        if row[0] == "tool":
            policy.require(len(row) == 3 and row[1] not in tools, "Duplicate or malformed sealed tool row")
            tools[row[1]] = row[2]
    keys = {"java.runtime.version", "java.vendor", "os.name", "os.arch"}
    policy.require(set(tools) == keys | {"gradle"} and tools["gradle"] == "9.6.1", "Unsupported sealed tool model")
    policy.existing_sdk37(env)
    java = Path(env["JAVA_HOME"]) / "bin/java"
    result = subprocess.run([str(java), "-XshowSettings:properties", "-version"], env=child_environment(job, env),
                            check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    policy.require(len(result.stdout) <= 65536, "Java identity output bound exceeded")
    actual = {}
    for line in result.stdout.decode("utf-8").splitlines():
        match = re.fullmatch(r"\s*([A-Za-z0-9_.]+) = (.+)", line)
        if match and match[1] in keys:
            policy.require(match[1] not in actual, "Duplicate actual runtime property")
            actual[match[1]] = match[2]
    policy.require(actual == {key: tools[key] for key in keys} and actual["os.name"] == "Mac OS X"
                   and actual["os.arch"] == "aarch64" and actual["java.runtime.version"].startswith("21."),
                   "Actual runtime differs from the sealed producer")
    return actual


def promotion_command(job, selected, bundle):
    producers = policy.text_lines(bundle["producers.txt"])
    policy.require(producers == sorted(set(producers)) and all(re.fullmatch(policy.TASK, task) for task in producers),
                   "Exact sealed exclusions required")
    return [str(job.producer / "gradlew"), "--offline", *FLAGS, "-PkiraPublicationMode=promote",
            "-PkiraPublicationSealSha256=" + selected["seal_sha256"],
            *[part for task in producers for part in ("-x", task)], ":promoteKiraPublicationBytes"]


def stop_owned(job, env, label):
    home = job.root / "gradle"
    policy.require(not home.is_symlink(), "Redirected Gradle home")
    launchers = list(home.glob("wrapper/dists/gradle-9.6.1-bin/*/gradle-9.6.1/bin/gradle"))
    policy.require(len(launchers) <= 1, "Ambiguous owned Gradle installation")
    if launchers:
        launcher = policy.regular(launchers[0])
        with (job.root / "diagnostics" / (label + "-stop.log")).open("xb") as log:
            subprocess.run([str(launcher), "--offline", "--no-daemon", "--stop"], cwd=job.root,
                           env=child_environment(job, env), stdin=subprocess.DEVNULL,
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
    else:
        policy.require(not (home / "daemon").exists(), "Owned daemon registry has no launcher")


def run_gradle(job, env, command, cwd, label):
    write_json(job.root / "diagnostics" / (label + "-command.json"), command)
    process = None
    try:
        with (job.root / "diagnostics" / (label + ".log")).open("xb") as log:
            process = subprocess.Popen(command, cwd=cwd, env=child_environment(job, env), stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + 300
            while process.poll() is None:
                policy.require(time.monotonic() < deadline and os.fstat(log.fileno()).st_size <= 64 * 1024 * 1024,
                               "Owned Gradle time/output bound exceeded")
                time.sleep(0.5)
            policy.require(os.fstat(log.fileno()).st_size <= 64 * 1024 * 1024, "Owned Gradle output bound exceeded")
            policy.require(process.returncode == 0, "Owned Gradle invocation failed; inspect bounded diagnostics")
    finally:
        try:
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)
        finally:
            try:
                stop_owned(job, env, label)  # Even a process-exit race must not skip owned shutdown.
            finally:
                log_path = job.root / "diagnostics" / (label + ".log")
                if log_path.exists() and policy.regular(log_path).stat().st_size > 64 * 1024 * 1024:
                    with log_path.open("r+b") as log:
                        log.truncate(64 * 1024 * 1024)  # Keep failed diagnostic uploads bounded too.


def warmup(job, selected, env):
    scratch = job.producer.with_name("tooling")
    policy.source_snapshot(scratch, selected["source_sha"], fresh=True)
    profile(job, env)
    write_json(job.root / "diagnostics/runtime.json", toolchain(job, selected, env))
    run_gradle(job, env, [str(scratch / "gradlew"), *FLAGS, "help"], scratch, "tooling")
    policy.source_snapshot(scratch, selected["source_sha"], fresh=False)
    shutil.rmtree(scratch)  # Reserved absent by prepare; never copy project histories to producer.
    write_json(job.root / "diagnostics/tooling-complete.json", {"task": "help", "scratch_absent": True})


def tree_hashes(root):
    policy.require(root.is_dir() and not root.is_symlink(), "Missing actual output tree")
    result, total = {}, 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        policy.require(not any((Path(directory) / name).is_symlink() for name in dirs), "Redirected output tree")
        for name in sorted(names):
            path = policy.regular(Path(directory) / name)
            total += path.stat().st_size
            policy.require(total <= policy.MAX_BYTES and len(result) < policy.MAX_ENTRIES, "Output evidence bound exceeded")
            with path.open("rb") as stream:
                facts = policy.file_facts(stream, path.stat().st_size)
            result[path.relative_to(root).as_posix()] = {key: facts[key] for key in ("size", "sha256")}
    policy.require(bool(result), "Empty actual output tree")
    return result


def archive_owned_maven(job, files):
    """Retain the actual native writer bytes for later consumers; never regenerate."""
    source = job.producer / "build/publication-bytes/owned-maven"
    target = job.root / "diagnostics/owned-maven.tar"
    policy.require(tree_hashes(source) == files, "Native destination changed before archival")
    with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
        with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name, facts in sorted(files.items()):
                path = policy.regular(source / policy.relative_path(name))
                member = tarfile.TarInfo(name)
                member.size, member.mode = facts["size"], 0o600
                with path.open("rb") as stream:
                    archive.addfile(member, stream)
    policy.require(tree_hashes(source) == files, "Native destination changed during archival")
    with target.open("rb") as stream:
        facts = policy.file_facts(stream, target.stat().st_size)
    # Verify the retained bytes themselves, not just the filesystem before/after
    # copying. No extraction to disk, implicit cache or second publication action.
    with tarfile.open(target, "r:") as archive:
        members = archive.getmembers()
        policy.require([member.name for member in members] == sorted(files), "Retained archive membership changed")
        for member in members:
            policy.require(member.type == tarfile.REGTYPE and not member.linkname
                           and member.size == files[member.name]["size"], "Retained archive member changed")
            with archive.extractfile(member) as stream:
                actual = policy.file_facts(stream, member.size)
            policy.require({key: actual[key] for key in ("size", "sha256")} == files[member.name],
                           "Retained bytes differ from native destination")
    return {"file": target.name, "size": facts["size"], "sha256": facts["sha256"], "files": len(files)}


def promote(job, selected, env):
    policy.require(not job.producer.with_name("tooling").exists()
                   and policy.json_data(policy.read_small(job.root / "diagnostics/tooling-complete.json")) ==
                   {"task": "help", "scratch_absent": True}, "Separate tooling preparation required")
    toolchain(job, selected, env)
    profile(job, env)
    bundle = common.stage_raw(job, {"producer": selected}, job.root / policy.ARCHIVE)
    run_gradle(job, env, promotion_command(job, selected, bundle), job.producer, "promotion")
    profile(job, env)
    after = policy.source_snapshot(job.producer, selected["source_sha"], fresh=False)
    policy.require(after == policy.json_data(policy.read_small(job.root / "diagnostics/source-before.json")),
                   "Source changed during fresh promotion")
    policy.require(metadata(job, selected, fresh=False) == bundle, "Original source/archive changed during promotion")
    write_json(job.root / "diagnostics/source-after.json", after)
    files = tree_hashes(job.producer / "build/publication-bytes/owned-maven")
    write_json(job.root / "diagnostics/owned-maven.json", files)
    retained = archive_owned_maven(job, files)
    restored = {}
    for line in policy.text_lines(bundle["model.tsv"]):
        row = line.split("\t")
        if row[0] == "output":
            path = policy.regular(job.producer / policy.relative_path(row[10]))
            with path.open("rb") as stream:
                facts = policy.file_facts(stream, path.stat().st_size)
            restored[row[10]] = {key: facts[key] for key in ("size", "sha256")}
    write_json(job.root / "diagnostics/restored-outputs.json", restored)
    write_json(job.root / "diagnostics/result.json", {"scope": "actual-native-owned-file-only", "native_exit_code": 0,
               "producer": selected, "owned_maven": retained,
               "notice": "Not remote publication, release approval, reservation or consumer parity."})


def finalize(job, env):
    job.check()
    policy.require(not (job.root / "diagnostics").is_symlink(), "Redirected diagnostics")
    (job.root / "diagnostics").mkdir(mode=0o700, exist_ok=True)
    stop_owned(job, env, "final")
    if (job.root / "bootstrap-started.json").exists():
        helper = bootstrap(job)
        helper.cleanup(env)
        receipt = job.root.parent / helper.PROVENANCE
        if receipt.exists():
            data = policy.read_small(common.private_file(receipt))
            value = policy.json_data(data)
            policy.require(value["owner"] == helper.owner(helper.owned_root(env), env) and value["cleanup"] == "complete",
                           "Unowned bootstrap provenance")
            common.write_private(job.root / "diagnostics/libffi-provenance.json", data)
            receipt.unlink()
    owned = job.root.parent / "kira-libffi-source"
    policy.require(not owned.exists() and not owned.is_symlink(), "Bootstrap remains")
    write_json(job.root / "diagnostics/cleanup-ready.json", {"owned_gradle_stop": "complete", "bootstrap_absent": True})


def dispose(job):
    job.check()
    # Never erase state under an unconfirmed worker. Finalize must succeed first.
    policy.require(policy.json_data(policy.read_small(job.root / "diagnostics/cleanup-ready.json")) ==
                   {"owned_gradle_stop": "complete", "bootstrap_absent": True}, "Worker cleanup was not confirmed")
    policy.require(shutil.rmtree.avoids_symlink_attacks, "Safe owned tree removal unavailable")
    for path in (job.producer.with_name("tooling"), job.producer, job.root):
        policy.require(not path.is_symlink(), "Redirected disposal root")
        if path.exists():
            shutil.rmtree(path)
        policy.require(not path.exists() and not path.is_symlink(), "Owned output disposal incomplete")


def main(argv=None):
    try:
        args = sys.argv[1:] if argv is None else argv
        policy.require(len(args) == 1 and args[0] in {"prepare", "intake", "toolchain", "bootstrap", "warmup", "promote", "finalize", "dispose"},
                       "Owned-file command refused")
        env = dict(os.environ)
        token = env.pop("KIRA_INTAKE_READ_TOKEN", None)
        policy.require(not any(key in env for key in ("KIRA_MAVEN_WRITE_USERNAME", "KIRA_MAVEN_WRITE_TOKEN")), "Write custody forbidden")
        job = runtime(env)
        os.umask(0o077)
        os.environ.clear()
        os.environ.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", HOME=str(job.root / "home"), TMPDIR=str(job.root / "tmp"), LANG="C", LC_ALL="C")
        signal.signal(signal.SIGTERM, lambda *_: policy.require(False, "Interrupted"))
        if args[0] == "prepare":
            output = prepare(job, policy.json_data(env.get("KIRA_PRODUCER_SELECTION", "").encode()), env)
            with Path(env["GITHUB_OUTPUT"]).open("a") as stream:
                for key, value in output.items():
                    policy.require(not any(c in str(value) for c in "\r\n\x00"), "Invalid workflow output")
                    if key != "root":  # The owner path was emitted immediately after exclusive creation.
                        stream.write(f"{key}={value}\n")
            with Path(env["GITHUB_ENV"]).open("a") as stream:
                stream.write(f"GRADLE_USER_HOME={job.root}/gradle\nTMPDIR={job.root}/tmp\n")
        elif args[0] == "finalize":
            finalize(job, env)
        elif args[0] == "dispose":
            dispose(job)
        else:
            selected = load_selection(job, env.get("KIRA_SELECTION_SHA256", ""))
            if args[0] == "intake":
                acquire(job, selected, token)
            elif args[0] == "toolchain":
                toolchain(job, selected, env)
            elif args[0] == "bootstrap":
                metadata(job, selected)
                write_json(job.root / "bootstrap-started.json", {"source_sha": selected["source_sha"]})
                bootstrap(job).prepare(env, job.producer)
            else:
                {"warmup": warmup, "promote": promote}[args[0]](job, selected, env)
        print("Owned-file phase completed; no remote package writes or release authority.")
        return 0
    except Exception:
        print("Owned-file qualification refused; inspect bounded diagnostics. No remote package authority.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
