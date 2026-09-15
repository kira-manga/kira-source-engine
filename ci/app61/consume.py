#!/usr/bin/env python3
"""App61: configuration-only strict positive and same-cache checksum negative; prior bundle is carried."""
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    require(path.is_file() and not path.is_symlink(), f"Not a regular input: {path}")
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            value.update(block)
    return value.hexdigest()


def save(name, value):
    data = (json.dumps(value, indent=2) + "\n").encode()
    require(len(data) <= 1048576, "Bounded receipt required")
    with (reports / name).open("w") as output:
        output.write(data.decode())
        output.flush()
        os.fsync(output.fileno())


os.umask(0o077)
require(sys.argv[1:] == ["--consume-nonshipping"], "Strict nonshipping consumption only")
require(os.environ.get("GITHUB_ACTIONS") == "true", "Hosted, reviewed carrier only")
require(os.environ.get("GITHUB_REPOSITORY") == "kira-manga/kira-source-engine", "Wrong repository")
require(os.environ.get("GITHUB_EVENT_NAME") == "push" and os.environ.get("RUNNER_OS") == "Linux"
        and os.environ.get("RUNNER_ARCH") == "X64" and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
        "Only one hosted Linux push attempt")
require(os.environ.get("GITHUB_REF") == "refs/heads/validation/app61-native-negative-20260915-03", "Wrong branch")
require(os.environ.get("GITHUB_RUN_ATTEMPT") == "1", "No automatic reruns")
run_id = os.environ["GITHUB_RUN_ID"]
require(re.fullmatch(r"[0-9]+", run_id), "Invalid run identity")
control = Path(__file__).resolve().parents[2]
workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
require(control == workspace / "control", "Unexpected carrier checkout")
binding = json.loads((control / "ci/app61/binding.json").read_text())
require(binding["schema"] == "app61-strict-consume-v1" and binding["status"] == "PRIMARY_BOUND_FOR_CONSUMPTION"
        and binding["authorization"] == "APP61_CONFIGURATION_CHECKSUM_NEGATIVE_AUTHORIZED",
        "Source preparation is not execution authorization")
require(binding["app_public_commit"] == "42e612b1d354dc2b46e5f90cac6c3795859dcdb8"
        and binding["app_public_tree"] == "32f542e1bd59996e07693e178c1f8079d5dd9719",
        "Only the exact reviewed native-input candidate")
# Locate setup-ruby's executable BEFORE sanitizing PATH; never fall back to /usr/bin/ruby.
ruby_candidate = shutil.which("ruby", path=os.environ.get("PATH", ""))
token = user = ""  # Read the explicit credential only immediately before positive Gradle.
reports = workspace / "reports"
reports.mkdir()
result = {"status": "NOT_RUN", "nonshipping": True, "errors": [], "commands": [],
          "source": binding["app_public_commit"], "engine_base": binding["engine_base"],
          "gradle_exit": None, "negative_exit": None, "native_files": {}, "published_inputs": [],
          "scope": ["help"], "negative_scope": ["help", "--offline"], "batches": [],
          "forced_cleanup": False, "cleanup_signals": [], "cleanup_barrier_absent": False, "started": time.time()}
run = Path(os.environ["RUNNER_TEMP"]).resolve() / ("kira-app61-consume-" + run_id + "-1")
app, home, gradle_home, tmp = (run / name for name in ("app", "home", "gradle", "tmp"))
created = False
before = None
expected = {}
last_batch_settled = False
native_paths = []
placeholder = None
placeholder_bytes = None
env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TZ": "UTC"}
marker = "-Dkira.app61.consume=" + str(run)


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


