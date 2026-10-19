"""Synthetic fixed-host reads/index transitions only; no registry, approval or native writes."""

import copy
import hashlib
import io
import json
import ssl
import unittest
from unittest import mock

import publication_policy as policy
import remote_maven_readback as remote
import test_github_publication_receipts as http_fixtures
import test_publication_policy as fixtures


def index_bytes(artifact, versions, timestamp="20261017000000", *, with_release=True):
    values = "".join("<version>" + version + "</version>" for version in versions)
    release = f"<release>{versions[-1]}</release>" if with_release else ""
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<metadata><groupId>{policy.GROUP}</groupId>'
            f'<artifactId>{artifact}</artifactId><versioning><latest>{versions[-1]}</latest>'
            f'{release}<versions>{values}</versions>'
            f'<lastUpdated>{timestamp}</lastUpdated></versioning></metadata>\n').encode()


def selected_packages(repository_id=73):
    names = {":" + module + ":" + publication: policy.GROUP + "." + module + suffix
             for module in policy.MODULES for publication, suffix in policy.PUBLICATIONS.items()}
    return {identity: {"id": 1001 + number, "name": name, "repository_id": repository_id}
            for number, (identity, name) in enumerate(sorted(names.items()))}


def registry_values(bindings, completed=(), version=fixtures.RELEASE):
    packages = [{"id": item["id"], "name": item["name"], "package_type": "maven",
                 "repository": {"id": item["repository_id"], "full_name": policy.REPOSITORY}}
                for item in bindings.values()]
    values = [packages]
    for number, identity in enumerate(sorted(bindings)):
        active = [{"id": 2001 + number, "name": "0.1.0"}]
        if identity in completed:
            active.append({"id": 3001 + number, "name": version})
        values.extend((active, []))
    return values


def registry_responses(values):
    ordered = [values[0], *values[1::2], *values[2::2]]
    return [http_fixtures.Response(value, headers={"X-OAuth-Scopes":
            "read:packages" if number < 16 else "delete:packages,read:packages"})
            for number, value in enumerate(ordered)]


class Response(io.BytesIO):
    def __init__(self, data, headers, status, on_read):
        super().__init__(data)
        self.headers, self.status, self.on_read = headers, status, on_read
        self.read_count = 0

    def getheaders(self):
        return list(self.headers)

    def getheader(self, key, default=None):
        values = [value for name, value in self.headers if name.lower() == key.lower()]
        return ", ".join(values) if values else default

    def read1(self, size):
        self.read_count += 1
        if self.on_read is not None:
            self.on_read()
        return super().read1(size)


