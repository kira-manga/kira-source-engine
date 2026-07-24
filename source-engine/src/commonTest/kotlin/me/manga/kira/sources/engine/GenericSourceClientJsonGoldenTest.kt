package me.manga.kira.source.engine

import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import kotlinx.datetime.LocalDate
import me.manga.kira.source.contracts.SourceEngineResult
import me.manga.kira.source.contracts.SourceMangaRef
import me.manga.kira.source.contracts.SourceConfigParser
import me.manga.kira.source.contracts.model.SourceConfig
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertTrue
import kotlin.test.fail

/** End-to-end golden test of the generic engine over a JSON-API source: parse config → validate → run. */
class GenericSourceClientJsonGoldenTest {

    private fun jsonSource(): SourceConfig {
        val document = (SourceConfigParser.parse(GOLDEN_CONFIG_JSON) as SourceEngineResult.Success).value
        val validation = DefaultSourceConfigValidator(DefaultStrategyRegistry()).validate(document)
        assertTrue(validation.isValid, "config invalid: ${validation.errors}")
        return document.sources.first { it.api == "example-json" }
    }

    private fun client(headers: Map<String, String> = emptyMap()) = GenericSourceEngine(
        config = jsonSource(),
        http = FakeHttpExecutor(JSON_RESPONSES),
        headerStore = FakeHeaderStore(headers),
    )

    private fun <T> SourceEngineResult<T>.valueOrFail(): T = when (this) {
        is SourceEngineResult.Success -> value
        is SourceEngineResult.Failure -> fail("expected success, got $error")
    }

    @Test
    fun home_maps_json_items_to_domain_manga() = runTest {
        val list = client().home(1).valueOrFail()
        assertEquals(2, list.size)
        val op = list[0]
        assertEquals("example-json", op.api)
        assertEquals("en", op.language)
        assertEquals("One Piece", op.title)
        assertEquals("https://api.example.com/one-piece", op.url)
        assertEquals("https://img.example.com/covers/op.jpg", op.coverUrl)
        assertEquals(9, op.rating)
        // already-absolute cover passes through untouched
        assertEquals("https://img.example.com/covers/naruto.jpg", list[1].coverUrl)
    }

    @Test
    fun search_url_encodes_query() = runTest {
        val http = FakeHttpExecutor(JSON_RESPONSES)
        val client = GenericSourceEngine(jsonSource(), http, FakeHeaderStore())
        val list = client.search("one piece", 1).valueOrFail()
        assertEquals(2, list.size)
        assertTrue(http.requested.any { it == "https://api.example.com/search?q=one%20piece&page=1" })
    }

    @Test
    fun details_maps_scalars_chapters_and_dates() = runTest {
        val manga = SourceMangaRef("example-json", "en", "One Piece", "https://api.example.com/one-piece", "")
        val details = client().details(manga).valueOrFail()
        assertEquals("One Piece", details.title)
        assertEquals("Pirates adventure", details.description) // strip-html + trim
        assertEquals("Oda", details.author)
        assertEquals("Ongoing", details.status) // enum-map "1" -> "Ongoing"
        assertEquals("9.2", details.rating)
        assertEquals(listOf("Action", "Adventure"), details.genres)
        assertEquals(2, details.chapters.size)

        val ch1 = details.chapters[0]
        assertEquals("1", ch1.number)
        assertEquals("Romance Dawn", ch1.name)
        assertEquals("https://api.example.com/one-piece/1", ch1.url)
        assertEquals(LocalDate(2023, 11, 14), ch1.date) // epoch-seconds 1700000000
    }

    @Test
    fun pages_extracts_image_urls_and_injects_headers() = runTest {
        val chapter = details_first_chapter()
        val manga = SourceMangaRef("example-json", "en", "One Piece", "https://api.example.com/one-piece", "")
        val pages = client(headers = mapOf("Referer" to "https://api.example.com"))
            .pages(manga, chapter).valueOrFail()
        assertEquals(2, pages.size)
        assertEquals("https://img.example.com/op/1/1.webp", pages[0].url)
        assertEquals("https://api.example.com", pages[0].headers["Referer"])
    }

    private suspend fun details_first_chapter() = client()
        .details(SourceMangaRef("example-json", "en", "One Piece", "https://api.example.com/one-piece", ""))
        .valueOrFail().chapters.first().also { assertNotNull(it) }
}
