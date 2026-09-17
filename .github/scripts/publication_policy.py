#!/usr/bin/env python3
"""Bounded CI/intake comparisons and local lineage observations. No release authority.

The native Gradle gate owns the KMP/POM model. This module only checks its sealed
file set, committed inputs, and externally selected GitHub identities. Synthetic
test receipts are NOT provenance. Completed intake invokes the pinned native
verifier itself; it never accepts a caller's decoded/"verified" JSON as proof.
"""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
import xml.etree.ElementTree as ET


REPOSITORY = "kira-manga/kira-source-engine"
BRANCH = "remediation/production-readiness-2026-09-04"
REF = "refs/heads/" + BRANCH
WORKFLOW = ".github/workflows/ci.yml"
WORKFLOW_REF = f"{REPOSITORY}/{WORKFLOW}@{REF}"
REPO_URL = "https://github.com/" + REPOSITORY
SIGNER = "https://github.com/" + WORKFLOW_REF
ISSUER = "https://token.actions.githubusercontent.com"
PREDICATE = "https://slsa.dev/provenance/v1"
ARCHIVE = "kira-publication-bytes.tar"
GROUP = "me.manga.kira.source"
MODULES = ("source-contract", "source-engine", "source-testkit")
PUBLICATIONS = {"kotlinMultiplatform": "", "android": "-android", "jvm": "-jvm",
                "iosArm64": "-iosarm64", "iosSimulatorArm64": "-iossimulatorarm64"}
GENERATED = {".gradle", ".kotlin", "build", *(m + "/build" for m in MODULES)}
SOURCE_INPUTS = {"build.gradle.kts", "settings.gradle.kts", "gradle.properties",
                 "gradle/libs.versions.toml", "gradle/publication-gates.gradle.kts",
                 "gradle/wrapper/gradle-wrapper.properties", "gradle/wrapper/gradle-wrapper.jar",
                 *(m + "/build.gradle.kts" for m in MODULES)}
METADATA = {"inventory.tsv", "model.tsv", "producers.txt", "seal.sha256"}
ALGORITHMS = ("md5", "sha1", "sha256", "sha512")
MAX_META = 8 * 1024 * 1024
MAX_BYTES = 512 * 1024 * 1024
MAX_ENTRIES = 4096
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
TASK = r"(?::[A-Za-z0-9_.-]+)+"

# Official cli/cli v2.100.0 release API digest; no binary acquisition by this tool.
# Source 45437bc7eeeb3359bbfddd1742f79de7652fd3e2 uses sigstore-go v1.3.0
# (22d3691c7b8e0c5530fae3c05577690bfef5cd00). Certificate Extensions are FLAT JSON.
GH_ASSET = "gh_2.100.0_linux_amd64.tar.gz"
GH_ASSET_SHA256 = "e4d4bb4498e8d007abe545b6568926793ace1b6447da598294a610018cb164be"
GH_MEMBER = "gh_2.100.0_linux_amd64/bin/gh"


class Refusal(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Refusal(message)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def hex_value(value, length=64):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{%d}" % length, value) is not None


def positive(value):
    return type(value) is int and value > 0


def relative_path(value):
    require(isinstance(value, str) and len(value) <= 1024 and
            all(p not in ("", ".", "..") and re.fullmatch(r"[A-Za-z0-9_.-]+", p)
                for p in value.split("/")), "Unsafe relative path")
    return value


def regular(path):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), "Symlink input refused")
    require(stat.S_ISREG(path.stat().st_mode), "Regular file required")
    return path


def read_small(path):
    path = regular(path)
    require(path.stat().st_size <= MAX_META, "Metadata bound exceeded")
    with path.open("rb") as stream:
        data = stream.read(MAX_META + 1)
    require(len(data) <= MAX_META, "Metadata bound exceeded")
    return data


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON key")
        result[key] = value
    return result


def json_data(data):
    require(len(data) <= MAX_META, "JSON bound exceeded")
    return json.loads(data, object_pairs_hook=unique_object,
                      parse_constant=lambda _: require(False, "Non-finite JSON number"))


def write_new(path, data):
    with Path(path).open("xb") as stream:
        stream.write(data)


def properties(data):
    """Intentionally smaller than Java Properties: no escapes or continuations."""
    require(len(data) <= MAX_META, "Properties bound exceeded")
    result = {}
    for line in data.decode("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "!")):
            continue
        require("\\" not in line and "\x00" not in line, "Escaped/ambiguous properties refused")
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)\s*=\s*(.*)", line)
        require(match is not None and match[1] not in result, "Malformed/duplicate property")
        result[match[1]] = match[2].strip()
    return result


def committed_version(data, overrides=()):
    require(not overrides, "Version/property overrides refused")
    value = properties(data).get("VERSION_NAME", "")
    require(re.fullmatch(VERSION + r"(?:-SNAPSHOT)?", value) is not None,
            "Committed VERSION_NAME must be canonical X.Y.Z or X.Y.Z-SNAPSHOT")
    return value


def canonical(value):
    require(isinstance(value, str) and re.fullmatch(VERSION, value) is not None,
            "A committed canonical candidate version is required; no SNAPSHOT/prerelease")


def matching_tag(version, tag):
    canonical(version)
    require(tag == "v" + version, "Tag/version mismatch")


