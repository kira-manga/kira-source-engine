package me.manga.kira.source.engine

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import me.manga.kira.source.contracts.SourceBaseUrlProvider
import me.manga.kira.source.contracts.SourceChapter
import me.manga.kira.source.contracts.SourceConfigParser
import me.manga.kira.source.contracts.SourceEngineError
import me.manga.kira.source.contracts.SourceEngineResult
import me.manga.kira.source.contracts.SourceHeaderProvider
import me.manga.kira.source.contracts.SourceMangaRef
import me.manga.kira.source.contracts.SourcePage
import me.manga.kira.source.contracts.SourceRequest
import me.manga.kira.source.contracts.SourceResponse
import me.manga.kira.source.contracts.SourceTransport
import me.manga.kira.source.contracts.model.EndpointSpec
import me.manga.kira.source.contracts.model.FieldSpec
import me.manga.kira.source.contracts.model.FilterDefinition
import me.manga.kira.source.contracts.model.FilterRequestSpec
import me.manga.kira.source.contracts.model.SourceConfig
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import kotlin.test.fail

/** Synthetic request/page goldens. No native networking, captured production values or source I/O. */
class GenericSourceEngineCredentialTest {
    private val static = mapOf("Referer" to "fixture-static", "User-Agent" to "fixture-agent", "X-Precedence" to "static")
    private val captured = mapOf("cOoKiE" to "fixture-cookie", "aUtHoRiZaTiOn" to "fixture-auth", "x-arbitrary" to "fixture-value", "X-Precedence" to "captured")
    private val merged = static + captured

    private class RecordingTransport(private val respond: suspend (SourceRequest) -> SourceResponse) : SourceTransport {
        val requests = mutableListOf<SourceRequest>()
        override suspend fun execute(request: SourceRequest): SourceResponse {
            requests += request
            return respond(request)
        }
    }

    private fun config() = SourceConfig(
        api = "credential-fixture", language = "en", engine = "generic",
        baseUrl = "https://api.test", imageBase = "https://img.test:8443",
        headers = static, trustedHosts = listOf("cdn.api.test"),
        previousHosts = listOf("old.api.test"), previousImageHosts = listOf("old.img.test"),
        endpoints = mapOf(
            "home" to EndpointSpec("{baseUrl}/home", root = "items"),
            "featured" to EndpointSpec("{baseUrl}/featured", root = "items"),
            "search" to EndpointSpec("{baseUrl}/search?q={queryEncoded}", root = "items"),
            "details" to EndpointSpec("{itemUrl}"),
            "pages" to EndpointSpec("{chapterUrl}", root = "pages"),
        ),
        fields = mapOf(
            "item.title" to FieldSpec(path = "title"), "item.url" to FieldSpec(path = "url"),
            "detail.title" to FieldSpec(path = "title"), "detail.chapters" to FieldSpec(listPath = "chapters"),
            "chapter.number" to FieldSpec(path = "number"), "chapter.url" to FieldSpec(path = "url"),
            "page.image" to FieldSpec(path = ""),
        ),
    )

    private fun manga(url: String = "https://api.test/manga/1") = SourceMangaRef("credential-fixture", "en", "Fixture", url, "")
    private fun chapter(url: String = "https://api.test/chapter/1") = SourceChapter("1", "Fixture", url, null)
    private fun pageBody(urls: List<String>) = """{"pages":${JsonArray(urls.map(::JsonPrimitive))}}"""

    private fun <T> SourceEngineResult<T>.valueOrFail(): T = when (this) {
        is SourceEngineResult.Success -> value
        is SourceEngineResult.Failure -> fail("Expected synthetic fixture success, got $error")
    }

    @Test
    fun initial_foreign_details_and_pages_requests_withhold_every_source_header() = runTest {
        val http = RecordingTransport { SourceResponse(200, """{"title":"Fixture","pages":[]}""") }
        var reads = 0
        val engine = GenericSourceEngine(config(), http, SourceHeaderProvider { reads++; captured })
        engine.details(manga("https://foreign.test/manga/1")).valueOrFail()
        engine.pages(manga(), chapter("https://foreign.test/chapter/1")).valueOrFail()
        assertEquals(listOf("https://foreign.test/manga/1", "https://foreign.test/chapter/1"), http.requests.map { it.url })
        assertTrue(http.requests.all { it.headers.isEmpty() })
        assertEquals(2, reads)
    }

