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
import kotlin.test.assertNull
import kotlin.test.assertTrue
import kotlin.test.fail

/** End-to-end golden test of the generic engine over an HTML-scraped source (Ksoup extraction). */
class GenericSourceClientHtmlGoldenTest {

    private fun htmlSource(): SourceConfig {
        val document = (SourceConfigParser.parse(GOLDEN_CONFIG_JSON) as SourceEngineResult.Success).value
        return document.sources.first { it.api == "example-html" }
    }

    private fun client() = GenericSourceEngine(htmlSource(), FakeHttpExecutor(HTML_RESPONSES), FakeHeaderStore())

    private fun <T> SourceEngineResult<T>.valueOrFail(): T = when (this) {
        is SourceEngineResult.Success -> value
        is SourceEngineResult.Failure -> fail("expected success, got $error")
    }

    @Test
    fun home_scrapes_items_and_absolutises_relative_links() = runTest {
        val list = client().home(1).valueOrFail()
        assertEquals(2, list.size)
        val op = list[0]
        assertEquals("One Piece", op.title)
        assertEquals("https://html.example.com/manga/op", op.url) // relative href absolutised
        assertEquals("https://html.example.com/covers/op.jpg", op.coverUrl) // abs:src
        assertNull(op.rating) // no item.rating field for this source
    }

    @Test
    fun details_scrapes_scalars_genres_and_embedded_chapter_list() = runTest {
        val manga = SourceMangaRef("example-html", "en", "One Piece", "https://html.example.com/manga/op", "")
        val details = client().details(manga).valueOrFail()
        assertEquals("One Piece", details.title)
        assertEquals("Pirates adventure", details.description) // trim collapses surrounding whitespace
        assertEquals("Oda", details.author)
        assertEquals("Ongoing", details.status)
        assertEquals("9.2", details.rating)
        assertEquals(listOf("Action", "Adventure"), details.genres)
        assertEquals(2, details.chapters.size)

        val ch1 = details.chapters[0]
        assertEquals("1", ch1.number)
        assertEquals("Romance Dawn", ch1.name)
        assertEquals("https://html.example.com/manga/op/1", ch1.url)
        assertEquals(LocalDate(2024, 1, 15), ch1.date) // iso date
    }

    @Test
    fun pages_scrapes_image_elements() = runTest {
        val manga = SourceMangaRef("example-html", "en", "One Piece", "https://html.example.com/manga/op", "")
        val chapter = client().details(manga).valueOrFail().chapters.first()
        val pages = client().pages(manga, chapter).valueOrFail()
        assertEquals(2, pages.size)
        assertEquals("https://html.example.com/op/1/1.webp", pages[0].url)
        assertEquals("https://html.example.com/op/1/2.webp", pages[1].url)
    }

    @Test
    fun unmapped_url_surfaces_as_http_failure() = runTest {
        // details for a manga whose URL isn't in the fixture map -> 404 -> Failure
        val manga = SourceMangaRef("example-html", "en", "Ghost", "https://html.example.com/manga/ghost", "")
        val result = client().details(manga)
        assertTrue(result is SourceEngineResult.Failure)
    }
}