def candidate_eligible(context, version, source_sha):
    return hex_value(source_sha, 40) and isinstance(version, str) and re.fullmatch(VERSION, version) is not None and all(
        context.get(key) == value for key, value in {
            "GITHUB_REPOSITORY": REPOSITORY, "GITHUB_EVENT_NAME": "push", "GITHUB_REF": REF,
            "GITHUB_SHA": source_sha, "GITHUB_WORKFLOW_REF": WORKFLOW_REF,
            "GITHUB_WORKFLOW_SHA": source_sha,
        }.items())


def check_environment(env):
    forbidden = {"VERSION_NAME", "JAVA_OPTS", "GRADLE_OPTS", "JAVA_TOOL_OPTIONS",
                 "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS"}
    require(not any(k in forbidden or k.startswith("ORG_GRADLE_PROJECT_") for k in env),
            "Ambient Gradle/JVM/property overrides refused (values not logged)")
    home = env.get("GRADLE_USER_HOME")
    if home:
        require(not any((Path(home) / p).exists() for p in
                        ("gradle.properties", "init.gradle", "init.gradle.kts", "init.d")),
                "Unreviewed Gradle user properties/init input refused")


def git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0")
    return subprocess.run(["git", "-C", str(root), *args], env=env, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30).stdout


def release_lineage(root, *, version, tag, source_sha, source_tree, tag_object, branch_tip):
    """Observe pinned LOCAL Git objects/refs; never reserve a tag or grant publication.

    All expected identities come from the separately reviewed caller. Neither a local
    ref nor this comparison authenticates its GitHub origin, protection or approval.
    The campaign ref is not a decision about the eventual protected publisher branch.
    """
    require(isinstance(version, str) and len(version) <= 64 and
            isinstance(tag, str) and len(tag) <= 65, "Version/tag argument bound exceeded")
    matching_tag(version, tag)
    require(all(hex_value(value, 40) for value in (source_sha, source_tree, tag_object, branch_tip)),
            "Exact SHA-1 source/tree/tag-object/branch-tip IDs required")
    root = Path(root).resolve(strict=True)
    require(root.is_dir() and len(str(root)) <= 4096 and not any(c in str(root) for c in "\r\n\x00"),
            "Invalid lineage repository path")
    # Do not reuse caller GIT_DIR/WORK_TREE/OBJECT_DIRECTORY, config injection,
    # alternate objects, replacement namespace or shallow-file overrides.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS="1",
               GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1",
               GIT_ALLOW_PROTOCOL="")

    def inspect(*args, absent=False):
        # Commit-graph caches must not substitute for walking real commit objects.
        result = subprocess.run(["git", "--no-replace-objects", "-c", "core.commitGraph=false",
                                 "-C", str(root), *args], env=env, check=False,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        require(result.returncode == 0 or (absent and result.returncode == 1), "Git lineage read refused")
        require(len(result.stdout) <= MAX_META, "Git lineage output bound exceeded")
        return result.stdout

    def ref_object(ref):
        value = inspect("show-ref", "--verify", "--hash", ref).decode("ascii").strip()
        require(hex_value(value, 40), "Invalid exact Git ref object")
        return value

    def unaltered_history():
        require(inspect("rev-parse", "--is-shallow-repository").strip() == b"false",
                "Shallow lineage refused")
        for name in ("info/grafts", "shallow", "objects/info/alternates", "objects/info/http-alternates"):
            path = Path(inspect("rev-parse", "--path-format=absolute", "--git-path", name).decode().strip())
            require(not path.exists() and not path.is_symlink(), "Grafted/shallow/alternate history refused")
        packs = Path(inspect("rev-parse", "--path-format=absolute", "--git-path", "objects/pack").decode().strip())
        require(not any(packs.glob("*.promisor")), "Promisor pack markers refused; no lazy acquisition")
        require(not inspect("for-each-ref", "--count=1", "--format=%(objectname)", "refs/replace").strip(),
                "Replacement refs refused, even when replacement processing is disabled")
        require(not inspect("config", "--includes", "--name-only", "--get-regexp",
                            r"^(extensions\.partialclone|remote\..*\.promisor)$", absent=True).strip(),
                "Partial/promisor repository refused; no lazy acquisition")

    require(inspect("rev-parse", "--show-object-format").strip() == b"sha1", "Unsupported Git object format")
    require(inspect("rev-parse", "--is-bare-repository").strip() == b"false" and
            Path(inspect("rev-parse", "--show-toplevel").decode().strip()).resolve() == root,
            "Use the exact selected working repository root")
    unaltered_history()
    tag_ref = "refs/tags/" + tag
    require(ref_object(tag_ref) == tag_object and ref_object(REF) == branch_tip,
            "Missing/moved tag object or campaign branch tip")
    require(inspect("cat-file", "-t", tag_object).strip() in (b"tag", b"commit"), "Tag must peel to a commit")
    require(inspect("rev-parse", "--verify", "--end-of-options", tag_object + "^{commit}").decode().strip() == source_sha,
            "Tag/source commit mismatch")
    require(inspect("cat-file", "-t", branch_tip).strip() == b"commit", "Campaign tip is not a commit")
    require(inspect("rev-parse", "--verify", "--end-of-options", source_sha + "^{tree}").decode().strip() == source_tree,
            "Source tree mismatch")
    entry = inspect("ls-tree", source_sha, "--", "gradle.properties").decode().split()
    require(len(entry) == 4 and entry[0] in ("100644", "100755") and entry[1] == "blob" and
            hex_value(entry[2], 40) and entry[3] == "gradle.properties", "Committed regular version file required")
    size = inspect("cat-file", "-s", entry[2]).decode().strip()
    require(re.fullmatch(r"0|[1-9][0-9]{0,9}", size) and int(size) <= MAX_META, "Committed version file bound exceeded")
    require(committed_version(inspect("cat-file", "blob", entry[2])) == version, "Committed version/tag mismatch")
    # Walking both roots also detects a missing ancestor when source == branch tip;
    # merge-base alone could return success without inspecting that missing parent.
    count = inspect("rev-list", "--max-count=" + str(MAX_ENTRIES + 1), "--count", source_sha, branch_tip).decode().strip()
    require(re.fullmatch(r"[1-9][0-9]*", count) and int(count) <= MAX_ENTRIES, "Complete lineage exceeds the supported bound")
    inspect("merge-base", "--is-ancestor", source_sha, branch_tip)
    unaltered_history()
    require(ref_object(tag_ref) == tag_object and ref_object(REF) == branch_tip, "Refs moved during lineage observation")
    return {"scope": "local-git-lineage-observation-only", "ref": REF, "branch_tip": branch_tip,
            "tag": tag, "tag_object": tag_object, "source_sha": source_sha, "source_tree": source_tree,
            "version": version, "walked_commits": int(count), "hold": "stage-c-authority-and-live-rechecks-required",
            "notice": "Not a reservation, tag-signature check, remote-origin/protection proof or approval; refs can change after this observation."}


def source_snapshot(root, sha, fresh=False):
    """Compare EVERY committed blob/mode and reject additions outside exact generated roots."""
    require(hex_value(sha, 40), "Invalid source SHA")
    root = Path(root).resolve(strict=True)
    require(git(root, "rev-parse", "HEAD").decode().strip() == sha, "Checkout/source SHA mismatch")
    tree = git(root, "rev-parse", sha + "^{tree}").decode().strip()
    require(hex_value(tree, 40), "Invalid source tree")
    git(root, "diff", "--cached", "--quiet", sha, "--")
    entries = git(root, "ls-tree", "-rz", "--full-tree", sha).split(b"\x00")
    files = {}
    total = 0
    for entry in filter(None, entries):
        header, name = entry.decode("utf-8").split("\t")
        mode, kind, blob = header.split(" ")
        relative_path(name)
        require(mode in ("100644", "100755") and kind == "blob" and hex_value(blob, 40),
                "Only regular committed source files are supported")
        require(not any(name == p or name.startswith(p + "/") for p in GENERATED),
                "Generated roots cannot contain committed inputs")
        data = read_small(root / name)
        total += len(data)
        require(total <= MAX_BYTES and len(files) < MAX_ENTRIES, "Source bound exceeded")
        actual_mode = "100755" if (root / name).stat().st_mode & 0o111 else "100644"
        actual_blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\x00" + data,
                                   usedforsecurity=False).hexdigest()
        require(actual_blob == blob and actual_mode == mode, "Committed source byte/mode mismatch")
        files[name] = {"size": len(data), "sha256": digest(data), "mode": mode}
    require(WORKFLOW in files and "gradle.properties" in files, "Committed workflow/version absent")
    parents = {str(p) for name in files for p in Path(name).parents if str(p) != "."}
    observed = set()
    for directory, dirs, names in os.walk(root, followlinks=False):
        rel = Path(directory).relative_to(root)
        for name in list(dirs):
            path = (rel / name).as_posix()
            if path == ".git":
                dirs.remove(name)
                continue
            require(not (root / path).is_symlink(), "Source directory symlink refused")
            if path in GENERATED:
                require(not fresh, "Fresh candidate workspace has generated state")
                dirs.remove(name)
            else:
                require(path in parents, "Unexpected source/build directory")
        for name in names:
            path = (rel / name).as_posix()
            if path != ".git":
                require(path in files, "Unexpected source/build file")
                observed.add(path)
    require(observed == set(files), "Incomplete committed source tree")
    return {"source_sha": sha, "source_tree": tree,
            "workflow_sha256": files[WORKFLOW]["sha256"], "files": files}


