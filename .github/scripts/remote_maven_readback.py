"""Fixed-repository GET verification only; never unused-GAV, writer or release authority."""

import base64
from datetime import datetime
import hashlib
import http.client
import re
import ssl
import time
import xml.etree.ElementTree as ET

import publication_policy as policy


HOST = "maven.pkg.github.com"
PREFIX = "/kira-manga/kira-source-engine/"
REPOSITORY_URL = "https://" + HOST + PREFIX[:-1]
SOCKET_SECONDS = 15
READ_SECONDS = 30
COHORT_SECONDS = 300
INDEX_LIMIT = 4 * 1024 * 1024
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}")


def read_credentials(username, token):
    policy.require(type(username) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", username) is not None
                   and type(token) is str and re.fullmatch(r"[A-Za-z0-9_.-]{1,1024}", token) is not None,
                   "Explicit package-read credentials refused")
    return username, token


class _Budget:
    def __init__(self):
        self.deadline = time.monotonic() + COHORT_SECONDS
        self.bytes = policy.MAX_BYTES
        self.requests = policy.MAX_ENTRIES

    def check(self):
        policy.require(time.monotonic() < self.deadline, "Remote readback time bound exceeded")


def _path(value):
    policy.require(type(value) is str and len(value) <= 1024
                   and re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", value) is not None
                   and all(part not in (".", "..") for part in value.split("/")), "Maven path refused")
    return PREFIX + value


def _read(path, credentials, limit, budget, *, capture=False):
    """No redirects, retries, token discovery or decoding; hash the actual response bytes."""
    target = _path(path)
    username, token = read_credentials(*credentials)
    policy.require(type(limit) is int and 0 <= limit <= policy.MAX_BYTES, "Remote byte bound refused")
    budget.check()
    budget.requests -= 1
    policy.require(budget.requests >= 0, "Remote request bound exceeded")
    connection = http.client.HTTPSConnection(HOST, timeout=SOCKET_SECONDS, context=ssl.create_default_context())
    response = None
    try:
        deadline = min(budget.deadline, time.monotonic() + READ_SECONDS)
        authorization = base64.b64encode((username + ":" + token).encode("ascii")).decode("ascii")
        connection.request("GET", target, headers={"Authorization": "Basic " + authorization,
                           "Accept-Encoding": "identity", "Cache-Control": "no-cache",
                           "User-Agent": "kira-native-publication-readback"})
        response = connection.getresponse()
        policy.require(response.status == 200, "Remote HTTP status refused; not absence authority")
        headers = response.getheaders()
        for name in ("content-length", "content-encoding", "transfer-encoding", "age", "location"):
            policy.require(sum(key.lower() == name for key, _ in headers) <= 1, "Ambiguous HTTP headers refused")
        policy.require(response.getheader("Content-Encoding", "identity").lower() == "identity"
                       and not response.getheader("Location"), "Encoded or redirected bytes refused")
        age = response.getheader("Age")
        policy.require(age is None or re.fullmatch(r"0{1,8}", age) is not None, "Aged bytes refused")
        length = response.getheader("Content-Length")
        transfer = response.getheader("Transfer-Encoding")
        policy.require(transfer is None or (transfer.lower() == "chunked" and length is None),
                       "Ambiguous response framing refused")
        if length is not None:
            policy.require(re.fullmatch(r"0|[1-9][0-9]{0,9}", length) is not None and int(length) <= limit,
                           "Remote response length refused")
            length = int(length)
        hashes = {name: hashlib.new(name, usedforsecurity=False) for name in policy.ALGORITHMS}
        size, body = 0, bytearray()
        while True:
            policy.require(time.monotonic() < deadline, "Remote response time bound exceeded")
            chunk = response.read1(min(65536, limit + 1 - size))
            policy.require(time.monotonic() < deadline, "Remote response time bound exceeded")
            if not chunk:
                break
            size += len(chunk)
            budget.bytes -= len(chunk)
            policy.require(size <= limit and budget.bytes >= 0, "Remote response byte bound exceeded")
            for value in hashes.values():
                value.update(chunk)
            if capture:
                body.extend(chunk)
        policy.require(length is None or length == size, "Truncated remote response refused")
        return {"size": size, **{name: value.hexdigest() for name, value in hashes.items()}}, bytes(body)
    finally:
        try:
            if response is not None:
                response.close()
        finally:
            connection.close()