def inspect_bundle():
    bundles = list((app / "app/build/outputs/bundle/release").glob("*.aab"))
    require(len(bundles) == 1, "One actual release AAB required")
    bundle = bundles[0]
    require(bundle.resolve() == bundle and 0 < bundle.stat().st_size <= 1073741824, "Invalid/oversized AAB")
    original = digest(bundle)
    with zipfile.ZipFile(bundle) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        require(0 < len(names) <= 20000 and len(set(names)) == len(names)
                and all(not Path(name).is_absolute() and ".." not in Path(name).parts for name in names)
                and sum(entry.file_size for entry in entries) <= 2147483648, "Invalid/unbounded AAB ZIP")
        require({"BundleConfig.pb", "base/manifest/AndroidManifest.xml", "base/dex/classes.dex"} <= set(names),
                "Release bundle lacks real manifest/dex/config payloads")
        require(not any(re.fullmatch(r"META-INF/[^/]+\.(?:SF|RSA|DSA|EC)", name, re.I) for name in names),
                "AAB must be unsigned")
        require(archive.testzip() is None, "AAB ZIP CRC validation failed")
    require(digest(bundle) == original, "AAB changed during inspection")
    result["bundle"] = {"path": str(bundle.relative_to(app)), "bytes": bundle.stat().st_size,
        "sha256": original, "zip_entries": len(names), "zip_crc_checked": True, "unsigned": True, "retained": False}
    save("bundle.json", result["bundle"])


def alter_consumed_agp():
    require(result["gradle_exit"] == 0 and result["cleanup_barrier_absent"] and not owned_children(),
            "No cache mutation before successful positive batch and owned absence")
    metadata = app / "gradle/verification-metadata.xml"
    root = ET.parse(metadata).getroot()
    components = [node for node in root.findall(".//{*}component") if node.attrib ==
                  {"group": "com.android.tools.build", "name": "gradle", "version": "9.2.1"}]
    require(len(components) == 1, "One source-bound AGP component required")
    artifacts = [node for node in components[0].findall("{*}artifact") if node.get("name") == "gradle-9.2.1.jar"]
    require(len(artifacts) == 1, "One source-bound AGP artifact required")
    checksums = [node.get("value") for node in artifacts[0].findall("{*}sha256")]
    require(checksums == [binding["negative_agp_sha256"]], "Unexpected AGP checksum authority")
    matches = list((gradle_home / "caches/modules-2/files-2.1/com.android.tools.build/gradle/9.2.1").glob("*/gradle-9.2.1.jar"))
    require(len(matches) == 1, "One freshly resolved AGP JAR required")
    artifact = matches[0]
    info = artifact.lstat()
    require(artifact.resolve() == artifact and stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and info.st_uid == os.geteuid() and 0 < info.st_size <= 134217728
            and digest(artifact) == checksums[0], "Fresh AGP cache file does not match original source XML")
    proof = {"artifact": str(artifact.relative_to(gradle_home)), "coordinate": "com.android.tools.build:gradle:9.2.1",
        "metadata_sha256": digest(metadata), "expected_sha256": checksums[0], "original_sha256": digest(artifact),
        "original_bytes": info.st_size, "device": info.st_dev, "inode": info.st_ino, "original_mtime_ns": info.st_mtime_ns,
        "original_mode": stat.S_IMODE(info.st_mode),
        "fresh_owned_cache": True, "positive_configuration_completed": True, "changed": False}
    save("negative-artifact.json", proof)  # Persist the original XML/cache authority BEFORE alteration.
    # Change only the ZIP comment: still a structurally valid JAR, never a corrupt-ZIP substitute
    # for real dependency-verification failure. Size+mtime change invalidates file-hash reuse.
    os.chmod(artifact, stat.S_IMODE(info.st_mode) | stat.S_IWUSR)  # Only this proven-owned disposable file.
    with zipfile.ZipFile(artifact, "a") as archive:
        require(len(archive.comment) < 60000, "Unexpected AGP ZIP comment")
        archive.comment += ("\nKIRA_APP61_CHECKSUM_NEGATIVE_" + run_id).encode()
    os.utime(artifact, ns=(info.st_atime_ns, max(time.time_ns(), info.st_mtime_ns + 1000000000)))
    after = artifact.lstat()
    require((after.st_dev, after.st_ino, after.st_uid, after.st_nlink) ==
            (info.st_dev, info.st_ino, info.st_uid, 1) and after.st_size > info.st_size
            and after.st_mtime_ns > info.st_mtime_ns, "AGP cache identity/size/mtime did not change as bound")
    with zipfile.ZipFile(artifact) as archive:
        require(archive.testzip() is None, "Altered AGP JAR is not a valid ZIP")
    proof.update(changed=True, altered_sha256=digest(artifact), altered_bytes=after.st_size, altered_mtime_ns=after.st_mtime_ns)
    require(proof["altered_sha256"] != proof["expected_sha256"], "AGP checksum negative changed no bytes")
    save("negative-artifact.json", proof)
    return artifact, proof