def ci_state(root, env, fresh=False):
    check_environment(env)
    source = source_snapshot(root, env.get("GITHUB_SHA", ""), fresh)
    version = committed_version(read_small(Path(root) / "gradle.properties"))
    eligible = candidate_eligible(env, version, source["source_sha"])
    if env.get("GITHUB_EVENT_NAME") == "push" and env.get("GITHUB_REPOSITORY") == REPOSITORY:
        require(env.get("GITHUB_REF") == REF and env.get("GITHUB_WORKFLOW_REF") == WORKFLOW_REF
                and env.get("GITHUB_WORKFLOW_SHA") == source["source_sha"], "Trusted-push identity mismatch")
    run_id, attempt = env.get("GITHUB_RUN_ID", ""), env.get("GITHUB_RUN_ATTEMPT", "")
    require(re.fullmatch(r"[1-9][0-9]*", run_id) and re.fullmatch(r"[1-9][0-9]*", attempt),
            "Run/attempt identity missing")
    return {**source, "version": version, "candidate": eligible, "run_id": int(run_id),
            "run_attempt": int(attempt), "repository": env.get("GITHUB_REPOSITORY"),
            "ref": env.get("GITHUB_REF"), "workflow_ref": env.get("GITHUB_WORKFLOW_REF"),
            "workflow_sha": env.get("GITHUB_WORKFLOW_SHA")}