    @Test
    fun filters_merge_in_existing_precedence_then_the_entire_map_is_destination_filtered() = runTest {
        val filters = listOf(
            FilterDefinition("precedence", "Fixture", "text", default = "filter", request = FilterRequestSpec("header", "X-Precedence")),
            FilterDefinition("custom", "Fixture", "text", default = "filter-only", request = FilterRequestSpec("header", "X-Filter-Only")),
            FilterDefinition("query", "Fixture", "text", default = "a b", request = FilterRequestSpec("query", "extra")),
        )
        val source = config().copy(filters = filters)
        val trustedHttp = RecordingTransport { SourceResponse(200, """{"items":[]}""") }
        GenericSourceEngine(source, trustedHttp, FakeHeaderStore(captured)).search("fixture", 1).valueOrFail()
        val expected = merged + mapOf("X-Precedence" to "filter", "X-Filter-Only" to "filter-only")
        assertEquals(expected, trustedHttp.requests.single().headers)
        assertEquals("https://api.test/search?q=fixture&extra=a%20b", trustedHttp.requests.single().url)

        val foreignHttp = RecordingTransport { SourceResponse(200, """{"items":[]}""") }
        val foreign = source.copy(endpoints = source.endpoints + ("search" to EndpointSpec("https://foreign.test/search?q={queryEncoded}", root = "items")))
        GenericSourceEngine(foreign, foreignHttp, FakeHeaderStore(captured)).search("fixture", 1).valueOrFail()
        assertEquals("https://foreign.test/search?q=fixture&extra=a%20b", foreignHttp.requests.single().url)
        assertEquals(emptyMap(), foreignHttp.requests.single().headers)
    }

    @Test
    fun mixed_json_pages_keep_public_foreign_images_anonymous_and_omit_unsafe_destinations() = runTest {
        val trusted = listOf("https://api.test/1.webp", "https://img.test:8443/2.webp", "HTTPS://CDN.API.TEST:443/3.webp")
        val foreign = listOf(
            "https://foreign.test/4.webp", "https://img.test/5.webp", "https://sub.api.test/6.webp",
            "https://api.test.evil.test/7.webp", "https://old.api.test/8.webp", "https://old.img.test/9.webp",
            "http://api.test/10.webp", "https://api.test./11.webp", "https://bücher.test/12.webp",
        )
        val unsafe = listOf(
            "https://user@api.test/13.webp", "https://api.test:0/14.webp", "https://api.test\\@foreign.test/15.webp",
            "https://%61pi.test/16.webp", "https://api.test/17.webp\n", "data:image/png,fixture", "javascript:fixture",
        )
        val http = RecordingTransport { SourceResponse(200, pageBody(trusted + foreign + unsafe)) }
        var reads = 0
        val pages = GenericSourceEngine(config(), http, SourceHeaderProvider { reads++; captured })
            .pages(manga(), chapter("https://foreign.test/chapter/1")).valueOrFail()
        assertEquals(trusted.map { SourcePage(it, merged) } + foreign.map { SourcePage(it) }, pages)
        assertEquals(emptyMap(), http.requests.single().headers)
        assertEquals(1, reads)
    }

    @Test
    fun html_golden_pages_keep_existing_relative_base_and_confine_headers_per_extracted_image() = runTest {
        val source = (SourceConfigParser.parse(GOLDEN_CONFIG_JSON) as SourceEngineResult.Success).value.sources
            .single { it.api == "example-html" }.copy(headers = static)
        val http = RecordingTransport {
            SourceResponse(200, """
                <html><body>
                  <img class="page" src="/op/1/1.webp">
                  <img class="page" src="https://foreign.test/op/1/2.webp">
                  <img class="page" src="https://user@foreign.test/op/1/3.webp">
                </body></html>
            """.trimIndent())
        }
        val pages = GenericSourceEngine(source, http, FakeHeaderStore(captured))
            .pages(manga(), chapter("https://html.example.com/manga/op/1")).valueOrFail()
        assertEquals(
            listOf(SourcePage("https://html.example.com/op/1/1.webp", merged), SourcePage("https://foreign.test/op/1/2.webp")),
            pages,
        )
        assertEquals(merged, http.requests.single().headers)
    }

