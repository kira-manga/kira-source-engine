#!/usr/bin/env python3
"""One NONSHIPPING App61 native capture. No release, substitution, or trust adoption."""
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    require(path.is_file() and not path.is_symlink(), f"Not a regular input: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


os.umask(0o077)
require(sys.argv[1:] == ["--capture-nonshipping"], "Capture-only invocation required")
require(os.environ.get("GITHUB_ACTIONS") == "true", "Hosted, reviewed carrier only")
require(os.environ.get("GITHUB_REPOSITORY") == "kira-manga/kira-source-engine", "Wrong repository")
require(os.environ.get("GITHUB_REF") == "refs/heads/validation/app61-native-capture-20260915-02", "Wrong branch")
require(os.environ.get("GITHUB_RUN_ATTEMPT") == "1", "No automatic reruns")
run_id = os.environ["GITHUB_RUN_ID"]
require(re.fullmatch(r"[0-9]+", run_id), "Invalid run identity")
control = Path(__file__).resolve().parents[2]
workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
require(control == workspace / "control", "Unexpected carrier checkout")
binding = json.loads((control / "ci/app61/binding.json").read_text())
# Hold only the explicitly supplied job credential; never forward the runner environment.
token = os.environ.pop("KIRA_PACKAGES_READ_TOKEN", "")
user = os.environ.pop("KIRA_PACKAGES_USER", "")
reports = workspace / "reports"
reports.mkdir()
result = {"status": "NOT_RUN", "nonshipping": True, "errors": [], "commands": [],
          "source": binding["app_public_commit"], "engine_base": binding["engine_base"],
          "gradle_exit": None, "native_files": {}, "published_inputs": [],
          "scope": [":app:bundleRelease", ":app:lintRelease"], "started": time.time()}
run = Path(os.environ["RUNNER_TEMP"]).resolve() / ("kira-app61-" + run_id + "-1")
app, home, gradle_home, tmp = (run / name for name in ("app", "home", "gradle", "tmp"))
created = False
before = None
native_paths = []
placeholder = None
placeholder_bytes = None
env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TZ": "UTC"}
marker = "-Dkira.app61.capture=" + str(run)


def command(argv, name, cwd, seconds=180, environment=None):
    receipt = {"argv": argv, "log": name + ".log", "exit": None}
    result["commands"].append(receipt)
    with (reports / (name + ".log")).open("xb") as output:
        process = subprocess.Popen(argv, cwd=cwd, env=env if environment is None else environment,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        process_fd = os.pidfd_open(process.pid)
        try:
            code = process.wait(timeout=seconds)
        except BaseException as error:
            try:
                signal.pidfd_send_signal(process_fd, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    signal.pidfd_send_signal(process_fd, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            receipt["process_exit"] = process.returncode
            if not isinstance(error, subprocess.TimeoutExpired):
                receipt["interrupted"] = True
                raise
            receipt["timed_out"] = True
            code = 124
        finally:
            os.close(process_fd)
    receipt["exit"] = code
    return code


def git(*args):
    return subprocess.check_output(["git", *args], cwd=app, env=env)


def source_state():
    return {"delta_sha256": hashlib.sha256(git("diff", "--binary", "--full-index", "HEAD")).hexdigest(),
            "files": {p: digest(app / p) for p in expected}}


def terminate(_number, _frame):
    raise KeyboardInterrupt("Hosted capture interrupted")


def child_identity(pid):
    # Linux stat fields after comm: state, ppid, ... starttime (field22).
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return int(fields[1]), int(fields[19])


def owned_children():
    while True:
        try:
            if os.waitpid(-1, os.WNOHANG)[0] == 0:
                break
        except ChildProcessError:
            break
    children = {}
    # PR_SET_CHILD_SUBREAPER makes detached descendants our children as their
    # parents exit. No historical/reaped process-group id grants kill authority.
    for value in Path(f"/proc/self/task/{os.getpid()}/children").read_text().split():
        pid = int(value)
        try:
            parent, started = child_identity(pid)
            if parent == os.getpid():
                children[pid] = started
        except FileNotFoundError:
            pass
    return children


signal.signal(signal.SIGTERM, terminate)
signal.signal(signal.SIGINT, terminate)
try:
    require(token and user, "Explicit read-only workflow credential is missing")
    require(hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"), "Linux pidfd signalling is required")
    # Same Linux subreaper ownership used by the existing lane: detached Gradle
    # daemons/workers become our children, rather than disappearing behind setsid.
    require(ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0, "Cannot own detached children")
    run.mkdir()
    created = True
    owned_identity = (run.stat().st_dev, run.stat().st_ino)
    for directory in (app, home, gradle_home, tmp, run / "konan"):
        directory.mkdir()
    java_home = Path(os.environ["JAVA_HOME"]).resolve()
    sdk = Path(os.environ["ANDROID_HOME"]).resolve()
    env.update(PATH=str(java_home / "bin") + ":/usr/bin:/bin", JAVA_HOME=str(java_home),
               HOME=str(home), GRADLE_USER_HOME=str(gradle_home), KONAN_DATA_DIR=str(run / "konan"),
               TMPDIR=str(tmp), TMP=str(tmp), TEMP=str(tmp), ANDROID_HOME=str(sdk), ANDROID_SDK_ROOT=str(sdk),
               JAVA_TOOL_OPTIONS=f"-Duser.home={home} -Djava.io.tmpdir={tmp} -Djava.awt.headless=true",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_TERMINAL_PROMPT="0",
               KIRA_SOURCE_CONFIG_BASE_URL="", KIRA_SOURCE_CONFIG_PINNED_KEYS="", CI="true")
    require(shutil.disk_usage(run).free >= 8 * 1024**3, "Less than 8 GiB free for this owned capture")
    carrier_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=control, env=env).decode().strip()
    parent = subprocess.check_output(["git", "rev-parse", "HEAD^"], cwd=control, env=env).decode().strip()
    require(carrier_head == os.environ["GITHUB_SHA"] and parent == binding["engine_base"],
            "Carrier must be one reviewed commit on the admitted Engine integration")
    result["carrier"] = carrier_head
    # Anonymous public checkout: fresh HOME, no credential helper/header, no package token.
    for argv, name in [(["git", "init", "--quiet", str(app)], "git-init"),
                       (["git", "-c", "credential.helper=", "-c", "http.extraheader=", "fetch", "--no-tags",
                         "--depth=1", "https://github.com/kira-manga/kira-app.git", binding["app_public_commit"]], "git-fetch"),
                       (["git", "checkout", "--quiet", "--detach", "FETCH_HEAD"], "git-checkout")]:
        require(command(argv, name, app) == 0, name + " failed")
    require(git("rev-parse", "HEAD").decode().strip() == binding["app_public_commit"], "App commit mismatch")
    require(git("rev-parse", "HEAD^{tree}").decode().strip() == binding["app_public_tree"], "App tree mismatch")
    require(not git("status", "--porcelain"), "Public checkout is not clean")
    for path, sha in binding["public_gradle_inputs"].items():
        require(digest(app / path) == sha, "Public Gradle input mismatch: " + path)
    for path, item in binding["overlay"].items():
        source = control / "ci/app61/overlay" / path
        require(digest(source) == item["sha256"], "Overlay mismatch: " + path)
        require(digest(app / path) == item["public_sha256"], "Overlay preimage mismatch: " + path)
        shutil.copyfile(source, app / path)
    manifest = json.loads((app / "release/verified-tools.json").read_text())
    graph = manifest["gradle"]["dependency_graph"]
    require(len(graph["files"]) == 20 and graph["metadata_sha256"] is None and graph["lockfiles"] == {},
            "Expected the reviewed, still-unqualified twenty-input scaffold")
    expected = dict(graph["files"], **manifest["gradle"]["files"])
    expected.update({p: item["sha256"] for p, item in binding["overlay"].items()})
    before = source_state()
    require(before["files"] == expected, "Overlaid scaffold input mismatch")
    (reports / "source-before.json").write_text(json.dumps(before, indent=2) + "\n")
    (reports / "binding.json").write_bytes((control / "ci/app61/binding.json").read_bytes())
    modules = sorted({str(Path(p).parent) for p in graph["files"] if p.endswith("build.gradle.kts")})
    native_paths = [Path("gradle/verification-metadata.xml")] + [Path(m) / name for m in modules
                    for name in ("gradle.lockfile", "buildscript-gradle.lockfile")]
    require(all(not (app / p).exists() and not (app / p).is_symlink() for p in native_paths),
            "Native capture must start without supplied metadata/locks")
    require(command([str(java_home / "bin/java"), "-XshowSettings:properties", "-version"], "java", app) == 0,
            "Selected Java failed")
    java_text = (reports / "java.log").read_text()
    require(re.search(r"java.vendor = Eclipse Adoptium\s*$", java_text, re.M) and
            re.search(r"java.runtime.version = 21\.0\.12\.1\+1(?:-LTS)?\s*$", java_text, re.M),
            "Launcher is not the scaffold-selected Temurin21.0.12.1+1")
    require((sdk / "platforms/android-37/android.jar").is_file(), "Installed Android SDK37 is missing; no auto-install")
    result["runtime"] = {"java_home": str(java_home), "sdk": str(sdk),
                         "image_version": os.environ.get("ImageVersion"),
                         "sdk37_properties_sha256": digest(sdk / "platforms/android-37/source.properties"),
                         "installed_byte_authority": "NOT_ESTABLISHED; JDK17/Native/SDK/image gaps remain"}
    slot = app / "app/google-services.json"
    require(not slot.exists() and not slot.is_symlink(), "Firebase slot must be absent and nonsymlink")
    apple_slot = app / "iosApp/iosApp/GoogleService-Info.plist"
    require(not apple_slot.exists() and not apple_slot.is_symlink(), "Store Apple Firebase input must be absent")
    placeholder_bytes = (app / "app/google-services.json.example").read_bytes()
    with slot.open("xb") as output:
        placeholder = slot
        output.write(placeholder_bytes)
    argv = ["./gradlew", *result["scope"], "--dependency-verification=strict",
            "--write-verification-metadata", "sha256", "--write-locks", "--no-daemon", "--no-parallel",
            "--max-workers=2", "--no-build-cache", "--no-configuration-cache", "--console=plain", "--info", "--stacktrace",
            "-Pkotlin.compiler.execution.strategy=in-process", "-PkiraUseMavenLocal=false",
            "-Porg.gradle.java.installations.auto-download=false", "-Pandroid.builder.sdkDownload=false",
            "-PallowPlaceholderGoogleServices=true", "-PallowUnconfiguredSourceRemote=true",
            "-Dorg.gradle.vfs.watch=false", "-Dorg.gradle.jvmargs=-Xmx6g -XX:MaxMetaspaceSize=1g " + marker,
            "--project-cache-dir", str(run / "project-cache")]
    # This is the sole child receiving the token; stop, git, Java, collection and upload do not.
    capture_env = dict(env, KIRA_PACKAGES_USER=user, KIRA_PACKAGES_READ_TOKEN=token)
    try:
        result["gradle_exit"] = command(argv, "native-capture", app, 42 * 60, capture_env)
    finally:
        capture_env.clear()
except BaseException as error:
    result["errors"].append(type(error).__name__ + ": " + str(error))
finally:
    # Immediate stop uses only the locally installed wrapper distribution, never another download.
    launchers = list(gradle_home.glob("wrapper/dists/gradle-9.6.1-bin/*/gradle-9.6.1/bin/gradle")) if created else []
    result.update(forced_cleanup=False, cleanup_signals=[], cleanup_barrier_absent=False)
    try:
        require(len(launchers) <= 1, "Ambiguous owned Gradle launcher")
        result["stop_exit"] = command([str(launchers[0]), "--stop", "--offline", "--console=plain"],
                                      "stop-immediate", app, 90) if launchers else "NOT_INSTALLED"
        require(result["stop_exit"] in (0, "NOT_INSTALLED"), "Native daemon stop failed")
    except BaseException as error:
        result["errors"].append("stop: " + str(error))
    try:
        for _ in range(20):
            survivors = owned_children()
            if not survivors:
                break
            time.sleep(0.25)
        if survivors:
            result["forced_cleanup"] = True
            result["errors"].append("Owned-worker forced cleanup required; this attempt remains FAILED")
            for sig in (signal.SIGTERM, signal.SIGKILL):
                deadline, sent = time.monotonic() + 5, set()
                while survivors and time.monotonic() < deadline:
                    for pid, started in survivors.items():
                        if (pid, started) in sent:
                            continue
                        fd = None
                        try:
                            fd = os.pidfd_open(pid)
                            # Pin a live process handle, then recheck both birth and current
                            # kernel parent. A reused pid or old group is never signalled.
                            if child_identity(pid) == (os.getpid(), started):
                                signal.pidfd_send_signal(fd, sig)
                                result["cleanup_signals"].append({"pid": pid, "starttime": started, "signal": sig.name})
                            sent.add((pid, started))
                        except (ProcessLookupError, FileNotFoundError):
                            pass
                        finally:
                            if fd is not None:
                                os.close(fd)
                    time.sleep(0.1)
                    # Killing an owned ancestor adopts its detached children; the same
                    # bounded phase now stops those newly proven-owned children too.
                    survivors = owned_children()
        survivors = owned_children()
        result["owned_children_after_cleanup"] = survivors
        require(not survivors, "Owned processes remain; preserve run directory")
        result["cleanup_barrier_absent"] = True
    except BaseException as error:
        result["errors"].append("stop/barrier: " + str(error))
        survivors = ["UNKNOWN"]
    try:
        # Raw native output only. Failed execution never upgrades emitted XML/locks to authority.
        result["native_snapshot_quiescent"] = not survivors
        for relative in native_paths if before is not None else []:
            source = app / relative
            if source.exists():
                sha = digest(source)
                target = reports / "native" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                require(digest(target) == sha, "Native output changed while it was being retained")
                result["native_files"][str(relative)] = {"bytes": target.stat().st_size, "sha256": sha}
        group = gradle_home / "caches/modules-2/files-2.1/me.manga.kira.source"
        for source in sorted(group.glob("*/0.1.0/*/*")):
            sha = digest(source)
            relative = source.relative_to(group)
            result["published_inputs"].append({"path": str(relative), "bytes": source.stat().st_size, "sha256": sha})
            if source.suffix in (".pom", ".module"):
                target = reports / "engine-metadata" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            accepted = binding["accepted_published_jvm"].get(source.name)
            if accepted is not None and (sha != accepted["sha256"] or source.stat().st_size != accepted["bytes"]):
                result["errors"].append("Existing published JVM byte authority differs: " + source.name)
        result["not_emitted_native_paths"] = [str(p) for p in native_paths if str(p) not in result["native_files"]]
        result["root_buildscript_lock_present"] = "buildscript-gradle.lockfile" in result["native_files"]
        if result["gradle_exit"] == 0 and "gradle/verification-metadata.xml" not in result["native_files"]:
            result["errors"].append("Successful execution did not emit native verification metadata")
    except BaseException as error:
        result["errors"].append("raw capture: " + str(error))
    source_safe = before is None
    try:
        if before is not None:
            after = source_state()
            (reports / "source-after.json").write_text(json.dumps(after, indent=2) + "\n")
            result["source_unchanged"] = before == after
            require(result["source_unchanged"], "Source changed during capture; preserve run directory")
            source_safe = True
    except BaseException as error:
        result["errors"].append("source check: " + str(error))
    placeholder_safe = placeholder is None
    try:
        if placeholder is not None:
            require(not placeholder.is_symlink() and placeholder.is_file() and placeholder.read_bytes() == placeholder_bytes,
                    "Owned Firebase example changed; preserve it and fail")
            placeholder.unlink()
            result["placeholder_removed"] = not placeholder.exists() and not placeholder.is_symlink()
            placeholder_safe = result["placeholder_removed"]
    except BaseException as error:
        result["errors"].append("placeholder cleanup: " + str(error))
    try:
        if created and source_safe and placeholder_safe and result["cleanup_barrier_absent"]:
            require(not run.is_symlink() and (run.stat().st_dev, run.stat().st_ino) == owned_identity, "Owned root changed")
            shutil.rmtree(run)
            result["owned_run_removed"] = not run.exists() and not run.is_symlink()
    except BaseException as error:
        result["errors"].append("owned cleanup: " + str(error))
    # Artifact uploads are not automatically secret-masked. Never retain an accidental credential echo.
    needles = [token.encode(), base64.b64encode((user + ":" + token).encode())] if token else []
    for path in reports.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            if any(needle in data for needle in needles):
                path.unlink()
                result["errors"].append("Omitted credential-bearing output: " + str(path.relative_to(reports)))
    result["status"] = "UNREVIEWED_NATIVE_OUTPUT" if result["gradle_exit"] == 0 and not result["errors"] else "PARTIAL_OR_NOT_RUN"
    if result["forced_cleanup"] or result.get("stop_exit") not in (0, "NOT_INSTALLED") or not result["cleanup_barrier_absent"]:
        result["status"] = "FAILED_CLEANUP_UNREVIEWED_OUTPUT"
    result["coverage"] = "Only emitted files and the selected bundle/lint task log; no test, Apple, complete-lock, or strict-consumption claim"
    result["ended"] = time.time()
    (reports / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write("cleanup_barrier_absent=" + str(result["cleanup_barrier_absent"]).lower() + "\n")
    print(result["status"] + "; Gradle exit=" + str(result["gradle_exit"]) + "; see reports/result.json")
    if not result["cleanup_barrier_absent"]:
        print("Owned cleanup barrier not established; report upload is withheld.")

sys.exit(result["gradle_exit"] if result["gradle_exit"] not in (None, 0) else
         (0 if result["status"] == "UNREVIEWED_NATIVE_OUTPUT" and result.get("owned_run_removed") else 1))