def existing_sdk37(env):
    roots = [Path(env[k]).resolve(strict=True) for k in ("ANDROID_HOME", "ANDROID_SDK_ROOT") if env.get(k)]
    require(roots and len(set(roots)) == 1 and roots[0].is_dir(), "Missing/inconsistent existing SDK roots")
    found = []
    for layout in ("android-37", "android-37.0"):
        directory = roots[0] / "platforms" / layout
        if not directory.exists() and not directory.is_symlink():
            continue
        jar = regular(directory / "android.jar")
        require(jar.stat().st_size > 0, "Empty SDK android.jar")
        props = properties(read_small(directory / "source.properties"))
        require(props.get("AndroidVersion.ApiLevel") in ("37", "37.0") and
                props.get("AndroidVersion.CodeName", "") in ("", "REL") and
                props.get("AndroidVersion.PreviewSdkInt", "0") == "0", "Wrong/preview SDK API")
        xml = directory / "package.xml"
        if xml.exists() or xml.is_symlink():
            data = read_small(xml)
            require(b"<!DOCTYPE" not in data and b"<!ENTITY" not in data, "Unsafe SDK XML")
            document = ET.fromstring(data)
            packages = [e for e in document.iter() if e.tag.split("}")[-1] == "localPackage"]
            require(len(packages) == 1 and packages[0].get("path") == "platforms;" + layout,
                    "SDK package/layout mismatch")
            apis = [e.text for e in packages[0].iter() if e.tag.split("}")[-1] == "api-level"]
            require(len(apis) == 1 and apis[0] in ("37", "37.0"), "SDK metadata API disagreement")
            names = [e.text for e in packages[0].iter() if e.tag.split("}")[-1] == "codename"]
            require(len(names) <= 1 and all(n in (None, "", "REL") for n in names), "Preview SDK package metadata")
        found.append(layout)
    require(found, "Existing SDK37 platform metadata/android.jar missing; no install or alias")
    return str(roots[0])


def text_lines(data):
    require(len(data) <= MAX_META and data.endswith(b"\n") and b"\r" not in data and b"\x00" not in data,
            "Malformed bounded LF metadata")
    lines = data.decode("utf-8").splitlines()
    require(0 < len(lines) <= MAX_ENTRIES and all(lines), "Metadata line bound/empty line")
    return lines


def file_facts(stream, size):
    require(type(size) is int and 0 <= size <= MAX_BYTES, "File size bound exceeded")
    hashes = {a: hashlib.new(a, usedforsecurity=False) for a in ALGORITHMS}
    remaining = size
    while remaining:
        data = stream.read(min(65536, remaining))
        require(data, "Truncated payload")
        for h in hashes.values():
            h.update(data)
        remaining -= len(data)
    return {"size": size, **{a: h.hexdigest() for a, h in hashes.items()}}


def selected_inputs(files):
    return SOURCE_INPUTS | {name for name in files if any(name.startswith(m + "/src/") for m in MODULES)}


def check_bundle(metadata, payloads, version, source_files):
    """No variant/POM resolver: compare model-derived outputs and existing seal relationships."""
    canonical(version)
    require(set(metadata) == METADATA, "Incomplete/extra bundle metadata")
    expected_seal = "".join(f"{digest(metadata[n])}  {n}\n" for n in sorted(METADATA - {"seal.sha256"}))
    require(metadata["seal.sha256"] == expected_seal.encode(), "Metadata seal mismatch")
    expected_ids = {(":" + m, p, GROUP, m + suffix, version)
                    for m in MODULES for p, suffix in PUBLICATIONS.items()}
    rows = {}
    lines = text_lines(metadata["inventory.tsv"])
    for line in lines:
        row = line.split("\t")
        require(len(row) == 9 and tuple(row[:5]) in expected_ids, "Inventory identity/column mismatch")
        relative_path(row[6])
        require(re.fullmatch(r"0|[1-9][0-9]*", row[7]) and hex_value(row[8]), "Inventory size/digest invalid")
        require(row[6] not in rows, "Duplicate inventory path")
        rows[row[6]] = row
    require(list(rows) == sorted(rows), "Inventory path order mismatch")
    model = text_lines(metadata["model.tsv"])
    require(model == sorted(model), "Model order mismatch")
    outputs, sources, producers = {}, {}, set()
    for line in model:
        row = line.split("\t")
        if row[0] == "source":
            require(len(row) == 4 and row[1] not in sources, "Malformed/duplicate source row")
            relative_path(row[1])
            require(row[1] in source_files, "Uncommitted model source")
            facts = source_files[row[1]]
            require(row[2:] == [str(facts["size"]), facts["sha256"]], "Model/committed source mismatch")
            sources[row[1]] = row
        elif row[0] == "output":
            require(len(row) == 12 and tuple(row[1:6]) in expected_ids and
                    row[6] in ("artifact", "sources", "pom", "module"), "Malformed output model")
            relative_path(row[9])
            relative_path(row[10])
            require(row[9] not in outputs and re.fullmatch(r"[A-Za-z0-9_.-]*", row[7]) and
                    re.fullmatch(r"[A-Za-z0-9_.-]+", row[8]), "Duplicate/unsafe model output")
            filename = row[4] + "-" + version + ("-" + row[7] if row[7] else "") + "." + row[8]
            require(row[9] == GROUP.replace(".", "/") + f"/{row[4]}/{version}/{filename}",
                    "Model Maven path mismatch")
            tasks = row[11].split(",")
            require(tasks and len(tasks) == len(set(tasks)) and all(re.fullmatch(TASK, t) for t in tasks),
                    "Malformed model producer tasks")
            producers.update(tasks)
            outputs[row[9]] = row
    require(set(sources) == selected_inputs(source_files), "Incomplete model source set")
    require(text_lines(metadata["producers.txt"]) == sorted(producers), "Producer list mismatch")
    require(set(payloads) == set(outputs), "Missing/extra raw payload; sidecars are NOT payloads")
    expected_rows = {}
    roles = {identity: [] for identity in expected_ids}
    for path, output in outputs.items():
        facts = payloads[path]
        identity = output[1:6]
        roles[tuple(identity)].append(output[6])
        expected_rows[path] = identity + [output[6], path, str(facts["size"]), facts["sha256"]]
        for algorithm in ALGORITHMS:
            sidecar = path + "." + algorithm
            text = facts[algorithm].encode("ascii")
            expected_rows[sidecar] = identity + ["checksum:" + algorithm, sidecar, str(len(text)), digest(text)]
    require(all(set(r) == {"artifact", "sources", "pom", "module"} and
                r.count("pom") == r.count("module") == 1 for r in roles.values()),
            "Incomplete fifteen-publication output set")
    require(rows == expected_rows, "Inventory/raw-byte/expected-sidecar mismatch")
    return {"inventory_sha256": digest(metadata["inventory.tsv"]), "seal_sha256": digest(metadata["seal.sha256"])}


