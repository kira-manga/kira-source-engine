#!/usr/bin/env python3
"""Bounded, job-private Kotlin 2.2.21 libffi source qualification; no fallback.

The reviewed checkout and hosted Xcode runner are trust roots. Input SHA-256s
are checked before extraction/execution. This does not authenticate the rejected
JetBrains libffi prebuilt, nor claim a reproducible or JetBrains-signed result.
Only prepare runs native code; verify and cleanup never launch Gradle/daemons.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request


VERSION = "2.2.21"
DEPENDENCY = "libffi-3.3-1-macos-arm64"
PROFILE = DEPENDENCY + ".default"
MARKER = ".kira-libffi-source-owned.json"
STATE = "provenance.json"
PROVENANCE = "kira-libffi-provenance.json"
ROOT = Path(__file__).resolve().parents[2]
NATIVE_HOME = "-Dorg.gradle.project.kotlin.native.home="
CACHE_OFF = "-Dorg.gradle.caching=false"
OPTION_ENV = ("GRADLE_OPTS", "JAVA_OPTS", "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS")
PROTECTED = {"kotlin.native.home", "org.jetbrains.kotlin.native.home", "konan.data.dir",
             "kotlin.native.version", "org.jetbrains.kotlin.native.version", "konan.home"}


class BootstrapError(Exception):
    pass


def check(condition, message):
    if not condition:
        raise BootstrapError(message)


def present(path):
    return path.exists() or path.is_symlink()


def regular(path, limit=None):
    info = path.lstat()
    check(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, f"not a single-link regular file: {path.name}")
    check(limit is None or info.st_size <= limit, f"oversized file: {path.name}")
    return info


def directory(path):
    check(stat.S_ISDIR(path.lstat().st_mode), f"not a real directory: {path.name}")


def digest(path):
    regular(path)
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def read_json(path):
    regular(path, 131072)
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value, replace=False):
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    check(len(data) <= 131072, "provenance exceeds its size limit")
    temporary = path.with_name(path.name + ".new") if replace else path
    if replace:
        regular(path, 131072)
    with temporary.open("xb") as output:
        output.write(data)
    temporary.chmod(0o600)
    if replace:
        os.replace(temporary, path)


def pins(repo=ROOT):
    value = read_json(repo / "release/libffi-source.json")
    check(set(value) == {"schema_version", "kotlin_version", "host", "dependency", "source", "patch",
                         "cfi_upstream", "cfi_backport", "recipe", "compiler", "provenance"}, "unexpected source pin schema")
    check(value["schema_version"] == 1 and value["kotlin_version"] == VERSION and
          value["host"] == "macos_arm64" and value["dependency"] == DEPENDENCY,
          "only the reviewed Kotlin 2.2.21 Apple ARM64 recipe is supported")
    routes = {
        "source": ("https://github.com/libffi/libffi/releases/download/v3.3/libffi-3.3.tar.gz", 1305466),
        "patch": ("https://raw.githubusercontent.com/Homebrew/formula-patches/06252df03c68aee70856e5842f85f20b259e5250/libffi/libffi-3.3-arm64.patch", 284504),
        "cfi_upstream": ("https://github.com/libffi/libffi/commit/8308bed5b2423878aa20d7884a99cf2e30b8daf7.patch", 1315),
        "recipe": ("https://raw.githubusercontent.com/JetBrains/kotlin/2146684dcba708e5a304758b41a9e4ec9c7eff71/kotlin-native/tools/libffi/build_for_macos.sh", 933),
        "compiler": (f"https://github.com/JetBrains/kotlin/releases/download/v{VERSION}/kotlin-native-prebuilt-macos-aarch64-{VERSION}.tar.gz", 253527505),
    }
    for name, (url, size) in routes.items():
        pin = value[name]
        check(pin["url"] == url and pin["bytes"] == size and
              re.fullmatch(r"[0-9a-f]{64}", pin["sha256"]), f"invalid {name} pin")
    backport = value["cfi_backport"]
    check(backport["path"] == "scripts/release/libffi-cfi-startproc.patch" and backport["bytes"] == 692 and
          all(re.fullmatch(r"[0-9a-f]{64}", backport[key]) for key in ("sha256", "input_sha256", "output_sha256")),
          "invalid reviewed CFI backport pin")
    verify_download(repo / backport["path"], backport)
    check(value["source"]["archive_root"] == "libffi-3.3" and
          value["compiler"]["archive_root"] == f"kotlin-native-prebuilt-macos-aarch64-{VERSION}",
          "unexpected archive layout")
    catalog = (repo / "gradle/libs.versions.toml").read_text()
    check(re.findall(r'^kotlin\s*=\s*"([^"\n]+)"\s*$', catalog, re.M) == [VERSION],
          "Engine Kotlin version differs from the reviewed source recipe")
    return value


def owned_root(env):
    raw = env.get("RUNNER_TEMP", "")
    check(raw and Path(raw).is_absolute(), "RUNNER_TEMP must name an existing absolute directory")
    temp = Path(raw).resolve(strict=True)
    directory(temp)
    check(re.fullmatch(r"/[A-Za-z0-9._/-]+", str(temp)) and temp != Path("/"),
          "RUNNER_TEMP cannot contain whitespace or property/shell metacharacters")
    return temp / "kira-libffi-source"


def owner(root, env):
    check(root == owned_root(env), "not the exact job-owned bootstrap root")
    return {"schema_version": 1, "root": str(root), "repository": env.get("GITHUB_REPOSITORY", ""),
            "run_id": env.get("GITHUB_RUN_ID", ""), "run_attempt": env.get("GITHUB_RUN_ATTEMPT", "")}


def check_owner(root, env):
    directory(root)
    check(root.stat().st_uid == os.getuid() and root.stat().st_mode & 0o077 == 0,
          "bootstrap root has unexpected ownership or permissions")
    check(read_json(root / MARKER) == owner(root, env), "bootstrap ownership marker mismatch")


def create_owned_root(root, env):
    check(not present(root.parent / PROVENANCE), "prior provenance exists; refusing to overwrite it")
    root.mkdir(mode=0o700)  # Exclusive: never reuse a dependency/cache directory.
    try:
        write_json(root / MARKER, owner(root, env))
    except BaseException:
        root.rmdir()  # Only an empty directory just created by this invocation.
        raise


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        check(urllib.parse.urlsplit(new_url).scheme == "https", "non-HTTPS download redirect")
        return super().redirect_request(request, response, code, message, headers, new_url)


def verify_download(path, pin):
    check(regular(path, pin["bytes"]).st_size == pin["bytes"] and digest(path) == pin["sha256"],
          f"download size/SHA-256 mismatch: {path.name}")


def download(pin, path):
    check(urllib.parse.urlsplit(pin["url"]).scheme == "https", "non-HTTPS input URL")
    request = urllib.request.Request(pin["url"], headers={"User-Agent": "Kira-verified-libffi-source/1"})
    opener = urllib.request.build_opener(HTTPSRedirect())
    deadline = time.monotonic() + 600
    created = False
    try:
        with opener.open(request, timeout=30) as response, path.open("xb") as output:
            created = True
            check(response.status == 200, "download did not return HTTP 200")
            received = 0
            while True:
                # read1 bounds each wait, rather than waiting for a whole MiB from a slow peer.
                block = response.read1(min(1024 * 1024, pin["bytes"] - received + 1))
                if not block:
                    break
                received += len(block)
                check(received <= pin["bytes"] and time.monotonic() <= deadline,
                      "download exceeded its size/time bound")
                output.write(block)
        verify_download(path, pin)
    except BaseException:
        if created and present(path):
            path.unlink()
        raise


def archive_name(name, top):
    while name.startswith("./"):
        name = name[2:]
    name = name.rstrip("/")
    parts = name.split("/")
    check(parts and parts[0] == top and all(part not in ("", ".", "..") for part in parts) and
          not any(ord(char) < 32 or char in "\\:" for char in name), "unsafe archive path")
    return name


def extract_verified(path, pin, destination, *, source=False):
    """Inspect the complete verified tar before writing; never extract through links."""
    verify_download(path, pin)
    top = pin["archive_root"]
    max_entries, max_bytes = (455, 16 * 1024 * 1024) if source else (100000, 2 * 1024**3)
    with tarfile.open(path, "r:gz") as archive:
        members, folded, directories, total = {}, set(), set(), 0
        for member in archive:
            name = archive_name(member.name, top)
            check(name.casefold() not in folded, "duplicate/case-colliding archive path")
            folded.add(name.casefold())
            check(member.isdir() or member.isreg() or (not source and (member.issym() or member.islnk())),
                  "unsupported archive entry type")
            check(not member.sparse and member.size >= 0 and member.mode & 0o7000 == 0,
                  "sparse or special-mode archive entry")
            total += member.size
            members[name] = member
            check(len(members) <= max_entries and total <= max_bytes, "archive exceeds extraction bounds")
            directories.update(str(parent) for parent in PurePosixPath(name).parents if str(parent) != ".")
            if member.isdir():
                directories.add(name)
        check(members and (not source or len(members) == 455), "unexpected archive entry count")
        for name in directories:
            check(name not in members or members[name].isdir(), "archive traverses a non-directory")
        all_names = set(members) | directories
        check(len({name.casefold() for name in all_names}) == len(all_names),
              "case-colliding implicit archive paths")
        links = {}
        for name, member in members.items():
            if not (member.issym() or member.islnk()):
                continue
            link = member.linkname
            check(link and not link.startswith("/") and
                  not any(ord(char) < 32 or char in "\\:" for char in link), "unsafe archive link")
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), link) if member.issym() else link)
            target = archive_name(target, top)
            # Direct internal links only; no cycles, chains, absent targets, or link ancestors.
            check(target in directories or (target in members and members[target].isreg()), "unsafe archive link target")
            check(member.issym() or target not in directories, "hard link to a directory")
            links[name] = target
        destination.mkdir(mode=0o700)
        for name in sorted(directories, key=lambda item: (item.count("/"), item)):
            (destination / name).mkdir(mode=0o700)
        for name, member in members.items():
            if not member.isreg():
                continue
            output = destination / name
            with archive.extractfile(member) as source_file, output.open("xb") as output_file:
                shutil.copyfileobj(source_file, output_file, length=1024 * 1024)
            output.chmod(0o700 if member.mode & 0o111 else 0o600)
            os.utime(output, (member.mtime, member.mtime))
        for name, target in links.items():
            output = destination / name
            if members[name].issym():
                output.symlink_to(posixpath.relpath(target, posixpath.dirname(name)))
            else:
                shutil.copy2(destination / target, output)  # No aliased inode outside this tree.
    return destination / top


def properties(data):
    """The small Java .properties subset needed here, including escaped keys."""
    logical, current = [], ""
    for line in data.decode("latin-1").splitlines():
        current += line.lstrip() if current else line
        if (len(current) - len(current.rstrip("\\"))) % 2:
            current = current[:-1]
            continue
        logical.append(current)
        current = ""
    check(not current, "unterminated property continuation")

    def unescape(value):
        return re.sub(r"\\u([0-9a-fA-F]{4})|\\(.)", lambda match:
                      chr(int(match[1], 16)) if match[1] else
                      {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}.get(match[2], match[2]), value)

    result = []
    for line in logical:
        line = line.lstrip()
        if not line or line[0] in "#!":
            continue
        match = re.match(r"((?:\\.|[^\s=:])*)\s*(?:[=:]\s*)?(.*)$", line)
        check(match is not None, "invalid property line")
        result.append((unescape(match[1]), unescape(match[2])))
    return result


def profile_bytes(original, install):
    values = properties(original)
    check([value for key, value in values if key == "libffiDir.macos_arm64"] == [DEPENDENCY],
          "official libffiDir.macos_arm64 is not the expected dependency")
    check([value for key, value in values if key == "dependencyProfiles"] == ["default alt"],
          "unexpected dependency profile ordering")
    check(not any(key.startswith(DEPENDENCY + ".") for key, _ in values), "prior libffi profile override")
    check(install.is_absolute() and re.fullmatch(r"/[A-Za-z0-9._/-]+", str(install)), "unsafe local profile path")
    return original + (f"\n{PROFILE}={install}\n").encode("ascii")


def override_guard(repo, env, home=None, data=None):
    expected_home = NATIVE_HOME + str(home) if home is not None else None
    seen_home, seen_cache = 0, 0
    for variable in OPTION_ENV:
        raw = env.get(variable, "")
        check(not any(char in raw for char in "\r\n\0"), f"multiline {variable} is unsupported")
        for token in shlex.split(raw):
            check(not token.startswith("@"), f"argument-file override in {variable} is unsupported")
            key, _, value = re.sub(r"^-[DP]", "", token).partition("=")
            key = key.removeprefix("org.gradle.project.")
            if key in PROTECTED:
                check(variable == "GRADLE_OPTS" and token == expected_home, f"unexpected {key} override in {variable}")
                seen_home += 1
            if key == "org.gradle.caching":
                check(value == "false", "conflicting Gradle build-cache override")
                if variable == "GRADLE_OPTS" and token == CACHE_OFF:
                    seen_cache += 1
    for key, value in env.items():
        if key.startswith("ORG_GRADLE_PROJECT_"):
            prop = key.removeprefix("ORG_GRADLE_PROJECT_")
            check(prop not in PROTECTED, f"unexpected project-property environment override: {prop}")
            check(prop != "org.gradle.caching" or value == "false", "conflicting Gradle build-cache environment")
    for key in ("KONAN_HOME", "KOTLIN_NATIVE_HOME"):
        check(key not in env, f"unexpected {key} override")
    check(("KONAN_DATA_DIR" not in env) if data is None else env.get("KONAN_DATA_DIR") == str(data),
          "unexpected or missing KONAN_DATA_DIR")
    for path in {repo / "gradle.properties", repo / "local.properties",
                 Path(env.get("GRADLE_USER_HOME", str(Path(env["HOME"]) / ".gradle"))) / "gradle.properties"}:
        if not present(path):
            continue
        regular(path, 1024 * 1024)
        for key, value in properties(path.read_bytes()):
            system_property = key.startswith("systemProp.")
            key = key.removeprefix("systemProp.").removeprefix("org.gradle.project.")
            check(key not in PROTECTED, f"unexpected {key} in gradle.properties")
            # A project caching default is overridden by our explicit system property.
            if key == "org.gradle.caching" and (system_property or path != repo / "gradle.properties"):
                check(value == "false", "conflicting user Gradle build-cache setting")
    check(seen_home == (1 if home is not None else 0), "private native.home option is missing or duplicated")
    if home is not None:
        check(seen_cache >= 1, "Gradle build-cache disabling option is missing")


def host_guard(env):
    check(platform.system() == "Darwin" and platform.machine() == "arm64" and
          env.get("GITHUB_ACTIONS") == "true" and env.get("RUNNER_OS") == "macOS" and
          env.get("RUNNER_ARCH") == "ARM64", "an actual hosted Darwin ARM64 Actions runner is required")
    check(re.fullmatch(r"[1-9][0-9]*", env.get("GITHUB_RUN_ID", "")) and
          re.fullmatch(r"[1-9][0-9]*", env.get("GITHUB_RUN_ATTEMPT", "")) and env.get("GITHUB_REPOSITORY"),
          "GitHub job identity is missing")


def run(args, *, env, cwd=None, timeout=30):
    process = subprocess.Popen([str(arg) for arg in args], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               start_new_session=True)
    try:
        output, _ = process.communicate(timeout=timeout)
    finally:
        # Bound the entire owned command group, not just make/configure's parent.
        # This also runs on timeout, SIGTERM and KeyboardInterrupt; no global kill.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
            process.stdout.close()
    if process.returncode:
        # This environment has no credentials, and this is only source-build output.
        print(output[-4000:], file=sys.stderr)
        raise BootstrapError(f"{Path(args[0]).name} failed with exit {process.returncode}")
    return output.strip()


def apply_cfi_backport(source, upstream, pin, repo, env):
    # Upstream PR857 has newer BTI/PAC context, so its raw patch cannot apply at
    # zero fuzz to 3.3. Authenticate that authority and the reviewed three-move
    # backport, then require the exact post-Homebrew input and resulting source.
    verify_download(upstream, pin["cfi_upstream"])
    backport = pin["cfi_backport"]
    local_patch = repo / backport["path"]
    verify_download(local_patch, backport)
    assembly = source / "src/aarch64/sysv.S"
    check(digest(assembly) == backport["input_sha256"], "CFI backport input assembly differs from its reviewed hash")
    args = ["/usr/bin/patch", "-p1", "--ignore-whitespace", "--fuzz=0", "--batch", "--forward", "-i", local_patch]
    run(args + ["--dry-run"], env=env, cwd=source, timeout=60)
    run(args, env=env, cwd=source, timeout=60)
    check(digest(assembly) == backport["output_sha256"], "CFI backport output assembly differs from its reviewed hash")
    return digest(assembly)


def xcode_tool(name, developer, env):
    tool = Path(run(["/usr/bin/xcrun", "--sdk", "macosx", "--find", name], env=env))
    check(tool.is_absolute() and tool.name == name and
          tool.resolve(strict=True).is_relative_to(developer) and os.access(tool, os.X_OK),
          f"{name} is not from the selected Xcode")
    # ranlib is a libtool alias: validate its target, but preserve argv[0]'s mode.
    return tool


def build_library(root, source, patch, repo, pin):
    work = root / "work"
    for name in ("home", "tmp"):
        (work / name).mkdir(mode=0o700)
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(work / "home"),
           "TMPDIR": str(work / "tmp"), "LC_ALL": "C", "LANG": "C", "CONFIG_SITE": "/dev/null"}
    developer = Path(run(["/usr/bin/xcode-select", "-p"], env=env)).resolve(strict=True)
    check(str(developer).startswith("/Applications/") and str(developer).endswith(".app/Contents/Developer"),
          "the selected developer directory must be a hosted Xcode application")
    env["DEVELOPER_DIR"] = str(developer)
    tools = {name: xcode_tool(name, developer, env) for name in ("clang", "ar", "ranlib", "lipo", "otool")}
    env.update(CC=str(tools["clang"]), AR=str(tools["ar"]), RANLIB=str(tools["ranlib"]),
               SDKROOT=run(["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path"], env=env),
               PATH=str(tools["clang"].parent) + ":" + env["PATH"])
    compiler = run([tools["clang"], "--version"], env=env)
    check("Apple clang" in compiler and "Target: arm64-apple-" in compiler, "Xcode clang is not native Apple ARM64")
    patch_args = ["/usr/bin/patch", "-p1", "--ignore-whitespace", "--fuzz=0", "--batch", "--forward", "-i", patch]
    run(patch_args + ["--dry-run"], env=env, cwd=source, timeout=60)
    run(patch_args, env=env, cwd=source, timeout=60)
    cfi_sha256 = apply_cfi_backport(source, root / "downloads/cfi_upstream", pin, repo, env)
    configure = [str(source / "configure"), f"--prefix={root / 'install'}", "--disable-docs", "--enable-shared=no"]
    run(configure, env=env, cwd=source, timeout=180)
    run(["/usr/bin/make", "-j2", "install"], env=env, cwd=source, timeout=600)
    library = root / "install/lib/libffi.a"
    regular(library, 64 * 1024 * 1024)
    with library.open("rb") as archive:
        check(archive.read(8) == b"!<arch>\n", "installed libffi is not a static archive")
    check(run([tools["lipo"], "-archs", library], env=env) == "arm64", "libffi is not ARM64-only")
    smoke = work / "libffi-source-smoke"
    # An explicit archive filename, never -lffi or a system dylib search.
    run([tools["clang"], "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", root / "install/include",
         repo / "scripts/release/libffi-source-smoke.c", library, "-o", smoke], env=env, timeout=60)
    linked = run([tools["otool"], "-L", smoke], env=env).splitlines()[1:]
    check(not any("libffi" in line.lower() for line in linked), "smoke unexpectedly links dynamic libffi")
    check(run([smoke], env=env, timeout=30) ==
          "libffi source smoke passed: arm64 integer/floating/aggregate calls and closure", "smoke did not confirm all checks")
    return {"compiler": compiler, "xcode": run(["/usr/bin/xcodebuild", "-version"], env=env),
            "configure": configure[1:], "make_jobs": 2, "patch_fuzz": 0, "static_arch": "arm64",
            "cfi_backport_output_sha256": cfi_sha256,
            "smoke": "integer/floating/aggregate ffi_call and executable callback closure passed"}


def install_hashes(install):
    directory(install)
    files, size = {}, 0
    for base, directories, names in os.walk(install, followlinks=False):
        for name in directories:
            directory(Path(base) / name)
        for name in names:
            path = Path(base) / name
            size += regular(path, 64 * 1024 * 1024).st_size
            check(size <= 64 * 1024 * 1024 and len(files) < 128, "libffi install exceeds bounds")
            check(not re.search(r"\.(dylib|so)(\.|$)", name), "dynamic library in static-only install")
            files[path.relative_to(install).as_posix()] = digest(path)
    check({"lib/libffi.a", "include/ffi.h", "include/ffitarget.h", "lib/pkgconfig/libffi.pc"} <= files.keys(),
          "required static libffi archive/headers/config are missing")
    return files


def blacklist_hash(home):
    path = home / "tools/env_blacklist"
    directory(home / "tools")
    if not present(path):
        return None
    regular(path, 65536)
    check("KONAN_DATA_DIR" not in [line.strip() for line in path.read_text().splitlines()],
          "compiler environment blacklist suppresses KONAN_DATA_DIR")
    return digest(path)


def no_prebuilt(data):
    directory(data)
    # DependencyProcessor's extraction root, download cache, and durable receipt.
    for directory_path in (data, data / "dependencies", data / "dependencies/cache"):
        if not present(directory_path):
            continue
        directory(directory_path)
        check(not any(DEPENDENCY in path.name for path in directory_path.iterdir()), "prebuilt libffi appeared in isolated data")
        receipt = directory_path / ".extracted"
        if present(receipt):
            regular(receipt, 1024 * 1024)
            check(DEPENDENCY not in receipt.read_text(), "prebuilt libffi extraction receipt appeared")


def state_inputs(repo, pin):
    return {"pins": pin, "manifest_sha256": digest(repo / "release/libffi-source.json"),
            "helper_sha256": digest(repo / "scripts/release/prepare-libffi-source.py"),
            "cfi_backport_sha256": digest(repo / pin["cfi_backport"]["path"]),
            "smoke_source_sha256": digest(repo / "scripts/release/libffi-source-smoke.c")}


def prepare(env, repo=ROOT):
    host_guard(env)
    pin = pins(repo)
    override_guard(repo, env)
    root = owned_root(env)
    github_env = Path(env.get("GITHUB_ENV", ""))
    check(github_env.is_absolute() and github_env.resolve(strict=True).is_relative_to(root.parent),
          "GITHUB_ENV must be a runner-owned environment file")
    regular(github_env, 1024 * 1024)
    create_owned_root(root, env)
    state = {"owner": owner(root, env), "inputs": state_inputs(repo, pin), "status": "preparing",
             "qualification_scope": "host bootstrap only; not Engine compilation, package export/promotion or publication",
             "trust": "source-built library; NOT a JetBrains-signed or byte-identical prebuilt",
             "gradle_build_cache": False, "verification_count": 0}
    try:
        write_json(root / STATE, state)
        for name in ("downloads", "work", "data"):
            (root / name).mkdir(mode=0o700)
        for name in ("source", "patch", "recipe", "cfi_upstream"):
            download(pin[name], root / "downloads" / name)
        source = extract_verified(root / "downloads/source", pin["source"], root / "work/source", source=True)
        state["build"] = build_library(root, source, root / "downloads/patch", repo, pin)
        state["installed_files"] = install_hashes(root / "install")
        # The original recipe is provenance, not executed: it lacks our intake/cleanup guards.
        shutil.rmtree(root / "work")
        for name in ("source", "patch", "recipe", "cfi_upstream"):
            (root / "downloads" / name).unlink()
        state["source_intermediates_removed"] = True
        write_json(root / STATE, state, replace=True)
        download(pin["compiler"], root / "downloads/compiler")
        home = extract_verified(root / "downloads/compiler", pin["compiler"], root / "compiler")
        config = home / "konan/konan.properties"
        directory(home / "konan")
        regular(config, 262144)
        original = config.read_bytes()
        modified = profile_bytes(original, root / "install")
        state["original_konan_properties_sha256"] = hashlib.sha256(original).hexdigest()
        config.write_bytes(modified)  # The sole distribution edit: append one local-profile property.
        state["configured_konan_properties_sha256"] = digest(config)
        state["env_blacklist_sha256"] = blacklist_hash(home)
        state["native_home"] = str(home)
        state["konan_data_dir"] = str(root / "data")
        shutil.rmtree(root / "downloads")  # Preserve upstream archive digests, not duplicate archive bytes.
        no_prebuilt(root / "data")
        state["status"] = "prepared"
        write_json(root / STATE, state, replace=True)
        options = env.get("GRADLE_OPTS", "")
        options += (" " if options else "") + NATIVE_HOME + str(home) + " " + CACHE_OFF
        with github_env.open("a", encoding="utf-8") as output:
            output.write(f"GRADLE_OPTS={options}\nKONAN_DATA_DIR={root / 'data'}\n")
        print("Verified source-built ARM64 libffi, static ABI/closure smoke, and private Kotlin 2.2.21 compiler prepared")
    except BaseException:
        state["status"] = "prepare-failed"
        if present(root / STATE):
            write_json(root / STATE, state, replace=True)
        else:
            write_json(root / STATE, state)
        cleanup(env)
        raise


def verify(env, repo=ROOT):
    host_guard(env)
    root = owned_root(env)
    check_owner(root, env)
    state, pin = read_json(root / STATE), pins(repo)
    check(state["owner"] == owner(root, env) and state["status"] == "prepared" and
          state["inputs"] == state_inputs(repo, pin), "prepared provenance or checkout inputs changed")
    home = root / "compiler" / pin["compiler"]["archive_root"]
    for path in (root / "compiler", home, home / "konan"):
        directory(path)
    check(state["native_home"] == str(home) and state["konan_data_dir"] == str(root / "data"), "prepared paths changed")
    override_guard(repo, env, home, root / "data")
    config = home / "konan/konan.properties"
    regular(config, 262144)
    modified = config.read_bytes()
    suffix = f"\n{PROFILE}={root / 'install'}\n".encode("ascii")
    check(modified.endswith(suffix), "private libffi profile is missing or changed")
    original = modified[:-len(suffix)]
    check(hashlib.sha256(original).hexdigest() == state["original_konan_properties_sha256"] and
          digest(config) == state["configured_konan_properties_sha256"] and
          profile_bytes(original, root / "install") == modified, "private compiler config changed beyond its sole override")
    check(blacklist_hash(home) == state["env_blacklist_sha256"], "compiler environment blacklist changed")
    check(install_hashes(root / "install") == state["installed_files"], "installed libffi archive/headers/config changed")
    check(not present(root / "work") and not present(root / "downloads"), "source/download intermediates remain")
    no_prebuilt(root / "data")
    state["verification_count"] += 1
    write_json(root / STATE, state, replace=True)
    print("Verified private source-built libffi/profile/isolation; no disputed prebuilt receipt or download")


def cleanup(env):
    root = owned_root(env)
    if not present(root):
        return
    check_owner(root, env)
    check(shutil.rmtree.avoids_symlink_attacks, "this Python cannot safely remove a symlink-containing owned tree")
    state = read_json(root / STATE) if present(root / STATE) else {"owner": owner(root, env), "status": "interrupted"}
    check(state["owner"] == owner(root, env), "provenance owner mismatch")
    state["cleanup"] = "started"
    evidence = root.parent / PROVENANCE
    if present(evidence):
        prior = read_json(evidence)
        check(prior.get("owner") == owner(root, env) and prior.get("cleanup") == "started", "refusing unrelated provenance overwrite")
    write_json(evidence, state, replace=present(evidence))  # Evidence exists before any disposal.
    shutil.rmtree(root)
    check(not present(root), "owned bootstrap root was not removed")
    state["cleanup"] = "complete"
    write_json(evidence, state, replace=True)
    print("Removed only the marked libffi bootstrap root; small source provenance preserved")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "verify", "cleanup"))
    mode = parser.parse_args().mode
    os.umask(0o077)

    def interrupted(signum, frame):
        raise BootstrapError("interrupted; owned cleanup required")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        {"prepare": prepare, "verify": verify, "cleanup": cleanup}[mode](dict(os.environ))
    except (BootstrapError, OSError, ValueError, KeyError, tarfile.TarError, subprocess.SubprocessError) as error:
        print(f"libffi source {mode} failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