def cohort(inventory, version):
    """Inventory was admitted by archive_intake; also constrain every path before any GET."""
    policy.canonical(version)
    expected = {":" + module + ":" + publication: module + suffix
                for module in policy.MODULES for publication, suffix in policy.PUBLICATIONS.items()}
    result = {identity: {"artifact": artifact, "rows": []} for identity, artifact in expected.items()}
    paths, total = [], 0
    for line in policy.text_lines(inventory):
        row = line.split("\t")
        policy.require(len(row) == 9, "Inventory column count refused")
        identity = row[0] + ":" + row[1]
        policy.require(identity in expected and row[2:5] == [policy.GROUP, expected[identity], version],
                       "Inventory publication identity refused")
        prefix = policy.GROUP.replace(".", "/") + "/" + expected[identity] + "/" + version + "/"
        _path(row[6])
        policy.require(row[6].startswith(prefix) and "/" not in row[6][len(prefix):]
                       and re.fullmatch(r"0|[1-9][0-9]{0,9}", row[7]) is not None
                       and policy.hex_value(row[8]), "Inventory path/size/hash refused")
        size = int(row[7])
        total += size
        policy.require(total <= policy.MAX_BYTES, "Inventory total byte bound exceeded")
        result[identity]["rows"].append((row[6], size, row[8]))
        paths.append(row[6])
    policy.require(paths == sorted(set(paths)) and all(value["rows"] for value in result.values()),
                   "Complete unique fifteen-publication inventory required")
    return result


def _children(element, names, *, repeated=False):
    actual = [child.tag for child in element]
    policy.require(not element.attrib and (element.text or "").strip() == ""
                   and all(not (child.tail or "").strip() for child in element), "Ambiguous index structure")
    policy.require(set(actual) == set(names) and (repeated or len(actual) == len(names)),
                   "Unknown/duplicate index fields")


def _scalar(parent, name):
    values = parent.findall(name)
    policy.require(len(values) == 1 and not list(values[0]) and not values[0].attrib, "Index scalar refused")
    return values[0].text or ""


def _index(body, artifact):
    policy.require(0 < len(body) <= INDEX_LIMIT and b"\x00" not in body
                   and b"<!DOCTYPE" not in body and b"<!ENTITY" not in body,
                   "Unsafe/oversized index XML refused")
    # Decode strictly to exclude UTF16 encodings that conceal forbidden XML declarations.
    root = ET.fromstring(body.decode("utf-8"))
    policy.require(root.tag == "metadata", "Index root refused")
    _children(root, ("groupId", "artifactId", "versioning"))
    policy.require(_scalar(root, "groupId") == policy.GROUP and _scalar(root, "artifactId") == artifact,
                   "Index coordinates differ")
    versioning = root.find("versioning")
    _children(versioning, ("latest", "release", "versions", "lastUpdated"))
    versions = versioning.find("versions")
    _children(versions, ("version",), repeated=True)
    values = []
    for child in versions:
        policy.require(not list(child) and not child.attrib and type(child.text) is str
                       and VERSION.fullmatch(child.text) is not None, "Index version refused")
        values.append(child.text)
    policy.require(1 <= len(values) <= policy.MAX_ENTRIES and len(values) == len(set(values)),
                   "Index version list bound/duplicates refused")
    latest, release = (_scalar(versioning, key) for key in ("latest", "release"))
    policy.require(latest in values and release in values, "Index latest/release absent")
    updated = _scalar(versioning, "lastUpdated")
    policy.require(re.fullmatch(r"[0-9]{14}", updated) is not None, "Index timestamp refused")
    datetime.strptime(updated, "%Y%m%d%H%M%S")
    return {"versions": values, "latest": latest, "release": release, "last_updated": updated,
            "size": len(body), "sha256": policy.digest(body)}


def _read_index(artifact, credentials, budget):
    path = policy.GROUP.replace(".", "/") + "/" + artifact + "/maven-metadata.xml"
    facts, body = _read(path, credentials, INDEX_LIMIT, budget, capture=True)
    result = _index(body, artifact)
    for algorithm in policy.ALGORITHMS:
        expected = facts[algorithm].encode("ascii")
        actual, _ = _read(path + "." + algorithm, credentials, len(expected), budget)
        policy.require(actual["size"] == len(expected) and actual["sha256"] == policy.digest(expected),
                       "Index checksum bytes differ")
    return result


