#!/usr/bin/env python3
"""Finite public archive/runtime probes; no Gradle, gems or application build."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import sys
import tempfile
import time


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def digest(path):
    require(path.is_file() and not path.is_symlink(), "Nonregular bound input")
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            value.update(block)
    return value.hexdigest()


def load(path, expected, name):
    require(digest(path) == expected, "Changed bound helper: " + path.name)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def identity(path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and path.resolve() == path, "Unowned/aliased directory")
    return [info.st_dev, info.st_ino, info.st_uid]


def main():
    require(sys.argv[1:] == ["--qualify-archives"], "Archive qualification only")
    require(os.environ.get("GITHUB_ACTIONS") == "true" and
            os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted" and
            os.environ.get("GITHUB_REPOSITORY") == "kira-manga/kira-source-engine" and
            os.environ.get("GITHUB_EVENT_NAME") == "push" and
            os.environ.get("GITHUB_RUN_ATTEMPT") == "1", "One reviewed hosted push attempt only")
    control = Path(__file__).resolve().parents[2]
    workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
    require(control == workspace / "control", "Wrong control checkout")
    binding_path = control / "ci/app61-runtime/binding.json"
    require(binding_path.stat().st_size <= 16384 and not binding_path.is_symlink(), "Invalid binding")
    binding = json.loads(binding_path.read_text())
    require(binding["schema"] == "app61-runtime-archive-layout-v1" and
            os.environ.get("GITHUB_REF") == "refs/heads/" + binding["branch"], "Wrong qualification branch")
    linux = os.environ.get("RUNNER_OS") == "Linux"
    owner = load(control / "ci/app61-runtime" / ("app8-linux-owner.py" if linux else "app8-owner.py"),
                 binding["linuxOwnerSha256" if linux else "ownerSha256"], "app61_owner")
    adapter = load(control / "ci/app61-runtime/app5-commands.py", binding["commandsSha256"], "app61_commands")
    owner.deadline_capabilities()
    app = workspace / "app"
    source = app / "scripts/release/install-toolchain-inputs.py"
    runtime = load(source, binding["candidateFiles"]["scripts/release/install-toolchain-inputs.py"], "app61_runtime")
    host = runtime.host()
    require((os.environ.get("RUNNER_OS"), os.environ.get("RUNNER_ARCH")) ==
            (("Linux", "X64") if host == "linux-x64" else ("macOS", "ARM64")), "Wrong hosted architecture")
    roles = ("java", "native", "ruby") if host == "macos-arm64" else ("java", "ruby")
    os.umask(0o077)
    temporary = Path(os.environ["RUNNER_TEMP"]).resolve()
    identity(temporary)
    public = workspace / "app61-runtime-reports"
    public.mkdir(mode=0o700)
    run = Path(tempfile.mkdtemp(prefix="app61-runtime-", dir=temporary))
    run_identity = identity(run)
    for child in ("reports", "work", "home", "inputs"):
        (run / child).mkdir(mode=0o700)
    for name in ("env", "path"):
        (run / name).touch(mode=0o600)
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(run / "home"),
           "TMPDIR": str(run / "inputs"), "RUNNER_TEMP": str(run / "inputs"),
           "RUNNER_TOOL_CACHE": os.environ["RUNNER_TOOL_CACHE"], "GITHUB_ACTIONS": "true",
           "RUNNER_ENVIRONMENT": "github-hosted", "GITHUB_ENV": str(run / "env"),
           "GITHUB_PATH": str(run / "path"), "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C", "LC_ALL": "C"}
    if host == "macos-arm64":
        env["DEVELOPER_DIR"] = "/Applications/Xcode_26.4.1.app/Contents/Developer"
    limits = binding["deadlinesSeconds"]
    started = time.monotonic()
    end, work_end = started + limits["controller"], started + limits["work"]
    commands = adapter.commands(owner, run, env, end)
    result = {"host": host, "issueCommit": binding["issueCommit"], "sourceFreezeSha256": binding["sourceFreezeSha256"],
              "roles": {}, "errors": [], "cleanup": {}, "scope": binding["scope"],
              "controllerPython": {"executable": sys.executable, "version": sys.version}}
    receipts = {}
    prefix = runtime.RUBY_PREFIXES[host]
    ruby_before = None
    allowed = {"JAVA_HOME", "KIRA_VERIFIED_RUBY_PREFIX", "KONAN_DATA_DIR", "KONAN_USE_INTERNAL_SERVER",
               "ORG_GRADLE_PROJECT_kotlin.native.home", "ORG_GRADLE_PROJECT_konan.data.dir"}
    allowed.update("KIRA_VERIFIED_" + role.upper() + "_AREA" for role in runtime.ROLES)

    def published():
        path = run / "env"
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 16384, "Invalid publication file")
        values = {}
        for line in path.read_text().splitlines():
            key, value = line.split("=", 1)
            require(key in allowed and "\0" not in value and "\r" not in value, "Unexpected runtime publication")
            values[key] = value
        return values

    def check_source(label):
        require(commands.call(["/usr/bin/git", "-C", app, "rev-parse", "HEAD"], label + "-commit", 15,
                              end=work_end).strip() == binding["issueCommit"], "Wrong exact issue commit")
        require(commands.call(["/usr/bin/git", "-C", app, "rev-parse", "HEAD^{tree}"], label + "-tree", 15,
                              end=work_end).strip() == binding["issueTree"], "Wrong issue tree")
        require(not commands.call(["/usr/bin/git", "-C", app, "status", "--porcelain=v1", "--untracked-files=all"],
                                  label + "-status", 15, end=work_end).strip(), "Issue checkout changed")
        values = {path: digest(app / path) for path in binding["candidateFiles"]}
        require(values == binding["candidateFiles"], "Candidate bytes differ from freeze02")
        owner.save(run / "reports" / (label + ".json"), values)

    def capture(role):
        values = published()
        area_value = values.get("KIRA_VERIFIED_" + role.upper() + "_AREA")
        if not area_value:
            return values
        area = Path(area_value)
        require(area.parent == run / "inputs" and identity(area)[2] == os.getuid(), "Wrong runtime ownership root")
        receipt = owner.read_json(area / "owned.json")
        require(receipt["role"] == role and receipt["host"] == host and receipt["identity"] == identity(area),
                "Wrong installer receipt owner")
        receipts[role] = receipt
        owner.save(run / "reports" / (role + "-install.json"), receipt)
        return values

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM):
        signal.signal(signum, owner.interrupted)
    signal.setitimer(signal.ITIMER_REAL, limits["work"])
    try:
        ruby_before = identity(prefix) if prefix.is_dir() and not prefix.is_symlink() else None
        require(commands.call(["/usr/bin/git", "-C", control, "rev-parse", "HEAD"], "carrier-commit", 15,
                              end=work_end).strip() == os.environ["GITHUB_SHA"], "Wrong carrier commit")
        require(commands.call(["/usr/bin/git", "-C", control, "rev-parse", "HEAD^"], "carrier-parent", 15,
                              end=work_end).strip() == binding["carrierBase"], "Carrier is not directly on trusted base")
        check_source("source-before")
        if host == "macos-arm64":
            require(commands.call(["/usr/bin/xcodebuild", "-version"], "xcode-identity", 30, end=work_end).strip() ==
                    "Xcode 26.4.1\nBuild version 17E202", "Wrong selected Xcode identity")
        pins = runtime.read_json(app / "release/verified-tools.json")["runtime_archives"]
        for role in roles:
            status = result["roles"][role] = {"passed": False}
            try:
                commands.call(["/usr/bin/python3", "-I", "-B", source, role], role + "-installer",
                              limits[role], end=work_end, extra=published())
                values = capture(role)
                expected = pins[role][host]
                expected = expected if isinstance(expected, list) else [expected]
                actual = receipts[role]["archives"]
                require(len(actual) == len(expected) and all(
                    row["url"] == pin["url"] and row["sha256"] == pin["sha256"] and
                    row["bytes"] == pin.get("size", row["bytes"]) and row["files"] > 0
                    for row, pin in zip(actual, expected)), "Wrong authenticated archive receipt set")
                if role == "java":
                    home = Path(values["JAVA_HOME"])
                    require(home.is_relative_to(Path(values["KIRA_VERIFIED_JAVA_AREA"])), "Java escaped owned input")
                    require((run / "path").read_text() == str(home / "bin") + "\n", "Wrong Java PATH publication")
                    status.update(home=str(home), binarySha256=digest(home / "bin/java"),
                                  releaseSha256=digest(home / "release"), actualVersionGate="production installer -version passed")
                elif role == "ruby":
                    require(values["KIRA_VERIFIED_RUBY_PREFIX"] == str(prefix), "Wrong Ruby cache publication")
                    program = ('STDOUT.write(JSON.generate({version:RUBY_VERSION,engine:RUBY_ENGINE,platform:RUBY_PLATFORM,'
                               'executable:RbConfig.ruby,prefix:RbConfig::CONFIG.fetch("prefix"),'
                               'openssl:OpenSSL::OPENSSL_VERSION,psych:Psych::VERSION}))')
                    raw = commands.call([prefix / "bin/ruby", "-rjson", "-rrbconfig", "-ropenssl", "-rpsych", "-e", program],
                                        "ruby-runtime", limits["probe"], end=work_end)
                    probe = json.loads(raw)
                    executable = (prefix / "bin/ruby").resolve()
                    require(probe["version"] == "3.3.12" and probe["engine"] == "ruby" and
                            executable.is_relative_to(prefix) and Path(probe["executable"]).resolve() == executable and
                            probe["prefix"] == str(prefix),
                            "Ruby did not consume the authenticated embedded prefix")
                    status.update(probe=probe, binarySha256=digest(executable),
                                  cacheSelection="direct exact-prefix probe; setup-ruby Action not executed")
                else:
                    home, data = Path(values["ORG_GRADLE_PROJECT_kotlin.native.home"]), Path(values["KONAN_DATA_DIR"])
                    area = Path(values["KIRA_VERIFIED_NATIVE_AREA"])
                    require(home.is_relative_to(area) and data == area / "konan-data" and
                            values["ORG_GRADLE_PROJECT_konan.data.dir"] == str(data) and
                            values["KONAN_USE_INTERNAL_SERVER"] == "0", "Wrong Native home/data selection")
                    properties = home / "konan/konan.properties"
                    require(digest(properties) == receipts[role]["nativePolicy"]["after"] and
                            len(re.findall(rb"(?m)^airplaneMode = true$", properties.read_bytes())) == 1,
                            "Native policy publication differs")
                    marker = data / "dependencies/.extracted"
                    require(marker.read_text() == "".join(pin["directory"] + "\n" for pin in expected[1:]) and
                            all((data / "dependencies" / pin["directory"]).is_dir() for pin in expected[1:]),
                            "Wrong offline dependency markers")
                    status.update(home=str(home), data=str(data), markerSha256=digest(marker), compilerExecution="NOT_RUN")
                status["passed"] = True
            except Exception as error:
                status["error"] = str(error)[:500]
                try:
                    capture(role)
                except Exception as capture_error:
                    status["receiptError"] = str(capture_error)[:500]
                require(commands.drain(), "Owned command remains; do not start another role")
        check_source("source-after")
    except Exception as error:
        result["errors"].append(str(error)[:500])
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        cleanup = result["cleanup"]
        commands.begin_cleanup(min(end, time.monotonic() + limits["cleanup"]))
        cleanup_end = commands.end
        commands.end = min(cleanup_end, time.monotonic() + limits["stop"])
        cleanup["groupsQuietBeforeRemoval"] = commands.drain()
        commands.end = cleanup_end  # Same fixed cleanup allowance, never a renewed deadline.
        try:
            require(cleanup["groupsQuietBeforeRemoval"], "Keep owned paths while a child remains")
            values = published()
            for role in roles:
                try:
                    capture(role)
                except Exception as error:
                    cleanup.setdefault("receiptErrors", []).append(role + ": " + str(error)[:500])
            commands.call(["/usr/bin/python3", "-I", "-B", source, "cleanup"], "runtime-cleanup", 120,
                          end=cleanup_end, extra=values, cleaning=True)
            require(all(not Path(value).exists() and not Path(value).is_symlink()
                        for key, value in values.items() if key.endswith("_AREA") and value), "Owned runtime area remains")
            ruby = receipts.get("ruby")
            if ruby and ruby["createdRuby"]:
                require(not prefix.exists() and not prefix.is_symlink() and
                        not Path(str(prefix) + ".complete").exists() and
                        not Path(str(prefix) + ".complete").is_symlink(), "Created Ruby prefix/marker remains")
            if ruby_before is not None:
                require(identity(prefix) == ruby_before, "Preexisting Ruby ownership changed")
            cleanup["runtimeInputsRemoved"] = True
        except Exception as error:
            cleanup["error"] = str(error)[:500]
        commands.end = min(cleanup_end, time.monotonic() + limits["stop"])
        cleanup["groupsQuietAfterCleanup"] = commands.drain()
        result["commandsNormal"] = commands.normal()
        result["elapsedSeconds"] = round(time.monotonic() - started, 3)
        try:
            require({path: digest(app / path) for path in binding["candidateFiles"]} == binding["candidateFiles"],
                    "Candidate changed during cleanup")
            result["sourceUnchangedAfterCleanup"] = True
            owner.save(run / "reports/result.json", result)
            reports = list((run / "reports").iterdir())
            require(all(p.is_file() and not p.is_symlink() and p.suffix in (".json", ".log") and p.stat().st_size <= 1048576
                        for p in reports) and sum(p.stat().st_size for p in reports) <= 5242880, "Oversized/unsafe report set")
            for path in reports:
                with path.open("rb") as stream:
                    snapshot = stream.read(1048577)
                require(len(snapshot) <= 1048576, "Report grew beyond the snapshot bound")
                with (public / path.name).open("xb") as stream:
                    stream.write(snapshot)
            result["retentionComplete"] = True
        except Exception as error:
            result["errors"].append("retention/source: " + str(error)[:500])
        if cleanup.get("runtimeInputsRemoved") and cleanup["groupsQuietAfterCleanup"]:
            require(run.parent == temporary and identity(run) == run_identity, "Temporary ownership changed")
            shutil.rmtree(run)
            cleanup["ownedTemporaryAbsent"] = not run.exists()
        cleanup["withinDeadline"] = time.monotonic() < cleanup_end
        result["elapsedSeconds"] = round(time.monotonic() - started, 3)
        result["passed"] = (not result["errors"] and set(result["roles"]) == set(roles) and
                            all(row["passed"] for row in result["roles"].values()) and result["commandsNormal"] and
                            not cleanup.get("error") and not cleanup.get("receiptErrors") and
                            cleanup.get("ownedTemporaryAbsent", False) and cleanup["withinDeadline"])
        owner.save(public / "result.json", result)
        retained = list(public.iterdir())
        retention_ready = (all(p.is_file() and not p.is_symlink() and p.suffix in (".json", ".log")
                               and p.stat().st_size <= 1048576 for p in retained) and
                           sum(p.stat().st_size for p in retained if p.name != "result.json") <= 5242880 - 131072)
        result["retentionReady"] = retention_ready
        result["passed"] &= retention_ready
        owner.save(public / "result.json", result)
        output = Path(os.environ["GITHUB_OUTPUT"])
        require(output.is_file() and not output.is_symlink(), "Invalid workflow output")
        with output.open("a") as stream:
            stream.write("retention_ready=" + str(retention_ready).lower() + "\n")
            stream.write("cleanup_barrier_absent=" + str(cleanup.get("ownedTemporaryAbsent", False)).lower() + "\n")
        print("APP61_RUNTIME_ARCHIVE", host, "PASS" if result["passed"] else "FAIL",
              "cleanup=" + str(cleanup.get("ownedTemporaryAbsent", False)).lower())
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        sys.exit("APP61_RUNTIME_ARCHIVE INCOMPLETE: " + str(error))