    @Test
    fun separate_paginated_chapters_and_their_extracted_foreign_urls_do_not_gain_authority() = runTest {
        val original = config()
        val source = original.copy(endpoints = original.endpoints + mapOf(
            "details" to EndpointSpec("{baseUrl}/manga/1"),
            "chapters" to EndpointSpec("{itemUrl}/chapters?page={page}", root = "chapters", pageParam = "page", lastPageLocator = "has_next"),
        ))
        val http = RecordingTransport { request ->
            when (request.url) {
                "https://api.test/manga/1" -> SourceResponse(200, """{"title":"Fixture"}""")
                "https://foreign.test/manga/1/chapters?page=1" -> SourceResponse(
                    200, """{"chapters":[{"number":"1","url":"https://foreign.test/chapter/1"}],"has_next":true}""",
                )
                "https://foreign.test/manga/1/chapters?page=2" -> SourceResponse(
                    200, """{"chapters":[{"number":"2","url":"https://api.test/chapter/2"}],"has_next":false}""",
                )
                "https://foreign.test/chapter/1" -> SourceResponse(200, pageBody(listOf("https://img.test:8443/1.webp", "https://foreign.test/2.webp")))
                else -> fail("Unexpected synthetic destination")
            }
        }
        val engine = GenericSourceEngine(source, http, FakeHeaderStore(captured))
        val ref = manga("https://foreign.test/manga/1")
        val details = engine.details(ref).valueOrFail()
        assertEquals(listOf("1", "2"), details.chapters.map { it.number })
        assertEquals("https://foreign.test/chapter/1", details.chapters.first().url)
        val pages = engine.pages(ref, details.chapters.first()).valueOrFail()
        assertEquals(listOf(SourcePage("https://img.test:8443/1.webp", merged), SourcePage("https://foreign.test/2.webp")), pages)
        assertEquals(listOf(merged, emptyMap(), emptyMap(), emptyMap()), http.requests.map { it.headers })
    }

    @Test
    fun captured_header_opt_out_skips_storage_but_does_not_exempt_static_headers() = runTest {
        val http = RecordingTransport { SourceResponse(200, """{"items":[],"pages":["https://img.test:8443/1.webp","https://foreign.test/2.webp"]}""") }
        val engine = GenericSourceEngine(config().copy(usesCapturedHeaders = false), http, SourceHeaderProvider { fail("Storage must not be read") })
        engine.home(1).valueOrFail()
        engine.details(manga("https://foreign.test/manga/1")).valueOrFail()
        val pages = engine.pages(manga(), chapter()).valueOrFail()
        assertEquals(listOf(static, emptyMap(), static), http.requests.map { it.headers })
        assertEquals(listOf(SourcePage("https://img.test:8443/1.webp", static), SourcePage("https://foreign.test/2.webp")), pages)
    }

    @Test
    fun static_map_and_trust_list_are_frozen_at_engine_construction() = runTest {
        val mutableHeaders = static.toMutableMap()
        val mutableTrust = mutableListOf("original-extra.test")
        val source = config().copy(headers = mutableHeaders, trustedHosts = mutableTrust, usesCapturedHeaders = false)
        val http = RecordingTransport { SourceResponse(200, "{}") }
        val engine = GenericSourceEngine(source, http, SourceHeaderProvider { fail("Storage must not be read") })
        mutableHeaders["Referer"] = "mutated"
        mutableHeaders["X-Later"] = "mutated"
        mutableTrust.clear()
        mutableTrust += "later-extra.test"
        engine.details(manga("https://original-extra.test/manga/1")).valueOrFail()
        engine.details(manga("https://later-extra.test/manga/1")).valueOrFail()
        engine.details(manga()).valueOrFail()
        assertEquals(listOf(static, emptyMap(), static), http.requests.map { it.headers })
    }

    @Test
    fun a_suspended_pages_request_and_its_images_share_one_detached_provider_snapshot() = runTest {
        val providerMap = captured.toMutableMap()
        val started = CompletableDeferred<Unit>()
        val resume = CompletableDeferred<Unit>()
        var reads = 0
        val http = RecordingTransport {
            started.complete(Unit)
            resume.await()
            SourceResponse(200, pageBody(listOf("https://img.test:8443/1.webp", "https://foreign.test/2.webp")))
        }
        val engine = GenericSourceEngine(config(), http, SourceHeaderProvider { reads++; providerMap })
        val operation = async { engine.pages(manga(), chapter()) }
        started.await()
        providerMap.clear()
        providerMap["X-Later"] = "mutated"
        resume.complete(Unit)
        val pages = operation.await().valueOrFail()
        assertEquals(merged, http.requests.single().headers)
        assertEquals(listOf(SourcePage("https://img.test:8443/1.webp", merged), SourcePage("https://foreign.test/2.webp")), pages)
        assertEquals(1, reads)
    }

