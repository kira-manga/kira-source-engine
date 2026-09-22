package me.manga.kira.source.engine.internal

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

class HttpDestinationTest {
    @Test
    fun origins_case_fold_ascii_and_compare_effective_ports_without_rewriting_the_url() {
        val cases = listOf(
            "HTTPS://ExAmPlE.test/a?b#c" to CredentialOrigin("https", "example.test", 443),
            "https://example.test:443/" to CredentialOrigin("https", "example.test", 443),
            "https://example.test:00443/" to CredentialOrigin("https", "example.test", 443),
            "https://example.test:8443/" to CredentialOrigin("https", "example.test", 8443),
            "http://example.test/" to CredentialOrigin("http", "example.test", 80),
            "HTTP://example.test:00080/" to CredentialOrigin("http", "example.test", 80),
            "http://127.0.0.1:65535/" to CredentialOrigin("http", "127.0.0.1", 65535),
            "https://XN--BCHER-KVA.test/" to CredentialOrigin("https", "xn--bcher-kva.test", 443),
            "https://[2001:DB8::1]:443/" to CredentialOrigin("https", "[2001:db8::1]", 443),
        )
        for ((url, origin) in cases) {
            val parsed = assertNotNull(HttpDestination.parse(url), url)
            assertEquals(url, parsed.url)
            assertEquals(origin, parsed.credentialOrigin)
        }
    }

    @Test
    fun malformed_or_ambiguous_authorities_are_rejected_not_repaired() {
        val invalid = listOf(
            "", "/relative", "//example.test/path", "ftp://example.test/", "httpsx://example.test/",
            "https:example.test/path", "https:///example.test/", "https://", "https://?query", "https://#fragment",
            "https://user@example.test/", "https://user:pass@example.test/", "https://example.test@foreign.test/",
            "https://foreign.test@example.test/", "https://%65xample.test/", "https://example%2etest/",
            "https://example.test%3a443/", "https://example.test\\@foreign.test/", "https://example.test/path\\other",
            " https://example.test/", "https://example.test/ ", "https://example.test/\t", "https://example.test/\r\n",
            "https://example.test/\u0000", "https://example.test/\u007f", "https://example.test/\u0085", "https://example.test/\u00a0",
            "https://example.test:/", "https://example.test:+443/", "https://example.test:-1/", "https://example.test:0/",
            "https://example.test:65536/", "https://example.test:99999999999999999999/", "https://example.test:４４３/",
            "https://example.test:443:443/", "https://.example.test/", "https://example..test/",
            "https://${"a".repeat(64)}.test/", "https://${List(4) { "a".repeat(63) }.joinToString(".")}/",
            "https://127.1/", "https://2130706433/", "https://0177.0.0.1/", "https://127.00.0.1/",
            "https://0x7f000001/", "https://0x7f.0.0.1/", "https://127.0.0.0x1/", "https://256.0.0.1/",
            "https://127.0.0.1./", "https://example.123/", "https://[::1", "https://::1/",
            "https://[::1]extra/", "https://[::1]:/", "https://[fe80::1%25en0]/", "https://[v1.example]/",
            "https://example.test/%", "https://example.test/%GG", "https://example.test/{unexpanded}",
        )
        for (url in invalid) assertNull(HttpDestination.parse(url), "Invalid destination index ${invalid.indexOf(url)}")
    }

    @Test
    fun unicode_trailing_dot_and_non_dns_reg_names_remain_anonymous() {
        val urls = listOf(
            "https://bücher.test/page", "https://ＢＯＯＫ.test/page", "https://example.test./page",
            "https://foo_bar.test/page", "https://-example.test/page",
        )
        for (url in urls) {
            val destination = assertNotNull(HttpDestination.parse(url))
            assertEquals(url, destination.url)
            assertNull(destination.credentialOrigin)
        }
    }

    @Test
    fun ipv6_requires_valid_brackets_and_groups_but_does_not_invent_address_equivalence() {
        val valid = listOf(
            "::", "::1", "2001:db8::1", "2001:db8:0:0:0:0:0:1", "1:2:3:4:5:6:7:8",
            "::ffff:192.0.2.1", "1:2:3:4:5:6:192.0.2.1",
        )
        for (literal in valid) assertNotNull(HttpDestination.parse("https://[$literal]/")?.credentialOrigin)
        val invalid = listOf(
            "", "1", ":::1", "1:::2", "1::2::3", "1:2:3:4:5:6:7", "1:2:3:4:5:6:7:8:9",
            "1:2:3:4:5:6:7::8", "12345::1", "::g", "::ffff:192.00.2.1", "::ffff:256.0.2.1", "192.0.2.1::",
        )
        for (literal in invalid) assertNull(HttpDestination.parse("https://[$literal]/"))
        assertNotEquals(
            HttpDestination.parse("https://[2001:db8::1]/")?.credentialOrigin,
            HttpDestination.parse("https://[2001:db8:0:0:0:0:0:1]/")?.credentialOrigin,
        )
        assertNotEquals(
            HttpDestination.parse("https://[::ffff:192.0.2.1]/")?.credentialOrigin,
            HttpDestination.parse("https://[::ffff:c000:201]/")?.credentialOrigin,
        )
    }