def verify_native_negative(artifact, proof):
    log = reports / "checksum-negative.log"
    require(log.stat().st_size <= 16777216, "Oversized native negative diagnostic")
    text = log.read_text(errors="replace")
    label = "gradle-9.2.1.jar (com.android.tools.build:gradle:9.2.1)"
    position = text.find(label)
    block = text[max(0, position - 512):position + 8192] if position >= 0 else ""
    qualified = (result["negative_exit"] not in (None, 0, 124)
        and "Dependency verification failed for configuration" in text
        and position >= 0 and "checksum" in block.lower()
        and proof["expected_sha256"] in block and proof["altered_sha256"] in block
        and digest(artifact) == proof["altered_sha256"])
    result["checksum_negative_qualified"] = qualified
    save("negative-proof.json", {"qualified": qualified, "exit": result["negative_exit"], "artifact": label,
        "source_xml_expected_sha256": proof["expected_sha256"], "altered_sha256": proof["altered_sha256"],
        "same_owned_cache": True, "offline": True, "package_token_forwarded": False,
        "criterion": "Native verification diagnostic must name exact artifact and expected/actual SHA256; exit1 or corrupt-ZIP is insufficient"})
    require(qualified, "No genuine artifact-specific Gradle checksum failure; stale verification or unrelated failure is not PASS")


def settle_batch(label):
    # Immediate stop uses only the locally installed wrapper distribution, never another download.
    launchers = list(gradle_home.glob("wrapper/dists/gradle-9.6.1-bin/*/gradle-9.6.1/bin/gradle")) if created else []
    batch = {"label": label, "stop_exit": None, "forced_cleanup": False, "barrier_absent": False}
    result["batches"].append(batch)
    result["cleanup_barrier_absent"] = False
    try:
        require(len(launchers) <= 1, "Ambiguous owned Gradle launcher")
        require(label == "final" or len(launchers) == 1, "Completed Gradle batch lacks its owned stop launcher")
        result["stop_exit"] = command([str(launchers[0]), "--stop", "--offline", "--console=plain"],
                                      label + "-stop", app, 90) if launchers else "NOT_INSTALLED"
        batch["stop_exit"] = result["stop_exit"]
        require(result["stop_exit"] in (0, "NOT_INSTALLED"), "Native daemon stop failed")
    except BaseException as error:
        result["errors"].append(label + " stop: " + str(error))
    try:
        for _ in range(20):
            survivors = owned_children()
            if not survivors:
                break
            time.sleep(0.25)
        if survivors:
            result["forced_cleanup"] = batch["forced_cleanup"] = True
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
        result["cleanup_barrier_absent"] = batch["barrier_absent"] = True
    except BaseException as error:
        result["errors"].append(label + " stop/barrier: " + str(error))
        survivors = ["UNKNOWN"]
        result["owned_children_after_cleanup"] = survivors
    return batch["barrier_absent"] and batch["stop_exit"] in (0, "NOT_INSTALLED") and not batch["forced_cleanup"]


