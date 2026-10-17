package me.manga.kira.source.engine

import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import me.manga.kira.source.contracts.SourceEngineResult
import me.manga.kira.source.contracts.SourceEngineError
import me.manga.kira.source.contracts.SourceChapter
import me.manga.kira.source.contracts.SourceMangaRef
import me.manga.kira.source.contracts.SourceConfigParser
import me.manga.kira.source.contracts.model.SourceConfig
import me.manga.kira.source.contracts.model.SourceConfigDocument
import me.manga.kira.source.contracts.model.EndpointSpec
import me.manga.kira.source.contracts.model.FieldSpec
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

    @Test
    fun inline_chapters_share_the_details_json_island_and_coalesced_path() = runTest {
        val config = validated(source().copy(fields = source().fields + mapOf(
            "detail.chapters" to FieldSpec(listPath = "props.pageProps.absent,props.pageProps.chapters"),
            "chapter.name" to FieldSpec(path = "name"),
            "chapter.url" to FieldSpec(path = "url"),
        )))
        val manga = SourceMangaRef("sj", "en", "x", "https://sj.example.com/x", "")
        val body = island("""{"props":{"pageProps":{"initialManga":{"name":"My Manga"},"chapters":[
            {"name":"Chapter 2","url":"https://sj.example.com/ch2"},
            {"name":"Chapter 1","url":"https://sj.example.com/ch1"}
        ]}}}""")
        val http = FakeHttpExecutor(mapOf(manga.url to body))
        val details = GenericSourceEngine(config, http, FakeHeaderStore()).details(manga).valueOrFail()
        assertEquals("My Manga", details.title)
        assertEquals(listOf("Chapter 2", "Chapter 1"), details.chapters.map { it.name })
        assertEquals(listOf("https://sj.example.com/ch2", "https://sj.example.com/ch1"), details.chapters.map { it.url })
        assertEquals(listOf(manga.url), http.requested)
    }

    @Test
    fun chapter_pagination_reads_continuation_from_the_same_custom_island() = runTest {
        val http = FakeHttpExecutor(paginatedResponses())
        val details = GenericSourceEngine(paginatedSource(), http, FakeHeaderStore())
            .details(paginatedManga).valueOrFail()
        assertEquals(listOf("Chapter 1", "Chapter 2"), details.chapters.map { it.name })
        assertEquals(listOf(paginatedManga.url, chapterPage(1), chapterPage(2)), http.requested)
    }

    @Test
    fun malformed_required_later_island_fails_without_partial_chapters() = runTest {
        val http = FakeHttpExecutor(paginatedResponses() + (chapterPage(2) to island("{", "chapter-data")))
        assertEquals(
            SourceEngineResult.Failure(SourceEngineError.InvalidResponse),
            GenericSourceEngine(paginatedSource(), http, FakeHeaderStore()).details(paginatedManga),
        )
        assertEquals(listOf(paginatedManga.url, chapterPage(1), chapterPage(2)), http.requested)
    }

    private val paginatedManga = SourceMangaRef("sj", "en", "x", "https://sj.example.com/x", "")

    private fun chapterPage(page: Int) = "https://sj.example.com/x/chapters?page=$page"

    private fun paginatedSource(): SourceConfig = source().let { config ->
        validated(config.copy(
            endpoints = config.endpoints + ("chapters" to EndpointSpec(
                url = "{itemUrl}/chapters?page={page}", format = "script-json", scriptId = "chapter-data",
                root = "chapters", pageParam = "page", lastPageLocator = "hasNext",
            )),
            fields = config.fields + mapOf(
                "chapter.name" to FieldSpec(path = "name"),
                "chapter.url" to FieldSpec(path = "url"),
            ),
        ))
    }

    private fun validated(config: SourceConfig): SourceConfig {
        val result = DefaultSourceConfigValidator(DefaultStrategyRegistry())
            .validate(SourceConfigDocument(schemaVersion = 1, sources = listOf(config)))
        assertTrue(result.isValid, "config invalid: ${result.errors}")
        return config
    }

    private fun paginatedResponses(): Map<String, String> = SJ_RESPONSES + mapOf(
        chapterPage(1) to island("""{"chapters":[{"name":"Chapter 1","url":"https://sj.example.com/ch1"}],"hasNext":true}""", "chapter-data"),
        chapterPage(2) to island("""{"chapters":[{"name":"Chapter 2","url":"https://sj.example.com/ch2"}],"hasNext":false}""", "chapter-data"),
    )

    private fun island(json: String, id: String = "__NEXT_DATA__") =
        """<html><body><script id="$id" type="application/json">$json</script></body></html>"""
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