def archive_intake(path, version, source_files):
    """Read bounded uncompressed USTAR only; never extract. Reject hidden extension entries too."""
    path = regular(path)
    require(1024 <= path.stat().st_size <= MAX_BYTES and path.stat().st_size % 512 == 0,
            "Archive size/block bound exceeded")
    metadata, payloads, seen = {}, {}, set()
    with path.open("rb") as stream:
        archive_hash = file_facts(stream, path.stat().st_size)["sha256"]
        stream.seek(0)
        while True:
            header = stream.read(512)
            require(len(header) == 512, "Truncated tar header/end")
            if header == bytes(512):
                require(stream.read(512) == bytes(512), "Missing second tar end block")
                while data := stream.read(65536):
                    require(not any(data), "Data after tar end")
                break
            require(header[257:265] == b"ustar\x0000", "Only plain USTAR entries are admitted")
            entry = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
            name = relative_path(entry.name)
            require(entry.type == tarfile.REGTYPE and not entry.linkname and not entry.pax_headers,
                    "Tar links/directories/devices/extensions refused")
            require(name not in seen and len(seen) < MAX_ENTRIES, "Duplicate/too many tar entries")
            seen.add(name)
            if name in METADATA:
                require(0 <= entry.size <= MAX_META, "Tar metadata bound exceeded")
                metadata[name] = stream.read(entry.size)
                require(len(metadata[name]) == entry.size, "Truncated tar metadata")
            else:
                require(name.startswith("payloads/"), "Unexpected tar entry")
                payloads[name[len("payloads/"):]] = file_facts(stream, entry.size)
            padding = (-entry.size) % 512
            require(stream.read(padding) == bytes(padding), "Invalid/truncated tar padding")
    return {"archive_sha256": archive_hash, **check_bundle(metadata, payloads, version, source_files)}, metadata


def archive_bundle(bundle, output, version, source_files):
    bundle = Path(bundle)
    regular(bundle / "seal.sha256")
    files, dirs_seen = {}, set()
    for directory, dirs, names in os.walk(bundle, followlinks=False):
        for name in dirs:
            path = Path(directory) / name
            require(not path.is_symlink(), "Bundle directory symlink refused")
            dirs_seen.add(path.relative_to(bundle).as_posix())
        for name in names:
            path = regular(Path(directory) / name)
            key = relative_path(path.relative_to(bundle.absolute()).as_posix())
            require(len(files) < MAX_ENTRIES, "Bundle entry bound exceeded")
            files[key] = path
    parents = {str(p) for n in files for p in Path(n).parents if str(p) != "."}
    require(dirs_seen == parents, "Unexpected bundle directory")
    metadata, payloads, total = {}, {}, 0
    for name, path in files.items():
        if name in METADATA:
            metadata[name] = read_small(path)
        else:
            require(name.startswith("payloads/"), "Unexpected bundle entry")
            with path.open("rb") as stream:
                payloads[name[9:]] = file_facts(stream, path.stat().st_size)
        total += path.stat().st_size
        require(total <= MAX_BYTES, "Bundle byte bound exceeded")
    check_bundle(metadata, payloads, version, source_files)
    with Path(output).open("xb") as stream, tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, path in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = path.stat().st_size
            info.mode = 0o644
            with regular(path).open("rb") as payload:
                archive.addfile(info, payload)
    # Detect changes during packing, without resealing or retrying.
    result, packed_metadata = archive_intake(output, version, source_files)
    require(packed_metadata == metadata, "Bundle changed while packing")
    return result


def artifact_name(run_id, attempt):
    return f"kira-publication-bytes-{run_id}-{attempt}"


def expected_identity(expected):
    canonical(expected.get("version"))
    require(all(expected.get(k) == v for k, v in {
        "repository": REPOSITORY, "ref": REF, "workflow_ref": WORKFLOW_REF,
        "workflow_sha": expected.get("source_sha"),
    }.items()), "Expected identity is not the admitted literal producer")
    require(all(hex_value(expected.get(k), n) for k, n in {
        "source_sha": 40, "source_tree": 40, "workflow_sha": 40, "workflow_sha256": 64,
        "archive_sha256": 64, "inventory_sha256": 64, "seal_sha256": 64, "transport_sha256": 64,
    }.items()), "Missing expected independent source/artifact digests")
    require(all(positive(expected.get(k)) for k in
                ("run_id", "run_attempt", "workflow_id", "artifact_id", "producer_job_id", "attestation_job_id")),
            "Missing selected run/attempt/workflow/job/artifact IDs")