signal.signal(signal.SIGTERM, terminate)
signal.signal(signal.SIGINT, terminate)
try:
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
    changed = subprocess.check_output(["git", "diff-tree", "--no-commit-id", "--name-status", "--no-renames", "-r", "HEAD^", "HEAD"],
                                      cwd=control, env=env).decode().splitlines()
    require(sorted(changed) == sorted("A\t" + path for path in
            ("ci/app61/consume.py", "ci/app61/binding.json", ".github/workflows/app61-native-consume.yml")),
            "Public carrier must contain only the three reviewed controls, never a private archive")
    require(digest(control / "ci/app61/consume.py") == binding["runner_sha256"]
            and digest(control / ".github/workflows/app61-native-consume.yml") == binding["workflow_sha256"],
            "Changed consume controls")
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
    require(digest(app / "release/verified-tools.json") == binding["manifest_sha256"], "Pinned native-input manifest changed")
    manifest = json.loads((app / "release/verified-tools.json").read_text())
    graph = manifest["gradle"]["dependency_graph"]
    require(graph["files"] == binding["public_gradle_inputs"] and len(graph["files"]) == 20
            and len(graph["lockfiles"]) == 33 and graph["metadata_sha256"] == binding["native_inputs"]["gradle/verification-metadata.xml"],
            "Expected the reviewed twenty graph inputs, original03 XML and original04 thirty-three locks")
    native_inputs = dict(graph["lockfiles"], **{"gradle/verification-metadata.xml": graph["metadata_sha256"]})
    require(native_inputs == binding["native_inputs"] and len(native_inputs) == 34, "Native input binding differs")
    expected = dict(graph["files"], **manifest["gradle"]["files"])
    expected.update(binding["source_inputs"])
    expected.update(native_inputs)
    before = source_state()
    require(before["files"] == expected and before["delta_sha256"] == hashlib.sha256(b"").hexdigest(),
            "Source-native input mismatch or source overlay; no rewrite is allowed")
    (reports / "source-before.json").write_text(json.dumps(before, indent=2) + "\n")
    (reports / "binding.json").write_bytes((control / "ci/app61/binding.json").read_bytes())
    native_paths = [Path(path) for path in sorted(native_inputs)]
    toolcache = Path(os.environ["RUNNER_TOOL_CACHE"]).resolve()
    require(toolcache == Path("/opt/hostedtoolcache") and ruby_candidate, "Pinned setup-ruby runtime not located")
    ruby = Path(ruby_candidate).resolve()
    require(ruby == toolcache / "Ruby/3.3.12/x64/bin/ruby" and ruby.is_file() and not ruby.is_symlink(),
            "Only setup-ruby's exact hosted Ruby3.3.12 executable is allowed")
    require(command([str(ruby), "-rjson", "-rrbconfig", "-e",
        'puts JSON.generate({version: RUBY_VERSION, engine: RUBY_ENGINE, executable: RbConfig.ruby, arch: RbConfig::CONFIG["arch"]})'],
        "ruby-runtime", app, 30) == 0, "Pinned Ruby runtime failed")
    ruby_info = json.loads((reports / "ruby-runtime.log").read_text())
    require(ruby_info["version"] == "3.3.12" and ruby_info["engine"] == "ruby"
            and Path(ruby_info["executable"]).resolve() == ruby and ruby_info["arch"].startswith("x86_64-linux"),
            "Ruby runtime identity mismatch")
    result["ruby"] = dict(ruby_info, executable_sha256=digest(ruby), archive_byte_authority="NOT_ESTABLISHED")
    require(command([str(ruby), "scripts/release/verify-toolchain-inputs.rb"], "verify-source-inputs", app, 60) == 0,
            "Actual source-native toolchain input verifier failed")
    require((reports / "verify-source-inputs.log").read_text().strip() ==
            "Pinned Action, wrapper, Gradle metadata/locks, XcodeGen metadata, and pre-credential ordering checks passed",
            "Unexpected real input-verifier completion")
    result["toolchain_inputs_verified"] = True
    require(command([str(java_home / "bin/java"), "-XshowSettings:properties", "-version"], "java", app) == 0,
            "Selected Java failed")
    java_text = (reports / "java.log").read_text()
    require(re.search(r"java.vendor = Eclipse Adoptium\s*$", java_text, re.M) and
            re.search(r"java.runtime.version = 21\.0\.12\.1\+1(?:-LTS)?\s*$", java_text, re.M),
            "Launcher is not the scaffold-selected Temurin21.0.12.1+1")
    require((sdk / "platforms/android-37.0/android.jar").is_file(), "Installed stable Android SDK37.0 path is missing; no auto-install")
    result["runtime"] = {"java_home": str(java_home), "sdk": str(sdk),
                         "image_version": os.environ.get("ImageVersion"),
                         "sdk37_properties_sha256": digest(sdk / "platforms/android-37.0/source.properties"),
                         "installed_byte_authority": "NOT_ESTABLISHED; JDK17/Native/SDK/image gaps remain"}
    slot = app / "app/google-services.json"
    require(not slot.exists() and not slot.is_symlink(), "Firebase slot must be absent and nonsymlink")
    apple_slot = app / "iosApp/iosApp/GoogleService-Info.plist"
    require(not apple_slot.exists() and not apple_slot.is_symlink(), "Store Apple Firebase input must be absent")
    placeholder_bytes = (app / "app/google-services.json.example").read_bytes()
    with slot.open("xb") as output:
        placeholder = slot
        output.write(placeholder_bytes)
    arguments = ["--dependency-verification=strict", "--no-daemon", "--no-parallel",
            "--max-workers=2", "--no-build-cache", "--no-configuration-cache", "--console=plain", "--info", "--stacktrace",
            "-Pkotlin.compiler.execution.strategy=in-process", "-PkiraUseMavenLocal=false",
            "-Porg.gradle.java.installations.auto-download=false", "-Pandroid.builder.sdkDownload=false",
            "-PallowPlaceholderGoogleServices=true", "-PallowUnconfiguredSourceRemote=true",
            "-Dorg.gradle.vfs.watch=false", "-Dorg.gradle.jvmargs=-Xmx6g -XX:MaxMetaspaceSize=1g " + marker,
            "--project-cache-dir", str(run / "project-cache")]
    upload_guard = run / "no-upload.init.gradle"
    upload_guard.write_text('''// Refusal only: never changes source, compilation, resolution, R8 or task actions.
gradle.taskGraph.whenReady { graph ->
    // These exact KMP lint preparation tasks only prepare local jars. They were
    // the fourteen false positives in run35012695937; no publishing task is exempt.
    def localLintPreparation = [
        ':composeApp:prepareLintJarForPublish',
        ':core:prepareLintJarForPublish',
        ':data:prepareLintJarForPublish',
        ':domain:prepareLintJarForPublish',
        ':platform:prepareLintJarForPublish',
        ':presentation:prepareLintJarForPublish',
        ':ui:prepareLintJarForPublish',
        ':data:download:prepareLintJarForPublish',
        ':data:local:prepareLintJarForPublish',
        ':data:remote:prepareLintJarForPublish',
        ':sources:config:prepareLintJarForPublish',
        ':sources:contracts:prepareLintJarForPublish',
        ':sources:engine:prepareLintJarForPublish',
        ':sources:legacy:prepareLintJarForPublish'
    ] as Set
    def forbidden = graph.allTasks.findAll { task ->
        (task.name.toLowerCase(java.util.Locale.ROOT) =~ /upload|publish/) &&
            !localLintPreparation.contains(task.path)
    }
    if (!forbidden.isEmpty()) {
        throw new GradleException("App61 NONSHIPPING forbids upload/publication tasks: " + forbidden*.path.join(', '))
    }
}
''')
    result["upload_guard_sha256"] = digest(upload_guard)
    # Read and forward the explicit package credential ONLY for this positive invocation.
    token = os.environ.pop("KIRA_PACKAGES_READ_TOKEN", "")
    user = os.environ.pop("KIRA_PACKAGES_USER", "")
    require(token and user, "Explicit read-only positive-batch workflow credential is missing")
    consume_env = dict(env, KIRA_PACKAGES_USER=user, KIRA_PACKAGES_READ_TOKEN=token)
    last_batch_settled = False
    try:
        # Configuration-only consumption populates a fresh owned cache; the prior bundle PASS is not rerun.
        result["gradle_exit"] = command(["./gradlew", *result["scope"], *arguments,
            "-I", str(upload_guard)],
            "strict-configuration", app, 12 * 60, consume_env)
    finally:
        consume_env.clear()
        positive_settled = settle_batch("positive")
        last_batch_settled = True
    require(result["gradle_exit"] == 0 and positive_settled and not result["errors"], "Strict positive configuration or its cleanup failed")
    require(source_state() == before, "Positive consumption changed source/metadata/locks")
    artifact, negative_proof = alter_consumed_agp()
    # One configuration-only native verification negative; same cache, no package credential.
    last_batch_settled = False
    try:
        result["negative_exit"] = command(["./gradlew", *result["negative_scope"], *arguments,
            "-Porg.gradle.dependency.verification.console=verbose"], "checksum-negative", app, 180)
    finally:
        negative_settled = settle_batch("negative")
        last_batch_settled = True
    require(negative_settled and not result["errors"], "Negative-batch cleanup failed")
    verify_native_negative(artifact, negative_proof)