def prewrite_indexes(inventory, version, *, username, token):
    """Require existing authenticated indexes. A missing index is not an unused-GAV proof."""
    try:
        credentials, budget = read_credentials(username, token), _Budget()
        publications = cohort(inventory, version)
        result = {}
        for identity, value in sorted(publications.items()):
            result[identity] = _read_index(value["artifact"], credentials, budget)
            policy.require(version not in result[identity]["versions"] and len(result[identity]["versions"]) < policy.MAX_ENTRIES,
                           "Candidate already appears in index or history cannot grow within bound")
        return result
    except Exception:
        raise policy.Refusal("Remote pre-write index observation refused") from None


def _previous_indexes(previous, identities, version):
    policy.require(type(previous) is dict and set(previous) == set(identities), "Prior index cohort refused")
    result = {}
    for identity, item in previous.items():
        policy.require(type(item) is dict and set(item) == {"versions", "latest", "release", "last_updated", "size", "sha256"},
                       "Prior index fields refused")
        values = item["versions"]
        maximum = policy.MAX_ENTRIES if version is None else policy.MAX_ENTRIES - 1
        policy.require(type(values) is list and 1 <= len(values) <= maximum
                       and all(type(v) is str and VERSION.fullmatch(v) is not None for v in values)
                       and len(values) == len(set(values)) and (version is None or version not in values),
                       "Prior version history refused")
        policy.require(item["latest"] in values and item["release"] in values and type(item["last_updated"]) is str
                       and re.fullmatch(r"[0-9]{14}", item["last_updated"]) is not None
                       and type(item["size"]) is int and 0 < item["size"] <= INDEX_LIMIT
                       and policy.hex_value(item["sha256"]), "Prior index values refused")
        datetime.strptime(item["last_updated"], "%Y%m%d%H%M%S")
        result[identity] = {**item, "versions": list(values)}
    return result


def verify_remote(inventory, version, previous, completed, *, username, token, progress=None):
    """Verify actual completed bytes and all indexes; pending indexes must remain unchanged.

    completed is actual native task state; progress is this invocation's last actual readback,
    never a file/cache grant. New completions' immutable bytes are read once per boundary;
    all indexes stay fresh. The finalizer passes no progress and rereads the whole cohort.
    Sequential reads cannot prove global writer exclusion/unused GAVs.
    """
    try:
        credentials, budget = read_credentials(username, token), _Budget()
        publications = cohort(inventory, version)
        previous = _previous_indexes(previous, publications, version)
        policy.require(type(completed) is list and all(type(value) is str for value in completed)
                       and completed == sorted(set(completed)) and set(completed) <= set(publications),
                       "Completed publication subset refused")
        completed = list(completed)
        verified, prior_observed = [], {}
        if progress is not None:
            policy.require(type(progress) is dict and set(progress) == {"completed", "inventory_sha256", "indexes"}
                           and progress["inventory_sha256"] == policy.digest(inventory), "Readback progress binding refused")
            verified = progress["completed"]
            policy.require(type(verified) is list and all(type(value) is str for value in verified)
                           and verified == sorted(set(verified)) and set(verified) <= set(completed),
                           "Readback progress subset refused")
            verified = list(verified)
            prior_observed = _previous_indexes(progress["indexes"], publications, None)
        observed = {}
        for identity, value in sorted(publications.items()):
            if identity in completed and identity not in verified:
                for path, size, expected_hash in value["rows"]:
                    actual, _ = _read(path, credentials, size, budget)
                    policy.require(actual["size"] == size and actual["sha256"] == expected_hash,
                                   "Native immutable bytes differ from admitted inventory")
            current = _read_index(value["artifact"], credentials, budget)
            if identity in verified:
                policy.require(current == prior_observed[identity], "Previously read-back index changed")
            if identity in completed:
                policy.require(current["versions"] == previous[identity]["versions"] + [version]
                               and current["latest"] == version and current["release"] == version
                               and current["last_updated"] >= previous[identity]["last_updated"],
                               "Native index lost/changed version history")
            else:
                policy.require(current == previous[identity], "Pending publication index changed")
            observed[identity] = current
        return {"completed": list(completed), "inventory_sha256": policy.digest(inventory), "indexes": observed}
    except Exception:
        raise policy.Refusal("Remote native readback refused") from None