    @Test
    fun pages_redirects_and_exported_images_reuse_the_same_snapshot_after_storage_changes() = runTest {
        val providerMap = captured.toMutableMap()
        var reads = 0
        val http = RecordingTransport { request ->
            if (request.url == "https://api.test/chapter/1") {
                providerMap.clear()
                providerMap["X-Later"] = "mutated"
                SourceResponse(302, "", mapOf("Location" to "https://foreign.test/pages"))
            } else {
                SourceResponse(200, pageBody(listOf("https://img.test:8443/1.webp", "https://foreign.test/2.webp")))
            }
        }
        val pages = GenericSourceEngine(config(), http, SourceHeaderProvider { reads++; providerMap })
            .pages(manga(), chapter()).valueOrFail()
        assertEquals(listOf("https://api.test/chapter/1", "https://foreign.test/pages"), http.requests.map { it.url })
        assertEquals(listOf(merged, emptyMap()), http.requests.map { it.headers })
        assertEquals(listOf(SourcePage("https://img.test:8443/1.webp", merged), SourcePage("https://foreign.test/2.webp")), pages)
        assertEquals(1, reads)
    }

    @Test
    fun interleaved_verbs_cannot_turn_a_user_mirror_or_another_header_read_into_credential_authority() = runTest {
        var liveBase = "https://api.test"
        val providerMap = captured.toMutableMap()
        val started = CompletableDeferred<Unit>()
        val resume = CompletableDeferred<Unit>()
        var reads = 0
        val http = RecordingTransport { request ->
            if (request.url == "https://api.test/chapter/1") {
                started.complete(Unit)
                resume.await()
                SourceResponse(200, pageBody(listOf("relative.webp", "https://api.test/fixed.webp", "https://mirror.test/public.webp")))
            } else {
                SourceResponse(200, """{"items":[]}""")
            }
        }
        val source = config().copy(imageBase = "")
        val engine = GenericSourceEngine(source, http, SourceHeaderProvider { reads++; providerMap }, baseUrlProvider = SourceBaseUrlProvider { liveBase })
        val pending = async { engine.pages(manga(), chapter()) }
        started.await()
        liveBase = "https://mirror.test"
        providerMap.clear()
        providerMap["X-Later"] = "mutated"
        engine.home(1).valueOrFail()
        resume.complete(Unit)
        val pages = pending.await().valueOrFail()
        assertEquals(listOf("https://api.test/chapter/1", "https://mirror.test/home"), http.requests.map { it.url })
        assertEquals(listOf(merged, emptyMap()), http.requests.map { it.headers })
        assertEquals(3, pages.size)
        // Assert confinement on the *actual* emitted URL, not a refactor of existing live-base semantics.
        for (page in pages) assertEquals(if (page.url.startsWith("https://api.test/")) merged else emptyMap(), page.headers)
        assertEquals(merged, pages.single { it.url == "https://api.test/fixed.webp" }.headers)
        assertEquals(2, reads)
    }

    @Test
    fun malformed_initial_urls_fail_before_the_transport_for_both_response_controlled_verbs() = runTest {
        val urls = listOf(
            "https://user@api.test/manga/1", "https://api.test\\@foreign.test/manga/1", "https://%61pi.test/manga/1",
            " https://api.test/manga/1", "https://api.test/manga/1\n", "https://api.test:0/manga/1",
            "https://api.test:+443/manga/1", "https://api.test:65536/manga/1", "https://127.1/manga/1",
            "https://[fe80::1%25en0]/manga/1", "https:///api.test/manga/1", "javascript:fixture",
        )
        val http = RecordingTransport { fail("Invalid URL reached the transport") }
        val engine = GenericSourceEngine(config(), http, FakeHeaderStore(captured))
        for (url in urls) {
            assertEquals(SourceEngineResult.Failure(SourceEngineError.InvalidResponse), engine.details(manga(url)))
            assertEquals(SourceEngineResult.Failure(SourceEngineError.InvalidResponse), engine.pages(manga(), chapter(url)))
        }
        assertTrue(http.requests.isEmpty())
    }

    @Test
    fun explicit_http_sources_keep_exact_http_request_and_image_permissions() = runTest {
        val http = RecordingTransport { SourceResponse(200, pageBody(listOf("http://images.test/1.webp", "http://foreign.test/2.webp"))) }
        val source = config().copy(baseUrl = "http://api.test", imageBase = "http://images.test")
        val pages = GenericSourceEngine(source, http, FakeHeaderStore(captured))
            .pages(manga(), chapter("http://api.test/chapter/1")).valueOrFail()
        assertEquals(merged, http.requests.single().headers)
        assertEquals(listOf(SourcePage("http://images.test/1.webp", merged), SourcePage("http://foreign.test/2.webp")), pages)

        val secureHttp = RecordingTransport { SourceResponse(200, pageBody(listOf("http://images.test/1.webp"))) }
        val securePages = GenericSourceEngine(source.copy(baseUrl = "https://api.test"), secureHttp, FakeHeaderStore(captured))
            .pages(manga(), chapter()).valueOrFail()
        assertEquals(listOf(SourcePage("http://images.test/1.webp")), securePages)
    }
}