    @Test
    fun redirect_resolution_matches_rfc_3986_normal_http_examples() {
        val base = assertNotNull(HttpDestination.parse("http://a/b/c/d;p?q"))
        val cases = listOf(
            "g" to "http://a/b/c/g",
            "./g" to "http://a/b/c/g",
            "g/" to "http://a/b/c/g/",
            "/g" to "http://a/g",
            "//g" to "http://g",
            "?y" to "http://a/b/c/d;p?y",
            "g?y" to "http://a/b/c/g?y",
            "#s" to "http://a/b/c/d;p?q#s",
            "g#s" to "http://a/b/c/g#s",
            "g?y#s" to "http://a/b/c/g?y#s",
            ";x" to "http://a/b/c/;x",
            "g;x" to "http://a/b/c/g;x",
            "g;x?y#s" to "http://a/b/c/g;x?y#s",
            "" to "http://a/b/c/d;p?q",
            "." to "http://a/b/c/",
            "./" to "http://a/b/c/",
            ".." to "http://a/b/",
            "../" to "http://a/b/",
            "../g" to "http://a/b/g",
            "../.." to "http://a/",
            "../../" to "http://a/",
            "../../g" to "http://a/g",
            "https://other.test/a/../b?x#y" to "https://other.test/b?x#y",
        )
        for ((reference, expected) in cases) assertEquals(expected, base.resolve(reference)?.url, reference)
    }

    @Test
    fun redirect_dot_removal_does_not_touch_encoded_segments_queries_or_fragments() {
        val base = assertNotNull(HttpDestination.parse("http://a/b/c/d;p?q#old"))
        val cases = listOf(
            "../../../g" to "http://a/g",
            "../../../../g" to "http://a/g",
            "/./g" to "http://a/g",
            "/../g" to "http://a/g",
            "g." to "http://a/b/c/g.",
            ".g" to "http://a/b/c/.g",
            "g.." to "http://a/b/c/g..",
            "..g" to "http://a/b/c/..g",
            "./../g" to "http://a/b/g",
            "./g/." to "http://a/b/c/g/",
            "g/./h" to "http://a/b/c/g/h",
            "g/../h" to "http://a/b/c/h",
            "g;x=1/./y" to "http://a/b/c/g;x=1/y",
            "g;x=1/../y" to "http://a/b/c/y",
            "g?y/./x" to "http://a/b/c/g?y/./x",
            "g?y/../x" to "http://a/b/c/g?y/../x",
            "g#s/./x" to "http://a/b/c/g#s/./x",
            "g#s/../x" to "http://a/b/c/g#s/../x",
            "g//h" to "http://a/b/c/g//h",
            "%2e/%2E%2e/g" to "http://a/b/c/%2e/%2E%2e/g",
            "?" to "http://a/b/c/d;p?",
            "#" to "http://a/b/c/d;p?q#",
            "?url=https://other.test/a,b" to "http://a/b/c/d;p?url=https://other.test/a,b",
        )
        for ((reference, expected) in cases) assertEquals(expected, base.resolve(reference)?.url, reference)
        assertEquals("https://a/g", HttpDestination.parse("https://a")?.resolve("g")?.url)
        assertEquals("https://a?next", HttpDestination.parse("https://a?old")?.resolve("?next")?.url)
    }

    @Test
    fun malformed_redirect_references_are_not_reinterpreted_as_safe_relative_paths() {
        val base = assertNotNull(HttpDestination.parse("https://base.test/a/start"))
        val invalid = listOf(
            "http:g", "https:/other.test", "data:text/plain,example", "javascript:example", ":next",
            "1scheme:value", "///other.test/path", "//user@other.test/path", "//%65xample.test/path",
            "//other.test:0/path", "//[::1]:+443/path", " //other.test/path", "next\n", "next\\other",
            "next%", "next#one#two",
        )
        for (reference in invalid) assertNull(base.resolve(reference), "Invalid reference index ${invalid.indexOf(reference)}")
    }

    @Test
    fun page_reference_guard_preserves_relative_images_but_rejects_non_http_schemes() {
        val valid = listOf("page.webp", "../page.webp", "/page.webp", "//cdn.test/page.webp", "HTTPS://cdn.test/page.webp")
        for (reference in valid) assertTrue(HttpDestination.isSafeImageReference(reference))
        val invalid = listOf("data:image/png,fixture", "javascript:fixture", "https:cdn.test/image", "//user@cdn.test/page", "page image.webp")
        for (reference in invalid) assertFalse(HttpDestination.isSafeImageReference(reference))
    }
}