class RemoteMavenReadbackTests(unittest.TestCase):
    def setUp(self):
        self.metadata, payloads, _ = fixtures.bundle_fixture()
        self.inventory = self.metadata["inventory.tsv"]
        self.publications = remote.cohort(self.inventory, fixtures.RELEASE)
        self.identities = sorted(self.publications)
        self.objects = dict(payloads)
        for path, data in payloads.items():
            for algorithm in policy.ALGORITHMS:
                self.objects[path + "." + algorithm] = hashlib.new(algorithm, data, usedforsecurity=False).hexdigest().encode()
        self.prior_versions = ["0.1.0", "0.1.4", "0.1.2"]  # Preserve native order, do not sort history.
        self.publish_indexes([])
        self.headers, self.status, self.connections, self.requests, self.responses = {}, {}, [], [], []
        self.on_request = self.on_read = None
        connection = mock.patch.object(remote.http.client, "HTTPSConnection", side_effect=self.connect)
        self.http = connection.start()
        self.addCleanup(connection.stop)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        tls = mock.patch.object(remote.ssl, "create_default_context", return_value=context)
        tls.start()
        self.addCleanup(tls.stop)
        self.addCleanup(self.assert_closed)

    def index_path(self, identity):
        return policy.GROUP.replace(".", "/") + "/" + self.publications[identity]["artifact"] + "/maven-metadata.xml"

    def set_index(self, identity, data):
        path = self.index_path(identity)
        self.objects[path] = data
        for algorithm in policy.ALGORITHMS:
            self.objects[path + "." + algorithm] = hashlib.new(algorithm, data, usedforsecurity=False).hexdigest().encode()

    def publish_indexes(self, completed, *, with_release=True):
        for identity, value in self.publications.items():
            versions = self.prior_versions + ([fixtures.RELEASE] if identity in completed else [])
            self.set_index(identity, index_bytes(value["artifact"], versions, with_release=with_release))

    def connect(self, host, *, timeout, context):
        self.assertIn(host, (remote.HOST, remote.STORAGE_HOST))
        self.assertEqual(timeout, 15)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        if host == remote.STORAGE_HOST:
            self.assertTrue(self.responses[-1].closed)
            self.connections[-1].close.assert_called_once()
        connection = mock.Mock()

        def request(method, path, *, headers):
            self.assertEqual(method, "GET")
            self.assertEqual(headers["Accept-Encoding"], "identity")
            self.assertEqual(headers["Cache-Control"], "no-cache")
            self.assertNotIn("If-None-Match", headers)
            if host == remote.HOST:
                self.assertTrue(path.startswith(remote.PREFIX))
                self.assertEqual(headers["Authorization"], "Basic cmVhZGVyOnN5bnRoZXRpY19yZWFk")
                relative = path[len(remote.PREFIX):]
            else:
                self.assertEqual(set(headers), {"Accept-Encoding", "Cache-Control", "User-Agent"})
                relative = path
            self.requests.append((host, path))
            data = self.objects.get(relative, b"not absence authority")
            response = Response(data, self.headers.get(relative, [("Content-Length", str(len(data)))]),
                                self.status.get(relative, 200 if relative in self.objects else 404), self.on_read)
            self.responses.append(response)
            connection.getresponse.return_value = response
            if self.on_request is not None:
                self.on_request()

        connection.request.side_effect = request
        self.connections.append(connection)
        return connection

    def redirect(self, path, *, port=""):
        target = "/objects/%2C/" + path + "?sig=synthetic_SIGNED%2Cvalue&part=1"
        self.objects[target] = self.objects[path]
        self.status[path] = 302
        self.headers[path] = [("Location", "https://" + remote.STORAGE_HOST + port + target),
                              ("Set-Cookie", "synthetic_COOKIE=never-forwarded")]
        return target

    def assert_closed(self):
        self.assertTrue(all(response.closed for response in self.responses))
        for connection in self.connections:
            connection.close.assert_called_once()

    def before(self):
        return remote.prewrite_indexes(self.inventory, fixtures.RELEASE, username="reader", token="synthetic_read")

    def verify(self, before, completed=None):
        return remote.verify_remote(self.inventory, fixtures.RELEASE, before,
                                    self.identities if completed is None else completed,
                                    username="reader", token="synthetic_read")

    def test_all_fifteen_actual_payloads_four_sidecars_and_existing_index_history_match(self):
        before = self.before()
        self.assertEqual(len(self.requests), 15 * 5)
        # Even though fixture candidate payloads already exist, index observation is NOT unused-GAV authority.
        self.assertNotIn("unused", repr(before))
        self.publish_indexes(self.identities)
        result = self.verify(before)
        row_count = len(policy.text_lines(self.inventory))
        self.assertEqual(len(self.requests), row_count + 2 * 15 * 5)
        self.assertEqual(result["completed"], self.identities)
        self.assertEqual(result["inventory_sha256"], policy.digest(self.inventory))
        for observed in result["indexes"].values():
            self.assertEqual(observed["versions"], self.prior_versions + [fixtures.RELEASE])
        self.assertNotIn("synthetic_read", repr(result))

    def test_partial_progress_reads_only_completed_payloads_but_checks_all_pending_indexes(self):
        before = self.before()
        completed = self.identities[:2]
        self.publish_indexes(completed)
        result = self.verify(before, completed)
        self.assertEqual(result["completed"], completed)
        self.assertEqual(before[self.identities[0]]["versions"], self.prior_versions)
        pending = self.identities[-1]
        self.set_index(pending, index_bytes(self.publications[pending]["artifact"], self.prior_versions + ["9.9.9"]))
        with self.assertRaisesRegex(policy.Refusal, "^Remote native readback refused$"):
            self.verify(before, completed)

    def test_absent_release_and_origin_sha2_checksums_preserve_progress_and_final_full_readback(self):
        self.publish_indexes([], with_release=False)
        for identity in self.identities:
            for algorithm in ("sha256", "sha512"):
                self.status[self.index_path(identity) + "." + algorithm] = 404
        before = self.before()
        for identity, observed in before.items():
            self.assertEqual(set(observed), {"versions", "latest", "release", "last_updated", "size", "sha256"})
            self.assertIsNone(observed["release"])
            self.assertEqual(observed["sha256"], policy.digest(self.objects[self.index_path(identity)]))
        first, second = self.identities[:2]
        self.publish_indexes([first], with_release=False)
        progress = self.verify(before, [first])
        self.publish_indexes([first, second], with_release=False)
        count = len(self.requests)
        progress = remote.verify_remote(self.inventory, fixtures.RELEASE, before, [first, second],
                                        username="reader", token="synthetic_read", progress=progress)
        self.assertEqual(progress["completed"], [first, second])
        self.assertEqual(len(self.requests) - count, len(self.publications[second]["rows"]) + 15 * 5)
        self.publish_indexes(self.identities, with_release=False)
        count = len(self.requests)
        result = self.verify(before)  # Finalizer still rereads all immutable rows, not the progress cache.
        self.assertEqual(len(self.requests) - count, len(policy.text_lines(self.inventory)) + 15 * 5)
        self.assertEqual(result["completed"], self.identities)
        self.assertEqual(result["inventory_sha256"], policy.digest(self.inventory))
        for identity, observed in result["indexes"].items():
            self.assertIsNone(observed["release"])
            self.assertEqual(observed["versions"], self.prior_versions + [fixtures.RELEASE])
            self.assertEqual(observed["latest"], fixtures.RELEASE)
            self.assertEqual(observed["sha256"], policy.digest(self.objects[self.index_path(identity)]))
        self.assertTrue(all(response.read_count == 0 for response in self.responses if response.status == 404))

    def test_release_presence_can_be_added_on_completion_but_not_lost_or_rewritten_after_readback(self):
        self.publish_indexes([], with_release=False)
        before = self.before()
        self.publish_indexes(self.identities)
        result = self.verify(before)
        self.assertTrue(all(item["release"] == fixtures.RELEASE for item in result["indexes"].values()))
        self.publish_indexes([])
        present = self.before()
        self.publish_indexes(self.identities, with_release=False)
        with self.assertRaisesRegex(policy.Refusal, "^Remote native readback refused$"):
            self.verify(present)  # A real prior release marker must not disappear.
        first, pending = self.identities[0], self.identities[-1]
        self.publish_indexes([first], with_release=False)
        progress = self.verify(before, [first])
        self.set_index(first, index_bytes(self.publications[first]["artifact"], self.prior_versions + [fixtures.RELEASE]))
        with self.assertRaisesRegex(policy.Refusal, "^Remote native readback refused$"):
            remote.verify_remote(self.inventory, fixtures.RELEASE, before, [first],
                                 username="reader", token="synthetic_read", progress=progress)
        self.publish_indexes([first], with_release=False)
        self.set_index(pending, index_bytes(self.publications[pending]["artifact"], self.prior_versions))
        with self.assertRaisesRegex(policy.Refusal, "^Remote native readback refused$"):
            self.verify(before, [first])  # Pending None-versus-present state is also immutable.
        count = len(self.requests)
        for release in ("", "9.9.9", False):
            invalid = copy.deepcopy(before)
            invalid[first]["release"] = release
            with self.subTest(release=release), self.assertRaises(policy.Refusal):
                self.verify(invalid, [])
        invalid = copy.deepcopy(before)
        del invalid[first]["release"]
        with self.assertRaises(policy.Refusal):
            self.verify(invalid, [])
        self.assertEqual(len(self.requests), count)

    def test_optional_index_sha2_origin_absence_does_not_hide_status_framing_hash_or_timeout_failures(self):
        for algorithm in ("sha256", "sha512"):
            path = self.index_path(self.identities[0]) + "." + algorithm
            self.status[path] = 404
            self.before()
            for headers in ([("Age", "1")], [("Content-Encoding", "gzip")],
                            [("Location", "https://untrusted.invalid/checksum")],
                            [("Content-Length", "1"), ("content-length", "1")],
                            [("Content-Length", "1"), ("Transfer-Encoding", "chunked")],
                            [("Transfer-Encoding", "gzip")], [("Content-Length", str(remote.INDEX_LIMIT + 1))]):
                self.headers[path] = headers
                with self.subTest(algorithm=algorithm, headers=headers), self.assertRaises(policy.Refusal):
                    self.before()
                self.assertEqual(self.responses[-1].read_count, 0)
            self.headers.pop(path)
            for status in (301, 304, 401, 403, 500):
                self.status[path] = status
                with self.subTest(algorithm=algorithm, status=status), self.assertRaises(policy.Refusal):
                    self.before()
            self.status.pop(path)
            target = self.redirect(path)
            self.status[target] = 404
            with self.assertRaises(policy.Refusal):
                self.before()  # A storage404 cannot be treated as the authenticated origin's absence.
            self.assertEqual(self.requests[-1], (remote.STORAGE_HOST, target))
            self.status.pop(target)
            self.status.pop(path)
            self.headers.pop(path)
            original = self.objects[path]
            for data in (original[:-1], original + b"\n", b"x" * len(original)):
                self.objects[path] = data
                with self.subTest(algorithm=algorithm, size=len(data)), self.assertRaises(policy.Refusal):
                    self.before()
            self.objects[path] = original

            def timeout_on_checksum():
                if self.requests[-1] == (remote.HOST, remote.PREFIX + path):
                    raise TimeoutError("synthetic_provider_detail")

            self.on_read = timeout_on_checksum
            with self.assertRaisesRegex(policy.Refusal, "^Remote pre-write index observation refused$") as refused:
                self.before()
            self.assertTrue(refused.exception.__suppress_context__)
            self.on_read = None

    def test_optional_checksum_mode_is_exact_mutable_sha2_only_and_required_immutable_bytes_still_refuse(self):
        index = self.index_path(self.identities[0])
        immutable = next(path for path, _, _ in self.publications[self.identities[0]]["rows"] if path.endswith(".sha256"))
        cases = [(index, 64, {}), (index + ".md5", 32, {}), (index + ".sha1", 40, {}),
                 (immutable, 64, {}), (index.replace("/maven-", "/1.2.3/maven-") + ".sha256", 64, {}),
                 (index.replace("source-", "unknown-", 1) + ".sha256", 64, {}),
                 (index + ".sha256", 63, {}), (index + ".sha512", 64, {}),
                 (index + ".sha256", 64, {"capture": True}), (index + ".sha256", 0, {"absent": True}),
                 (index + ".sha256", 64, {"optional_index_checksum": 1})]
        for path, limit, options in cases:
            with self.subTest(path=path, options=options), self.assertRaises(policy.Refusal):
                remote._read(path, ("reader", "synthetic_read"), limit, remote._Budget(),
                             **{"optional_index_checksum": True, **options})
        self.http.assert_not_called()
        for path in (index, index + ".md5", index + ".sha1"):
            self.status[path] = 404
            with self.subTest(required=path), self.assertRaises(policy.Refusal):
                self.before()
            self.status.pop(path)
        before = self.before()
        self.publish_indexes(self.identities)
        self.status[immutable] = 404
        with self.assertRaisesRegex(policy.Refusal, "^Remote native readback refused$"):
            self.verify(before)

    def test_each_boundary_reads_new_completion_once_but_final_readback_ignores_progress(self):
        before = self.before()
        first, second = self.identities[:2]
        self.publish_indexes([first])
        progress = self.verify(before, [first])
        self.publish_indexes([first, second])
        count = len(self.requests)
        progress = remote.verify_remote(self.inventory, fixtures.RELEASE, before, [first, second],
                                        username="reader", token="synthetic_read", progress=progress)
        self.assertEqual(len(self.requests) - count, len(self.publications[second]["rows"]) + 15 * 5)
        # Even a same-history rewrite of a previously verified index refuses at the next boundary.
        self.set_index(first, index_bytes(self.publications[first]["artifact"], self.prior_versions + [fixtures.RELEASE], "20261017000001"))
        with self.assertRaisesRegex(policy.Refusal, "^Remote native readback refused$"):
            remote.verify_remote(self.inventory, fixtures.RELEASE, before, [first, second],
                                 username="reader", token="synthetic_read", progress=progress)
        self.publish_indexes(self.identities)
        count = len(self.requests)
        result = self.verify(before)  # Finalizer supplies no prior progress, even after earlier checks.
        self.assertEqual(result["completed"], self.identities)
        self.assertEqual(len(self.requests) - count, len(policy.text_lines(self.inventory)) + 15 * 5)

    def test_changed_truncated_extra_payload_bytes_and_any_of_four_sidecars_refuse(self):
        before = self.before()
        self.publish_indexes(self.identities)
        path = self.publications[self.identities[0]]["rows"][0][0]
        for candidate in [path] + [path + "." + algorithm for algorithm in policy.ALGORITHMS]:
            original = self.objects[candidate]
            for data in (original[:-1], original + b"\n", b"x" * len(original)):
                with self.subTest(path=candidate, size=len(data)):
                    self.objects[candidate] = data
                    with self.assertRaisesRegex(policy.Refusal, "^Remote native readback refused$"):
                        self.verify(before)
            self.objects[candidate] = original

    def test_http_auth_redirect_missing_aged_encoded_ambiguous_and_truncated_responses_refuse_without_retry(self):
        path = self.index_path(self.identities[0])
        for status in (301, 302, 304, 401, 403, 404, 500):
            with self.subTest(status=status):
                count = len(self.requests)
                self.status[path] = status
                with self.assertRaisesRegex(policy.Refusal, "^Remote pre-write index observation refused$"):
                    self.before()
                self.assertEqual(len(self.requests), count + 1)
        self.status.clear()
        for headers in ([('Age', '1')], [('Age', '-1')], [('Content-Encoding', 'gzip')],
                        [('Location', 'https://untrusted.invalid/a')], [('Content-Length', '0')],
                        [('Content-Length', str(len(self.objects[path]) + 1))],
                        [('Content-Length', '1'), ('content-length', '1')],
                        [('Content-Length', '10'), ('Transfer-Encoding', 'chunked')], [('Transfer-Encoding', 'gzip')]):
            with self.subTest(headers=headers):
                self.headers[path] = headers
                with self.assertRaisesRegex(policy.Refusal, "^Remote pre-write index observation refused$"):
                    self.before()

    def test_full_redirected_cohort_keeps_signed_targets_auth_isolation_and_all_byte_checks(self):
        for number, path in enumerate(list(self.objects)):
            self.redirect(path, port=":443" if number % 2 else "")
        before = self.before()
        self.publish_indexes(self.identities)
        for identity in self.identities:
            path = self.index_path(identity)
            for value in [path] + [path + "." + algorithm for algorithm in policy.ALGORITHMS]:
                self.redirect(value)
        result = self.verify(before)
        self.assertEqual(result["completed"], self.identities)
        self.assertEqual(len(self.requests), 2 * (len(policy.text_lines(self.inventory)) + 2 * 15 * 5))
        for registry, storage in zip(self.requests[::2], self.requests[1::2]):
            self.assertEqual(registry[0], remote.HOST)
            self.assertEqual(storage, (remote.STORAGE_HOST, "/objects/%2C/" + registry[1][len(remote.PREFIX):]
                                       + "?sig=synthetic_SIGNED%2Cvalue&part=1"))
        self.assertTrue(all(response.read_count == 0 for response in self.responses[::2]))
        self.assertNotIn("synthetic_", repr(result))

    def test_candidate_path_absence_rejects_partial_files_redirects_and_auth_errors_without_body_reads(self):
        self.before()  # The bridge requires these authenticated positive indexes first.
        paths = [row[0] for item in self.publications.values() for row in item["rows"]]
        first = self.publications[self.identities[0]]["rows"][0][0]
        with self.assertRaisesRegex(policy.Refusal, "^Existing or unobservable candidate path refused$"):
            remote.refuse_existing_paths(self.inventory, fixtures.RELEASE, username="reader", token="synthetic_read")
        for path in paths:
            del self.objects[path]
        count, response_count = len(self.requests), len(self.responses)
        self.assertIsNone(remote.refuse_existing_paths(self.inventory, fixtures.RELEASE,
                                                      username="reader", token="synthetic_read"))
        self.assertEqual(len(self.requests) - count, len(paths))
        self.assertTrue(all(response.read_count == 0 for response in self.responses[response_count:]))
        for status in (200, 302, 304, 401, 403, 500):
            self.status[first] = status
            count = len(self.requests)
            with self.subTest(status=status), self.assertRaises(policy.Refusal):
                remote.refuse_existing_paths(self.inventory, fixtures.RELEASE, username="reader", token="synthetic_read")
            self.assertEqual(len(self.requests), count + 1)
        self.status.clear()
        count = len(self.requests)
        remote.refuse_existing_paths(self.inventory, fixtures.RELEASE, username="reader", token="synthetic_read",
                                     active=self.identities[-1])
        self.assertEqual(len(self.requests) - count, len(self.publications[self.identities[-1]]["rows"]))

    def test_redirect_location_bound_ambiguity_authority_and_control_characters_refuse_before_storage(self):
        path = self.index_path(self.identities[0])
        valid = "https://" + remote.STORAGE_HOST + "/object?sig=synthetic_SIGNED"
        targets = ["", "/object", "//" + remote.STORAGE_HOST + "/object", valid.replace("https:", "http:"),
                   valid.replace(remote.STORAGE_HOST, "untrusted.invalid"),
                   valid.replace(remote.STORAGE_HOST, remote.STORAGE_HOST + ".untrusted.invalid"),
                   valid.replace(remote.STORAGE_HOST, remote.STORAGE_HOST + "@untrusted.invalid"),
                   valid.replace(remote.STORAGE_HOST, "user@" + remote.STORAGE_HOST),
                   valid.replace(remote.STORAGE_HOST, remote.STORAGE_HOST + ":444"),
                   valid.replace(remote.STORAGE_HOST, remote.STORAGE_HOST + ":0443"),
                   valid.replace(remote.STORAGE_HOST, remote.STORAGE_HOST + "."),
                   valid.replace("github-", "%67ithub-"), valid + "#", valid + "#fragment",
                   " " + valid, valid + " ", valid + "\r\n", valid + "\t", valid + "\x00",
                   valid + "\x7f", valid + "\u0085", valid + "\\other", valid + "," + valid,
                   valid + ", " + valid, valid + "x" * remote.LOCATION_LIMIT]
        cases = [[], [("Location", valid), ("location", valid)],
                 *[[("Location", target)] for target in targets]]
        self.status[path] = 302
        for number, headers in enumerate(cases):
            with self.subTest(case=number):
                self.headers[path] = headers
                count = len(self.requests)
                with self.assertRaisesRegex(policy.Refusal, "^Remote pre-write index observation refused$"):
                    self.before()
                self.assertEqual(len(self.requests), count + 1)
        self.assertTrue(all(host == remote.HOST for host, _ in self.requests))

    def test_storage_non200_chains_encoding_age_and_framing_refuse_without_retry(self):
        path = self.index_path(self.identities[0])
        target = self.redirect(path)
        location = ("Location", "https://" + remote.STORAGE_HOST + "/another?sig=synthetic_SIGNED")
        for status in (301, 302, 303, 304, 307, 308, 401, 403, 404, 500):
            with self.subTest(status=status):
                self.status[target], self.headers[target] = status, [location]
                count = len(self.requests)
                with self.assertRaisesRegex(policy.Refusal, "^Remote pre-write index observation refused$"):
                    self.before()
                self.assertEqual(len(self.requests), count + 2)
        self.status.pop(target)
        for number, headers in enumerate([[location], [("Age", "1")], [("Content-Encoding", "gzip")],
                                         [("Content-Length", "1"), ("content-length", "1")],
                                         [("Content-Length", "1"), ("Transfer-Encoding", "chunked")],
                                         [("Transfer-Encoding", "gzip")], [("Content-Length", "0")],
                                         [("Content-Length", str(len(self.objects[target]) + 1))]]):
            with self.subTest(headers=number):
                self.headers[target] = headers
                count = len(self.requests)
                with self.assertRaisesRegex(policy.Refusal, "^Remote pre-write index observation refused$"):
                    self.before()
                self.assertEqual(len(self.requests), count + 2)

    def test_redirect_hops_share_request_byte_and_response_cohort_deadlines(self):
        path = self.index_path(self.identities[0])
        target = self.redirect(path)
        data = self.objects[target]
        budget = remote._Budget()
        budget.requests = 1
        with self.assertRaisesRegex(policy.Refusal, "^Remote request bound exceeded$"):
            remote._read(path, ("reader", "synthetic_read"), len(data), budget)
        self.assertEqual(len(self.requests), 1)
        budget = remote._Budget()
        budget.requests, budget.bytes = 2, len(data)
        facts, body = remote._read(path, ("reader", "synthetic_read"), len(data), budget, capture=True)
        self.assertEqual((budget.requests, budget.bytes, body), (0, 0, data))
        self.assertEqual(facts["sha256"], policy.digest(data))
        budget = remote._Budget()
        budget.bytes = len(data) - 1
        with self.assertRaisesRegex(policy.Refusal, "^Remote response byte bound exceeded$"):
            remote._read(path, ("reader", "synthetic_read"), len(data), budget)
        for deadline, advance in ((300, 16), (5, 3)):
            with self.subTest(deadline=deadline), mock.patch.object(remote.time, "monotonic") as monotonic:
                clock = [0]
                monotonic.side_effect = lambda: clock[0]
                budget = remote._Budget()
                budget.deadline = deadline
                self.on_request = lambda: clock.__setitem__(0, clock[0] + advance)
                count = len(self.requests)
                with self.assertRaisesRegex(policy.Refusal, "^Remote response time bound exceeded$"):
                    remote._read(path, ("reader", "synthetic_read"), len(data), budget)
                self.assertEqual(len(self.requests), count + 2)
                self.assertEqual(self.responses[-1].read_count, 0)
        self.on_request = None

    def test_redirected_immutable_hash_mismatch_and_storage_provider_error_stay_sanitized(self):
        before = self.before()
        self.publish_indexes(self.identities)
        path = self.publications[self.identities[0]]["rows"][0][0]
        target = self.redirect(path)
        original = self.objects[target]
        self.objects[target] = b"x" * len(original)
        count = len(self.requests)
        with self.assertRaisesRegex(policy.Refusal, "^Remote native readback refused$"):
            self.verify(before)
        self.assertEqual(len(self.requests), count + 2)
        self.objects[target] = original

        def fail_storage(host, **kwargs):
            if host == remote.STORAGE_HOST:
                raise OSError("synthetic_SIGNED_storage_error")
            return self.connect(host, **kwargs)

        self.http.side_effect = fail_storage
        count = len(self.requests)
        with self.assertRaisesRegex(policy.Refusal, "^Remote native readback refused$") as refusal:
            self.verify(before)
        self.assertTrue(refusal.exception.__suppress_context__)
        self.assertEqual(len(self.requests), count + 1)

    def test_index_unknown_fields_entities_encodings_duplicates_and_bad_timestamp_refuse(self):
        identity = self.identities[0]
        original = self.objects[self.index_path(identity)]
        cases = [original.replace(b"<metadata>", b'<metadata unknown="x">'),
                 original.replace(b"</metadata>", b"<unknown/></metadata>"),
                 original.replace(b"<groupId>", b"<groupId/><groupId>"),
                 original.replace(b"</versioning>", b"<unknown/></versioning>"),
                 original.replace(b"<release>", b"<release/><release>"),
                 original.replace(b"<release>0.1.2</release>", b"<release/>"),
                 original.replace(b"<release>", b'<release attribute="x">'),
                 original.replace(b"<release>0.1.2</release>", b"<release><version>0.1.2</version></release>"),
                 original.replace(b"<release>0.1.2</release>", b"<release>9.9.9</release>"),
                 original.replace(b"<latest>0.1.2</latest>", b""),
                 original.replace(b"<lastUpdated>20261017000000</lastUpdated>", b""),
                 original.replace(b"</versions>", b"<version>0.1.0</version></versions>"),
                 original.replace(b"20261017000000", b"20261399000000"),
                 original.replace(b"<metadata>", b'<!DOCTYPE metadata [<!ENTITY test SYSTEM "file:///not-read">]><metadata>'),
                 original.decode().encode("utf-16"), b""]
        for data in cases:
            with self.subTest(size=len(data)):
                self.set_index(identity, data)
                with self.assertRaisesRegex(policy.Refusal, "^Remote pre-write index observation refused$"):
                    self.before()

    def test_native_index_must_preserve_order_append_exactly_once_and_match_all_index_sidecars(self):
        before = self.before()
        self.publish_indexes(self.identities)
        identity = self.identities[0]
        artifact, path = self.publications[identity]["artifact"], self.index_path(identity)
        original = self.objects[path]
        bad = [index_bytes(artifact, sorted(self.prior_versions) + [fixtures.RELEASE]),
               index_bytes(artifact, [fixtures.RELEASE]), index_bytes(artifact, self.prior_versions + ["9.9.9"]),
               index_bytes(artifact, self.prior_versions + [fixtures.RELEASE] * 2),
               index_bytes(artifact, self.prior_versions + [fixtures.RELEASE], "20251017000000")]
        for data in bad:
            with self.subTest(size=len(data)):
                self.set_index(identity, data)
                with self.assertRaisesRegex(policy.Refusal, "^Remote native readback refused$"):
                    self.verify(before)
        self.set_index(identity, original)
        for algorithm in policy.ALGORITHMS:
            saved = self.objects[path + "." + algorithm]
            self.objects[path + "." + algorithm] = saved + b"\n"
            with self.subTest(algorithm=algorithm), self.assertRaisesRegex(policy.Refusal, "^Remote native readback refused$"):
                self.verify(before)
            self.objects[path + "." + algorithm] = saved

    def test_invalid_custody_inventory_paths_and_progress_refuse_before_get(self):
        for username, token in [("reader:bad", "synthetic_read"), ("reader", "bad\r\ntoken"), ("", "x"), ("reader", None)]:
            with self.subTest(username=username), self.assertRaises(policy.Refusal):
                remote.prewrite_indexes(self.inventory, fixtures.RELEASE, username=username, token=token)
        original = self.inventory
        rows = policy.text_lines(original)
        for path in ("https://untrusted.invalid/a", "../../escape", "me/manga/kira/source/../escape", "//other/a"):
            columns = rows[0].split("\t")
            columns[6] = path
            self.inventory = ("\t".join(columns) + "\n" + "\n".join(rows[1:]) + "\n").encode()
            with self.assertRaises(policy.Refusal):
                self.before()
        self.http.assert_not_called()
        self.inventory = original
        before = self.before()
        count = len(self.requests)
        for completed in ([self.identities[0]] * 2, ["unknown"], list(reversed(self.identities)), [None]):
            with self.subTest(completed=completed), self.assertRaises(policy.Refusal):
                self.verify(before, completed)
        self.assertEqual(len(self.requests), count)

    def test_read_deadline_size_budget_provider_error_and_prior_copy_cleanup(self):
        path = self.index_path(self.identities[0])
        with mock.patch.object(remote, "INDEX_LIMIT", 64), self.assertRaises(policy.Refusal):
            self.before()
        self.headers[path] = []  # Also bound a streaming response with no Content-Length.
        with mock.patch.object(remote, "INDEX_LIMIT", 64), self.assertRaises(policy.Refusal):
            self.before()
        self.headers.clear()
        clock = [0]
        self.on_read = lambda: clock.__setitem__(0, 999)
        with mock.patch.object(remote.time, "monotonic", side_effect=lambda: clock[0]), self.assertRaises(policy.Refusal):
            self.before()
        self.on_read = None
        before = self.before()
        expected = copy.deepcopy(before)
        self.publish_indexes(self.identities)
        self.on_request = lambda: before[self.identities[0]]["versions"].append("mutated-caller-input")
        self.assertEqual(self.verify(before)["completed"], self.identities)
        self.on_request = None
        self.http.side_effect = OSError("synthetic_PRIVATE_provider_error")
        with self.assertRaisesRegex(policy.Refusal, "^Remote native readback refused$"):
            self.verify(expected)


