package me.manga.kira.source.engine

import kotlinx.coroutines.test.runTest
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
import me.manga.kira.source.contracts.model.SourceConfig
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import kotlin.test.fail

/** Synthetic HTML image-path goldens; no provider I/O or production payloads. */
class GenericSourceEngineImagePathTest {
    private val static = mapOf("Referer" to "fixture-static", "X-Static" to "fixture-value")
    private val captured = mapOf("Cookie" to "fixture-cookie", "Authorization" to "fixture-auth")
    private val merged = static + captured
    private val manga = SourceMangaRef("example-html", "en", "Fixture", "https://html.example.com/manga/1", "")
    private val chapter = SourceChapter("1", "Fixture", "https://html.example.com/chapter/1", null)

    private class RecordingTransport(private val respond: suspend (SourceRequest) -> SourceResponse) : SourceTransport {
        val requests = mutableListOf<SourceRequest>()
        override suspend fun execute(request: SourceRequest): SourceResponse {
            requests += request
            return respond(request)
        }
    }

    private fun config(): SourceConfig {
        val original = (SourceConfigParser.parse(GOLDEN_CONFIG_JSON) as SourceEngineResult.Success).value.sources
            .single { it.api == "example-html" }
        return original.copy(
            headers = static,
            usesCapturedHeaders = true,
            endpoints = original.endpoints + ("pages" to original.endpoints.getValue("pages").copy(
                listSelector = "img.page:not([src*='free_ads.jpg'])",
            )),
        )
    }

    private fun <T> SourceEngineResult<T>.valueOrFail(): T = when (this) {
        is SourceEngineResult.Success -> value
        is SourceEngineResult.Failure -> fail("Expected synthetic fixture success, got $error")
    }

    @Test
    fun html_abs_src_spaces_preserve_order_ad_filter_and_whole_map_confinement() = runTest {
        val http = RecordingTransport { SourceResponse(200, IMAGE_PATH_HTML_PAGES) }
        var reads = 0
        val pages = GenericSourceEngine(config(), http, SourceHeaderProvider { reads++; captured })
            .pages(manga, chapter).valueOrFail()
        assertEquals(
            listOf(
                SourcePage("https://foreign.test/Series%20Name/1/1.jpg?sig=a%2Fb+%20#part%20"),
                SourcePage("https://html.example.com/Series%20Name/1/2.jpg", merged),
                SourcePage("https://foreign.test/Series%20Name/1/3.jpg"),
                SourcePage("https://foreign.test/Series%20Name/1/4.jpg"),
            ),
            pages,
        )
        assertEquals(listOf(chapter.url), http.requests.map { it.url })
        assertEquals(merged, http.requests.single().headers)
        assertEquals(1, reads)
    }

    @Test
    fun raw_src_non_path_whitespace_and_controls_reaching_the_guard_still_reject() = runTest {
        // Raw src avoids claiming that Ksoup's abs:src preserves every original DOM spelling.
        val invalid = listOf(
            "https://bad host.test/Series Name/x.jpg",
            "https://user@html.example.com/Series Name/x.jpg",
            "https://foreign.test/Series Name/x.jpg?part=raw space",
            "https://foreign.test/Series Name/x.jpg#raw space",
            "https://foreign.test/Series\tName/x.jpg",
            " https://foreign.test/Series Name/x.jpg",
            "https://foreign.test/Series Name/x.jpg ",
            "https://foreign.test/Series Name/%GG.jpg",
        )
        val body = (invalid + "https://foreign.test/Series Name/ok.jpg")
            .joinToString("") { """<img class="page" src="$it">""" }
        val original = config()
        val source = original.copy(fields = original.fields + ("page.image" to FieldSpec(attr = "src")))
        val http = RecordingTransport { SourceResponse(200, body) }
        val pages = GenericSourceEngine(source, http, FakeHeaderStore(captured)).pages(manga, chapter).valueOrFail()
        assertEquals(listOf(SourcePage("https://foreign.test/Series%20Name/ok.jpg")), pages)
    }

    @Test
    fun json_and_script_json_images_keep_strict_path_spelling() = runTest {
        val json = """{"pages":["https://foreign.test/Series Name/1.jpg","https://foreign.test/Series%20Name/2.jpg"]}"""
        val original = config()
        for (format in listOf("json", "script-json")) {
            val source = original.copy(
                endpoints = original.endpoints + ("pages" to EndpointSpec("{chapterUrl}", format = format, root = "pages")),
                fields = original.fields + ("page.image" to FieldSpec(path = "")),
            )
            val body = if (format == "json") json else """<script id="__NEXT_DATA__" type="application/json">$json</script>"""
            val http = RecordingTransport { SourceResponse(200, body) }
            val pages = GenericSourceEngine(source, http, FakeHeaderStore(captured)).pages(manga, chapter).valueOrFail()
            assertEquals(listOf(SourcePage("https://foreign.test/Series%20Name/2.jpg")), pages)
        }
    }

    @Test
    fun html_fields_with_templates_do_not_gain_path_space_accommodation() = runTest {
        val original = config()
        val source = original.copy(fields = original.fields + ("page.image" to FieldSpec(
            attr = "src", template = "https://foreign.test/Series Name/template.jpg",
        )))
        for (hasPrimary in listOf(false, true)) {
            val body = if (hasPrimary) """<img class="page" src="https://foreign.test/Series Name/primary.jpg">"""
                else """<img class="page">"""
            val http = RecordingTransport { SourceResponse(200, body) }
            assertEquals(
                SourceEngineResult.Failure(SourceEngineError.InvalidResponse),
                GenericSourceEngine(source, http, FakeHeaderStore(captured)).pages(manga, chapter),
            )
        }
    }

    @Test
    fun authored_requests_and_redirect_locations_are_not_image_normalized() = runTest {
        val original = config()
        val rawUrl = "https://html.example.com/chapter 1"
        val noSend = RecordingTransport { fail("Unsafe initial URL reached transport") }
        val authored = original.copy(endpoints = original.endpoints + ("pages" to
            original.endpoints.getValue("pages").copy(url = rawUrl)))
        val failure = SourceEngineResult.Failure(SourceEngineError.InvalidResponse)
        assertEquals(failure, GenericSourceEngine(authored, noSend, FakeHeaderStore(captured)).pages(manga, chapter))
        assertEquals(failure, GenericSourceEngine(original, noSend, FakeHeaderStore(captured))
            .pages(manga, chapter.copy(url = rawUrl)))
        assertTrue(noSend.requests.isEmpty())

        val redirect = RecordingTransport { SourceResponse(302, "", mapOf("Location" to "/chapter 2")) }
        assertEquals(failure, GenericSourceEngine(original, redirect, FakeHeaderStore(captured)).pages(manga, chapter))
        assertEquals(listOf(chapter.url), redirect.requests.map { it.url })
        assertEquals(merged, redirect.requests.single().headers)
    }
}

private const val IMAGE_PATH_HTML_PAGES = """
<html><body>
<img class="page" src="/img/free_ads.jpg">
<img class="page" src="https://foreign.test/Series Name/1/1.jpg?sig=a%2Fb+%20#part%20">
<img class="page" src="/Series Name/1/2.jpg">
<img class="page" src="//foreign.test/Series Name/1/3.jpg">
<img class="page" src="https://foreign.test/Series%20Name/1/4.jpg">
<img class="page" src="https://user@html.example.com/Series Name/unsafe.jpg">
<img class="page" src="https://foreign.test/Series Name/unsafe.jpg?part=raw space">
<img class="page">
</body></html>
"""