def copy_expected_identity(expected):
    """Own the selected scalar identity fields before checking external inputs."""
    require(type(expected) is dict and len(expected) <= 64, "Expected tuple required")
    expected = dict(expected)
    expected_identity(expected)
    return expected


def timestamp(value):
    require(isinstance(value, str) and value.endswith("Z"), "UTC receipt timestamp required")
    return datetime.fromisoformat(value[:-1] + "+00:00")


def compare_official_receipts(expected, attempt, current, jobs, artifact, now):
    """Inputs must be fresh official attempt/current/jobs/artifact REST receipts, not author claims."""
    expected_identity(expected)
    for run in (attempt, current):
        required = {"id": expected["run_id"], "run_attempt": expected["run_attempt"],
                    "head_sha": expected["source_sha"], "head_branch": BRANCH, "event": "push",
                    "path": WORKFLOW, "workflow_id": expected["workflow_id"],
                    "status": "completed", "conclusion": "success"}
        require(all(type(run.get(k)) is type(v) and run.get(k) == v for k, v in required.items()),
                "Failed/in-progress/stale/superseded producer run")
        require(all(run.get(k, {}).get("full_name") == REPOSITORY for k in ("repository", "head_repository")),
                "Run repository mismatch")
    entries = jobs.get("jobs", [])
    require(type(jobs.get("total_count")) is int and jobs["total_count"] == len(entries) == 2,
            "Missing/paginated/unexpected attempt jobs")
    require({j.get("name") for j in entries} == {"verify", "attest"}, "Wrong attempt jobs")
    for job in entries:
        required = {"id": expected["producer_job_id" if job["name"] == "verify" else "attestation_job_id"],
                    "run_id": expected["run_id"], "run_attempt": expected["run_attempt"],
                    "head_sha": expected["source_sha"], "status": "completed", "conclusion": "success"}
        require(all(type(job.get(k)) is type(v) and job.get(k) == v for k, v in required.items()),
                "Missing/failed/wrong-attempt job")
    required = {"id": expected["artifact_id"], "name": artifact_name(expected["run_id"], expected["run_attempt"]),
                "digest": "sha256:" + expected["transport_sha256"], "expired": False,
                "url": f"https://api.github.com/repos/{REPOSITORY}/actions/artifacts/{expected['artifact_id']}"}
    require(all(type(artifact.get(k)) is type(v) and artifact.get(k) == v for k, v in required.items()),
            "Missing/expired/deleted/replaced artifact receipt")
    require(positive(artifact.get("size_in_bytes")) and timestamp(artifact.get("expires_at")) > now,
            "Expired/empty artifact")
    linked = artifact.get("workflow_run", {})
    require(type(linked.get("id")) is int and linked.get("id") == expected["run_id"] and linked.get("head_sha") == expected["source_sha"]
            and linked.get("head_branch") == BRANCH, "Wrong-run artifact")


def _verified_policy(document, expected):
    """Private policy seam: call ONLY on successful pinned-native stdout, never supplied JSON."""
    require(isinstance(document, list) and len(document) == 1, "Exactly one verified attestation required")
    result = document[0]
    require(isinstance(result, dict) and set(result) == {"attestation", "verificationResult"}
            and isinstance(result["attestation"], dict), "Unknown native verifier output shape")
    verified = result["verificationResult"]
    require(verified.get("mediaType") == "application/vnd.dev.sigstore.verificationresult+json;version=0.1",
            "Unknown native result media type")
    cert = verified.get("signature", {}).get("certificate", {})
    required = {"issuer": ISSUER, "subjectAlternativeName": SIGNER, "buildSignerURI": SIGNER,
                "buildSignerDigest": expected["workflow_sha"], "buildConfigURI": SIGNER,
                "buildConfigDigest": expected["workflow_sha"], "runnerEnvironment": "github-hosted",
                "sourceRepositoryURI": REPO_URL, "sourceRepositoryDigest": expected["source_sha"],
                "sourceRepositoryRef": REF, "sourceRepositoryOwnerURI": "https://github.com/kira-manga",
                "buildTrigger": "push", "runInvocationURI":
                f"{REPO_URL}/actions/runs/{expected['run_id']}/attempts/{expected['run_attempt']}"}
    require(all(cert.get(k) == v for k, v in required.items()), "Verified signer/source/invocation mismatch")
    times = verified.get("verifiedTimestamps")
    require(isinstance(times, list) and 0 < len(times) <= 16, "Verified timestamp absent")
    for item in times:
        require(item.get("type") in ("Tlog", "TimestampAuthority") and
                isinstance(item.get("uri"), str) and item["uri"], "Unknown verified timestamp shape")
        timestamp(item.get("timestamp"))
    statement = verified.get("statement", {})
    require(statement.get("_type") == "https://in-toto.io/Statement/v1" and
            statement.get("predicateType") == PREDICATE, "Wrong verified predicate")
    subjects = statement.get("subject", [])
    wanted = {ARCHIVE: {"sha256": expected["archive_sha256"]},
              "inventory.tsv": {"sha256": expected["inventory_sha256"]}}
    require(len(subjects) == 2 and {s.get("name") for s in subjects} == set(wanted) and
            all(s.get("digest") == wanted[s["name"]] for s in subjects), "Verified subject/digest mismatch")