except BaseException as error:
    result["errors"].append(type(error).__name__ + ": " + str(error))
finally:
    if not last_batch_settled:
        last_batch_settled = settle_batch("final")
    survivors = result.get("owned_children_after_cleanup", ["UNKNOWN"])
    try:
        # These are committed inputs, not newly emitted authority. Retain hashes only.
        result["native_snapshot_quiescent"] = not survivors
        for relative in native_paths if before is not None else []:
            source = app / relative
            if source.exists():
                sha = digest(source)
                result["native_files"][str(relative)] = {"bytes": source.stat().st_size, "sha256": sha}
                require(sha == binding["native_inputs"][str(relative)], "Committed native input changed")
        group = gradle_home / "caches/modules-2/files-2.1/me.manga.kira.source"
        for source in sorted(group.glob("*/0.1.0/*/*")):
            sha = digest(source)
            relative = source.relative_to(group)
            result["published_inputs"].append({"path": str(relative), "bytes": source.stat().st_size, "sha256": sha})
            accepted = binding["accepted_published_jvm"].get(source.name)
            if accepted is not None and (sha != accepted["sha256"] or source.stat().st_size != accepted["bytes"]):
                result["errors"].append("Existing published JVM byte authority differs: " + source.name)
        result["native_inputs_unchanged"] = len(result["native_files"]) == 34 and all(
            result["native_files"][path]["sha256"] == sha for path, sha in binding["native_inputs"].items())
        if result["gradle_exit"] == 0 and not result["native_inputs_unchanged"]:
            result["errors"].append("Strict consumption lost or changed committed native input bytes")
    except BaseException as error:
        result["errors"].append("input readback: " + str(error))
    source_safe = before is None
    try:
        if before is not None:
            after = source_state()
            (reports / "source-after.json").write_text(json.dumps(after, indent=2) + "\n")
            result["source_unchanged"] = before == after
            require(result["source_unchanged"], "Source changed during consumption; preserve run directory")
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
            if path.is_symlink() or path.stat().st_size > 16777216:
                path.unlink()
                result["errors"].append("Omitted nonregular/oversized diagnostic: " + str(path.relative_to(reports)))
                continue
            data = path.read_bytes()
            if any(needle in data for needle in needles):
                path.unlink()
                result["errors"].append("Omitted credential-bearing output: " + str(path.relative_to(reports)))
    passed = (result["gradle_exit"] == 0 and result.get("checksum_negative_qualified") is True
        and result.get("toolchain_inputs_verified") is True and result["scope"] == ["help"]
        and result.get("native_inputs_unchanged") is True and result.get("source_unchanged") is True
        and result.get("owned_run_removed") is True and not result["errors"]
        and [batch["label"] for batch in result["batches"]] == ["positive", "negative"])
    result["status"] = "CONFIGURATION_CHECKSUM_REVIEW_REQUIRED" if passed else "PARTIAL_OR_NOT_RUN"
    if result["forced_cleanup"] or result.get("stop_exit") not in (0, "NOT_INSTALLED") or not result["cleanup_barrier_absent"]:
        result["status"] = "FAILED_CLEANUP_UNREVIEWED_OUTPUT"
    result["coverage"] = ("One source-native verifier, strict configuration-only positive and same-cache offline help checksum negative. Prior unsigned bundle run35013910271 is carried, not rerun. "
        "No tests or standalone lint replay, Apple strict consumption, full matrix, publisher trust, installed-runtime byte authority, signing or shipping claim")
    result["ended"] = time.time()
    (reports / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write("cleanup_barrier_absent=" + str(result["cleanup_barrier_absent"]).lower() + "\n")
    print(result["status"] + "; Gradle exit=" + str(result["gradle_exit"]) + "; see reports/result.json")
    if not result["cleanup_barrier_absent"]:
        print("Owned cleanup barrier not established; report upload is withheld.")

sys.exit(result["gradle_exit"] if result["gradle_exit"] not in (None, 0) else
         (0 if result["status"] == "CONFIGURATION_CHECKSUM_REVIEW_REQUIRED" else 1))
