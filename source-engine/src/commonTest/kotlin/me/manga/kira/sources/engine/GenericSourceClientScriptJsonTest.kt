package me.manga.kira.source.engine

import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import me.manga.kira.source.contracts.SourceEngineResult
import me.manga.kira.source.contracts.SourceChapter
import me.manga.kira.source.contracts.SourceMangaRef
import me.manga.kira.source.contracts.SourceConfigParser
import me.manga.kira.source.contracts.model.SourceConfig
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import kotlin.test.fail

/**
 * Golden test for the `script-json` format (F7): a client-rendered (Next.js/RSC) page whose data lives in
 * a `<script id="__NEXT_DATA__">` JSON island rather than the visible DOM. The engine extracts that JSON
 * and applies `root`/field JSONPaths to it — for details scalars (rootScope) and for the page-image list.
 */
class GenericSourceClientScriptJsonTest {

    private fun source(): SourceConfig {
        val doc = (SourceConfigParser.parse(SJ_CONFIG) as SourceEngineResult.Success).value
        assertTrue(DefaultSourceConfigValidator(DefaultStrategyRegistry()).validate(doc).isValid)
        return doc.sources.first { it.api == "sj" }
    }

    private fun client() = GenericSourceEngine(source(), FakeHttpExecutor(SJ_RESPONSES), FakeHeaderStore())

    private fun <T> SourceEngineResult<T>.valueOrFail(): T = when (this) {
        is SourceEngineResult.Success -> value
        is SourceEngineResult.Failure -> fail("expected success, got $error")
    }

    @Test
    fun details_scalars_from_next_data_island() = runTest {
        val manga = SourceMangaRef("sj", "en", "x", "https://sj.example.com/x", "")
        assertEquals("My Manga", client().details(manga).valueOrFail().title)
    }

    @Test
    fun pages_image_list_from_next_data_island() = runTest {
        val manga = SourceMangaRef("sj", "en", "x", "https://sj.example.com/x", "")
        val chapter = SourceChapter("1", "c", "https://sj.example.com/ch1", null)
        val pages = client().pages(manga, chapter).valueOrFail().map { it.url }
        assertEquals(listOf("https://cdn.example.com/a.webp", "https://cdn.example.com/b.webp"), pages)
    }

    @Test
    fun missing_island_is_failure_not_empty_success() = runTest {
        // A site redesign that removes/renames the island is a structural change: report Failure
        // rather than a wrong-but-Success empty result.
        val responses = mapOf(
            "https://sj.example.com/x" to "<html><body><p>no island here</p></body></html>",
        )
        val client = GenericSourceEngine(source(), FakeHttpExecutor(responses), FakeHeaderStore())
        val manga = SourceMangaRef("sj", "en", "x", "https://sj.example.com/x", "")
        assertTrue(client.details(manga) is SourceEngineResult.Failure)
    }
}

private const val SJ_CONFIG = """
{
  "schemaVersion": 1, "revision": 1,
  "sources": [
    {
      "api": "sj", "language": "en", "baseUrl": "https://sj.example.com", "engine": "generic",
      "pagination": { "type": "page-number", "param": "page", "start": 1 },
      "endpoints": {
        "home":    { "url": "{baseUrl}/home?page={page}", "format": "json", "root": "items" },
        "details": { "url": "{itemUrl}", "format": "script-json" },
        "pages":   { "url": "{chapterUrl}", "format": "script-json", "root": "props.pageProps.initialChapter.images" }
      },
      "fields": {
        "detail.title": { "path": "props.pageProps.initialManga.name" },
        "page.image":   { "path": "" }
      }
    }
  ]
}
"""

private val SJ_RESPONSES: Map<String, String> = mapOf(
    "https://sj.example.com/x" to
        """<html><body><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"initialManga":{"name":"My Manga"}}}}</script></body></html>""",
    "https://sj.example.com/ch1" to
        """<html><body><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"initialChapter":{"images":["https://cdn.example.com/a.webp","https://cdn.example.com/b.webp"]}}}}</script></body></html>""",
)