def native_verify(gh_asset, bundle, subjects, expected):
    """No PATH gh, download, token or guessed schema fallback. Caller separately admits this asset.

    Signature/certificate verification runs BEFORE interpreting native JSON. Public trust-root
    refresh by gh may require network; this is not advertised as network-isolated verification.
    """
    require(platform.system() == "Linux" and platform.machine() == "x86_64", "Pinned verifier host mismatch")
    asset = regular(gh_asset)
    with asset.open("rb") as stream:
        asset_bytes = stream.read(MAX_META * 8 + 1)
    require(len(asset_bytes) <= MAX_META * 8 and digest(asset_bytes) == GH_ASSET_SHA256,
            "Official native verifier asset pin mismatch")
    bundle = regular(bundle)
    require(bundle.suffix in (".json", ".jsonl"), "Native bundle must be JSON/JSONL")
    data = read_small(bundle)
    with tempfile.TemporaryDirectory(prefix="kira-native-verifier-") as temp:
        temp = Path(temp)
        # Read the SAME pinned immutable bytes, not a second open of a replaceable path.
        with tarfile.open(fileobj=io.BytesIO(asset_bytes), mode="r:gz") as package:
            members = package.getmembers()
            require(len(members) <= MAX_ENTRIES, "Native distribution entry bound")
            matches = [m for m in members if m.name == GH_MEMBER]
            require(len(matches) == 1 and matches[0].isreg() and 0 < matches[0].size <= MAX_META * 8,
                    "Pinned native executable missing/invalid")
            binary = temp / "gh"
            write_new(binary, package.extractfile(matches[0]).read(MAX_META * 8 + 1))
        binary.chmod(0o700)
        local_bundle = temp / ("bundle" + bundle.suffix)
        write_new(local_bundle, data)
        env = {"PATH": os.defpath, "HOME": str(temp), "XDG_CONFIG_HOME": str(temp),
               "GH_HOST": "github.com", "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1"}
        for subject in subjects:
            command = [str(binary), "attestation", "verify", str(regular(subject)), "--bundle", str(local_bundle),
                       "--hostname", "github.com", "--repo", REPOSITORY, "--format", "json",
                       "--predicate-type", PREDICATE, "--cert-identity", SIGNER, "--cert-oidc-issuer", ISSUER,
                       "--signer-digest", expected["workflow_sha"], "--source-ref", REF,
                       "--source-digest", expected["source_sha"], "--deny-self-hosted-runners"]
            result = subprocess.run(command, env=env, check=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=120)
            _verified_policy(json_data(result.stdout), expected)


def verify_completed_bytes(root, expected, archive, gh_asset, attestation):
    """Shared byte/native phase; not a completed receipt or release-authorizing result."""
    expected_identity(expected)
    source = source_snapshot(root, expected["source_sha"], fresh=True)
    require(all(source[k] == expected[k] for k in ("source_tree", "workflow_sha256")), "Receipt source tree/workflow mismatch")
    require(committed_version(read_small(Path(root) / "gradle.properties")) == expected["version"],
            "Receipt/committed version mismatch")
    measured, metadata = archive_intake(archive, expected["version"], source["files"])
    require(all(expected[k] == v for k, v in measured.items()), "Independent raw/manifest/seal digest mismatch")
    with tempfile.TemporaryDirectory(prefix="kira-receipt-subject-") as temp:
        inventory = Path(temp) / "inventory.tsv"
        write_new(inventory, metadata["inventory.tsv"])
        native_verify(gh_asset, attestation, (archive, inventory), expected)
    return source, measured


def recheck_completed_bytes(root, expected, archive, source, measured):
    """Run after receipt acquisition/comparison too, detecting mutation during that phase."""
    require(source_snapshot(root, expected["source_sha"], fresh=True) == source, "Source changed during intake")
    require(archive_intake(archive, expected["version"], source["files"])[0] == measured, "Archive changed during intake")


def completed_intake(root, expected, receipts, archive, gh_asset, attestation):
    """Offline supplied-receipt comparison; saved JSON cannot establish live freshness."""
    expected = copy_expected_identity(expected)
    source, measured = verify_completed_bytes(root, expected, archive, gh_asset, attestation)
    compare_official_receipts(expected, receipts["attempt"], receipts["current"], receipts["jobs"],
                              receipts["artifact"], datetime.now(timezone.utc))
    recheck_completed_bytes(root, expected, archive, source, measured)


APPROVAL_FIELDS = {"repository", "source_sha", "source_tree", "version", "tag", "producer_workflow_ref",
                   "producer_workflow_sha", "publisher_workflow_ref", "publisher_workflow_sha", "run_id",
                   "run_attempt", "artifact_id", "transport_sha256", "archive_sha256", "inventory_sha256",
                   "seal_sha256", "subjects", "app_parity_ref", "backend_parity_ref"}


def equality_issues(expected, observed, fields):
    """An empty difference is equality ONLY, never authenticated approval or unused-GAV authority."""
    observed = observed if isinstance(observed, dict) else {}
    issues = ["unexpected:" + k for k in observed.keys() - fields]
    for key in sorted(fields):
        if key not in expected or expected[key] in (None, "", [], {}):
            issues.append("expected-missing:" + key)
        elif key not in observed:
            issues.append("missing:" + key)
        elif type(observed[key]) is not type(expected[key]) or observed[key] != expected[key]:
            issues.append("mismatch:" + key)
    return tuple(sorted(issues))