class RemoteRegistryTests(unittest.TestCase):
    connect = http_fixtures.ReceiptAcquisitionTests.connect
    assertClosed = http_fixtures.ReceiptAcquisitionTests.assertClosed

    def setUp(self):
        http_fixtures.ReceiptAcquisitionTests.setUp(self)
        self.bindings = selected_packages()
        self.reset()
        self.addCleanup(self.assertClosed)

    def reset(self, values=None):
        self.assertClosed()
        values = registry_values(self.bindings) if values is None else values
        self.responses = registry_responses(values)
        self.connections.clear()
        self.requests.clear()

    def observe(self, **kwargs):
        observer = lambda: remote.observe_deleted_versions(self.bindings, 73, token="synthetic_METADATA_AUDIT")
        return remote.observe_registry(self.bindings, 73, fixtures.RELEASE, token="synthetic_PACKAGE_READ",
                                        deleted_observer=observer, **kwargs)

    def test_all_fifteen_repository_associations_and_active_deleted_pages_are_observed(self):
        before = self.observe()
        self.assertEqual(set(before["packages"]), set(self.bindings))
        self.assertEqual(len(self.requests), 31)
        self.assertEqual(before["oauth_scopes"], ["read:packages"])
        self.assertEqual(before["deleted_audit"], {"profile": remote.AUDIT_PROFILE,
                                                 "oauth_scopes": ["delete:packages", "read:packages"]})
        self.assertTrue(all(args[0] == "GET" and args[1].startswith("/orgs/kira-manga/packages") for args, _ in self.requests))
        self.assertEqual(sum("state=deleted" in args[1] for args, _ in self.requests), 15)
        for number, (args, options) in enumerate(self.requests):
            token = "synthetic_PACKAGE_READ" if number < 16 else "synthetic_METADATA_AUDIT"
            self.assertEqual(options["headers"]["Authorization"], "Bearer " + token)
            self.assertEqual("state=deleted" in args[1], number >= 16)
        self.assertNotIn("synthetic_PACKAGE_READ", json.dumps(before))
        self.assertNotIn("synthetic_METADATA_AUDIT", json.dumps(before))
        self.reset()
        self.assertEqual(self.observe(before=before), before)

    def test_missing_duplicate_wrong_repository_and_used_or_deleted_candidate_refuse(self):
        original = registry_values(self.bindings)
        changes = [lambda value: value[0].pop(), lambda value: value[0].append(value[0][0].copy()),
                   lambda value: value[0][0]["repository"].update(id=999),
                   lambda value: value[0][0].update(id=999),
                   lambda value: value[1].append({"id": 4001, "name": fixtures.RELEASE}),
                   lambda value: value[2].append({"id": 4001, "name": fixtures.RELEASE}),
                   lambda value: value[1].append({"id": 2001, "name": "0.5.0"}),
                   lambda value: value[1].append({"id": 4001, "name": "0.1.0"})]
        for number, change in enumerate(changes):
            values = copy.deepcopy(original)
            change(values)
            self.reset(values)
            with self.subTest(change=number), self.assertRaisesRegex(policy.Refusal, "^Remote package registry observation refused$"):
                self.observe()
            self.assertClosed()

    def test_only_genuine_completed_versions_may_appear_and_existing_ids_cannot_change(self):
        before = self.observe()
        first, second = sorted(self.bindings)[:2]
        values = registry_values(self.bindings, [first])
        self.reset(values)
        with self.assertRaises(policy.Refusal):
            self.observe(before=before)
        self.reset(values)
        progress = self.observe(before=before, completed=[first])
        self.reset(registry_values(self.bindings, [first, second]))
        result = self.observe(before=before, completed=[first, second], progress=progress)
        self.assertEqual(len(result["packages"][first]["active"]), 2)
        for field in ("existing", "candidate", "foreign", "deleted"):
            values = registry_values(self.bindings, [first, second])
            if field == "existing":
                values[1][0]["id"] = 9999
            elif field == "candidate":
                values[1][1]["id"] = 9999
            elif field == "foreign":
                values[1].append({"id": 9999, "name": "9.9.9"})
            else:
                values[2].append({"id": 9999, "name": "0.0.9"})
            self.reset(values)
            with self.subTest(field=field), self.assertRaises(policy.Refusal):
                self.observe(before=before, completed=[first, second], progress=progress)

    def test_missing_audit_never_falls_back_and_separate_grants_are_bound_across_observations(self):
        with self.assertRaises(policy.Refusal):
            remote.observe_registry(self.bindings, 73, fixtures.RELEASE, token="synthetic_PACKAGE_READ")
        self.assertEqual(self.requests, [])
        before = self.observe()
        for number in (16, 30):
            self.reset()
            self.responses[number].headers["X-OAuth-Scopes"] = "read:packages,delete:packages,repo"
            with self.subTest(changed_response=number), self.assertRaises(policy.Refusal):
                self.observe(before=before)
        self.reset()
        for response in self.responses[16:]:
            response.headers["X-OAuth-Scopes"] = "read:packages,delete:packages,repo"
        with self.assertRaises(policy.Refusal):
            self.observe(before=before)
        self.reset()
        bad = {"packages": {identity: value | {"deleted": []} for identity, value in self.bindings.items()},
               "grants": {"profile": remote.AUDIT_PROFILE, "oauth_scopes": ["delete:packages", "read:packages"]}}
        bad["packages"].pop(next(iter(self.bindings)))
        with self.assertRaises(policy.Refusal):
            remote.observe_registry(self.bindings, 73, fixtures.RELEASE, token="synthetic_PACKAGE_READ",
                                      deleted_observer=lambda: bad)
        self.assertEqual(len(self.requests), 16)


if __name__ == "__main__":
    unittest.main()
