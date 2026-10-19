package me.manga.kira.source.engine.internal

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull

class SourceHeaderPolicyTest {
    private val headers = mapOf("X-Fixture" to "synthetic", "cOoKiE" to "synthetic", "Referer" to "synthetic")

    private fun SourceHeaderPolicy.selected(url: String): Map<String, String> =
        headersFor(assertNotNull(HttpDestination.parse(url)), headers)

    @Test
    fun signed_base_and_image_authorize_only_exact_scheme_host_effective_port() {
        val policy = SourceHeaderPolicy("https://api.example.test/root", "https://img.example.test:8443/images", emptyList())
        val trusted = listOf("https://api.example.test/page", "HTTPS://API.EXAMPLE.TEST:00443/page", "https://img.example.test:8443/page")
        val foreign = listOf(
            "https://api.example.test:8443/page", "http://api.example.test/page", "https://img.example.test/page",
            "http://img.example.test:8443/page", "https://sub.api.example.test/page", "https://example.test/page",
            "https://api.example.test.evil.test/page", "https://evilapi.example.test/page", "https://api-example.test/page",
        )
        for (url in trusted) assertEquals(headers, policy.selected(url), url)
        for (url in foreign) assertEquals(emptyMap(), policy.selected(url), url)
    }

    @Test
    fun bare_extra_hosts_grant_only_the_exact_host_on_https_443() {
        val policy = SourceHeaderPolicy("https://api.test:8443", "", listOf("CDN.api.test"))
        val trusted = listOf("https://cdn.api.test/page", "https://CDN.API.TEST:443/page", "https://cdn.api.test:00443/page")
        val foreign = listOf(
            "http://cdn.api.test/page", "https://cdn.api.test:8443/page", "https://sub.cdn.api.test/page",
            "https://api.test/page", "https://cdn.api.test.evil.test/page", "https://cdn.api.test./page",
        )
        for (url in trusted) assertEquals(headers, policy.selected(url))
        for (url in foreign) assertEquals(emptyMap(), policy.selected(url))
    }

    @Test
    fun http_base_allows_explicit_http_origins_but_not_an_undeclared_upgrade() {
        val policy = SourceHeaderPolicy("http://api.test", "http://img.test:8080", listOf("extra.test"))
        val trusted = listOf("http://api.test/page", "http://api.test:80/page", "http://img.test:8080/page", "https://extra.test/page")
        val foreign = listOf("https://api.test/page", "http://img.test/page", "http://extra.test/page", "http://other.test/page")
        for (url in trusted) assertEquals(headers, policy.selected(url))
        for (url in foreign) assertEquals(emptyMap(), policy.selected(url))
        assertEquals(headers, SourceHeaderPolicy("http://api.test", "https://img.test", emptyList()).selected("https://img.test/page"))
    }

    @Test
    fun https_base_never_exports_source_headers_to_an_http_image_origin() {
        val policy = SourceHeaderPolicy("https://api.test", "http://img.test", listOf("img.test"))
        assertEquals(emptyMap(), policy.selected("http://img.test/page"))
        assertEquals(headers, policy.selected("https://img.test/page"))
    }

    @Test
    fun unsupported_host_spellings_cannot_become_credential_authority_even_when_declared() {
        val policy = SourceHeaderPolicy("https://bücher.test", "https://img.test.", listOf("ＢＯＯＫ.test", "foo_bar.test"))
        val urls = listOf(
            "https://bücher.test/page", "https://xn--bcher-kva.test/page", "https://img.test./page",
            "https://img.test/page", "https://ＢＯＯＫ.test/page", "https://book.test/page", "https://foo_bar.test/page",
        )
        for (url in urls) assertEquals(emptyMap(), policy.selected(url))
        val ascii = SourceHeaderPolicy("https://xn--bcher-kva.test", "", emptyList())
        assertEquals(headers, ascii.selected("https://XN--BCHER-KVA.test/page"))
        assertEquals(emptyMap(), ascii.selected("https://bücher.test/page"))
    }

    @Test
    fun trust_is_copied_and_malformed_bare_entries_do_not_authorize_endpoints_or_ports() {
        val hosts = mutableListOf("extra.test")
        val policy = SourceHeaderPolicy("https://api.test", "", hosts)
        hosts.clear()
        hosts += "foreign.test"
        assertEquals(headers, policy.selected("https://extra.test/page"))
        assertEquals(emptyMap(), policy.selected("https://foreign.test/page"))

        val invalid = SourceHeaderPolicy(
            "https://api.test", "",
            listOf("foreign.test/path", "foreign.test:443", "https://foreign.test", "foreign.test?query", "foreign.test#fragment", "user@foreign.test"),
        )
        assertEquals(emptyMap(), invalid.selected("https://foreign.test/page"))
    }

    @Test
    fun ip_origins_compare_literals_not_resolution_compression_or_mapped_aliases() {
        val policy = SourceHeaderPolicy("http://127.0.0.1:8080", "https://[2001:DB8::1]", listOf("192.0.2.1"))
        assertEquals(headers, policy.selected("http://127.0.0.1:8080/page"))
        assertEquals(headers, policy.selected("https://[2001:db8::1]:443/page"))
        assertEquals(headers, policy.selected("https://192.0.2.1/page"))
        assertEquals(emptyMap(), policy.selected("http://localhost:8080/page"))
        assertEquals(emptyMap(), policy.selected("https://[2001:db8:0:0:0:0:0:1]/page"))
        assertEquals(emptyMap(), policy.selected("https://[::ffff:192.0.2.1]/page"))
    }
}