def release_holds(expected_tuple, observed_tuple, expected_readback, observed_readback, *, used_gavs=(), rerun=False):
    """Finite refusal table, NOT a registry/approval service. There is deliberately always a hold."""
    issues = ["stage-c-authority-and-live-rechecks-required"]
    issues.extend(equality_issues(expected_tuple, observed_tuple, APPROVAL_FIELDS))
    if not expected_readback:
        issues.append("complete-readback-expectation-missing")
    else:
        issues.extend("readback:" + i for i in equality_issues(expected_readback, observed_readback, set(expected_readback)))
    if used_gavs:
        issues.append("used-or-partial-gav-cohort")
    if rerun:
        issues.append("rerun-hold")
    return tuple(issues)


def emit_outputs(values):
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        for key, value in values.items():
            value = str(value).lower() if type(value) is bool else str(value)
            require(re.fullmatch(r"[A-Za-z0-9_.-]+", key) and "\n" not in value and "\r" not in value,
                    "Unsafe workflow output")
            stream.write(f"{key}={value}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sdk37")
    for name in ("ci-start", "ci-check", "pack"):
        child = sub.add_parser(name)
        child.add_argument("--state", type=Path, required=True)
        if name == "pack":
            child.add_argument("--output", type=Path, required=True)
    child = sub.add_parser("intake")
    child.add_argument("--archive", type=Path, required=True)
    child.add_argument("--inventory", type=Path, required=True)
    child = sub.add_parser("receipt", help="Offline saved-receipt comparison; no live freshness")
    for name in ("expected", "receipts", "archive", "gh-asset", "attestation"):
        child.add_argument("--" + name, type=Path, required=True)
    child = sub.add_parser("lineage", help="Read-only local tag/commit/ancestry observation, never publication authority")
    for name in ("version", "tag", "source-sha", "source-tree", "tag-object", "branch-tip"):
        child.add_argument("--" + name, required=True)
    args = parser.parse_args()
    if args.command == "sdk37":
        root = existing_sdk37(os.environ)
        with Path(os.environ["GITHUB_ENV"]).open("a") as stream:
            require("\n" not in root and "\r" not in root, "Unsafe SDK path")
            stream.write(f"ANDROID_HOME={root}\nANDROID_SDK_ROOT={root}\n")
        print("Existing SDK37 metadata/android.jar agree; no SDK acquisition.")
    elif args.command in ("ci-start", "ci-check", "pack"):
        require(not args.state.resolve().is_relative_to(args.root.resolve()), "Keep receipts outside checkout/bundle")
        state = ci_state(args.root, os.environ, fresh=args.command == "ci-start")
        if args.command == "ci-start":
            write_new(args.state, json.dumps(state, sort_keys=True).encode())
            emit_outputs({k: state[k] for k in ("candidate", "version", "source_tree", "workflow_sha256")})
            print("Eligible candidate route (NOT release approval)." if state["candidate"] else
                  "Check-only run: no candidate produced; no export/upload/attestation.")
        else:
            require(json_data(read_small(args.state)) == state, "Before/after committed-input disagreement")
            if args.command == "pack":
                require(state["candidate"], "Check-only runs cannot export/upload candidates")
                require(not args.output.resolve().is_relative_to(args.root.resolve()), "Keep archive outside checkout")
                result = archive_bundle(args.root / "build/publication-bytes/bundle", args.output,
                                        state["version"], state["files"])
                require(ci_state(args.root, os.environ) == state, "Committed inputs changed during packing")
                emit_outputs(result)
                print(json.dumps(result, sort_keys=True))
    elif args.command == "intake":
        state = ci_state(args.root, os.environ, fresh=True)
        require(state["candidate"], "Noncandidate attestation intake refused")
        for key in ("version", "source_tree", "workflow_sha256"):
            require(state[key] == os.environ["EXPECTED_" + key.upper()], "Producer/attester source mismatch")
        require(not args.inventory.resolve().is_relative_to(args.root.resolve()), "Never extract into checkout")
        measured, metadata = archive_intake(args.archive, state["version"], state["files"])
        require(all(os.environ["EXPECTED_" + k.upper()] == v for k, v in measured.items()), "Independent digest mismatch")
        write_new(args.inventory, metadata["inventory.tsv"])
        print("Exact candidate archive/manifest intake only; workflow is not yet a completed receipt.")
    elif args.command == "lineage":
        print(json.dumps(release_lineage(args.root, version=args.version, tag=args.tag,
                                        source_sha=args.source_sha, source_tree=args.source_tree,
                                        tag_object=args.tag_object, branch_tip=args.branch_tip), sort_keys=True))
    else:
        completed_intake(args.root, json_data(read_small(args.expected)), json_data(read_small(args.receipts)),
                         args.archive, args.gh_asset, args.attestation)
        print("Offline saved-receipt comparison matched. No live freshness or release authority.")


if __name__ == "__main__":
    try:
        main()
    except (Refusal, OSError, ValueError, KeyError, TypeError, AttributeError, tarfile.TarError,
            ET.ParseError, subprocess.SubprocessError) as error:
        # Do not dump tool stderr, environment values, private paths or receipt bodies.
        print(f"Publication policy refused ({type(error).__name__}). No release authority.", file=sys.stderr)
        sys.exit(1)
