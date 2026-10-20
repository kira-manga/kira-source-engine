package me.manga.kira.source.engine

import kotlinx.coroutines.test.runTest
import me.manga.kira.source.contracts.*
import me.manga.kira.source.contracts.model.EndpointSpec
import me.manga.kira.source.contracts.model.FieldSpec
import me.manga.kira.source.contracts.model.SourceConfig
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/** Small public contract fixtures, not captured provider responses. */
class SourceConversionBoundaryTest {
    private val manga = SourceMangaRef("example", "en", "Example", "https://example.test/title/1", "")
    private val chapter = SourceChapter("1", "Chapter 1", "https://example.test/read/1", null)
    private val source = SourceConfig(
        api = "example",
        language = "en",
        baseUrl = "https://example.test",
        engine = "generic",
        usesCapturedHeaders = false,
        endpoints = mapOf(
            "home" to EndpointSpec("{baseUrl}/catalog", format = "html", listSelector = ".card"),
            "pages" to EndpointSpec("{chapterUrl}", format = "html", listSelector = "img.page"),
        ),
        fields = mapOf("page.image" to FieldSpec(attr = "src")),
    )

    @Test
    fun absent_optional_carousel_succeeds_without_touching_providers_or_transport() = runTest {
        val engine = GenericSourceEngine(
            source,
            SourceTransport { error("No carousel request permitted") },
            SourceHeaderProvider { error("No credential lookup permitted") },
            baseUrlProvider = SourceBaseUrlProvider { error("No live-base lookup permitted") },
        )
        for (page in listOf(1, 2)) assertEquals(SourceEngineResult.Success(emptyList()), engine.featured(page))
        val configured = source.copy(endpoints = source.endpoints + ("featured" to source.endpoints.getValue("home")))
        val failing = GenericSourceEngine(configured, SourceTransport { SourceResponse(503, "unavailable") }, SourceHeaderProvider { emptyMap() })
        assertEquals(SourceEngineResult.Failure(SourceEngineError.Http(503)), failing.featured(1))
    }

    @Test
    fun interstitial_and_json_without_usable_images_fail_without_access_actions() = runTest {
        for ((config, body) in listOf(
            source to "<html><title>Age confirmation</title><form action='/confirm'><button>Continue</button></form></html>",
            source.copy(
                endpoints = source.endpoints + ("pages" to EndpointSpec("{chapterUrl}", format = "json", root = "pages")),
                fields = mapOf("page.image" to FieldSpec(path = "$")),
            ) to """{"pages":[]}""",
        )) {
            val requests = mutableListOf<SourceRequest>()
            val engine = GenericSourceEngine(config, SourceTransport { requests += it; SourceResponse(200, body) }, SourceHeaderProvider { emptyMap() })
            assertEquals(SourceEngineResult.Failure(SourceEngineError.InvalidResponse), engine.pages(manga, chapter))
            assertEquals(listOf(chapter.url), requests.map { it.url })
            assertTrue(requests.all { it.method == SourceHttpMethod.GET && it.headers.isEmpty() })
        }
        // Empty catalogue/search pages still represent normal pagination exhaustion.
        val engine = GenericSourceEngine(source, SourceTransport { SourceResponse(200, "<html></html>") }, SourceHeaderProvider { emptyMap() })
        assertEquals(SourceEngineResult.Success(emptyList()), engine.home(2))
    }
}
