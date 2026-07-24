package me.manga.kira.source.engine

import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import me.manga.kira.source.contracts.SourceEngineResult
import me.manga.kira.source.contracts.SourceChapter
import me.manga.kira.source.contracts.SourceMangaRef
import me.manga.kira.source.contracts.SourceTransport
import me.manga.kira.source.contracts.SourceConfigParser
import me.manga.kira.source.contracts.SourceRequest
import me.manga.kira.source.contracts.SourceResponse
import me.manga.kira.source.contracts.model.SourceConfig
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import kotlin.test.fail

/**
 * Golden tests for the DilarV2-class engine features:
 *  - F1 POST_JSON body template (sent as the request body),
 *  - F2 JSON list-item filters (drop novels / deleted entries),
 *  - F3 root-level template var `{root:path}` (page image needs a sibling of the page array),
 *  - F4 conditional list-root coalesce (`webp_pages,pages`) + the `{root:__dir}` token.
 */
class GenericSourceClientPostJsonFilterRootTest {

    private fun source(): SourceConfig {
        val doc = (SourceConfigParser.parse(FEAT_CONFIG) as SourceEngineResult.Success).value
        val v = DefaultSourceConfigValidator(DefaultStrategyRegistry()).validate(doc)
        assertTrue(v.isValid, "config invalid: ${v.errors}")
        return doc.sources.first { it.api == "feat-json" }
    }

    private fun <T> SourceEngineResult<T>.valueOrFail(): T = when (this) {
        is SourceEngineResult.Success -> value
        is SourceEngineResult.Failure -> fail("expected success, got $error")
    }

    @Test
    fun post_json_body_is_sent_and_list_is_filtered() = runTest {
        val http = RecordingHttp(FEAT_RESPONSES)
        val results = GenericSourceEngine(source(), http, FakeHeaderStore()).search("naruto", 1).valueOrFail()
        // F1: the templated JSON body was sent verbatim
        assertEquals("""{"query":"naruto","includes":["Manga"]}""", http.lastJsonBody)
        // F2: "A Novel" (series_type.name==Novel, exclude) and "Deleted" (deleted_at!=null, include-isNull) dropped
        assertEquals(listOf("Naruto"), results.map { it.title })
    }

    @Test
    fun pages_use_root_storage_key_and_pages_dir_when_webp_empty() = runTest {
        val manga = SourceMangaRef("feat-json", "en", "x", "https://f.example.com/series/1", "")
        val chapter = SourceChapter("1", "c1", "https://f.example.com/ch-jpg", null)
        val pages = GenericSourceEngine(source(), RecordingHttp(FEAT_RESPONSES), FakeHeaderStore())
            .pages(manga, chapter).valueOrFail().map { it.url }
        // webp_pages empty -> coalesce picks `pages`, dir token `hq`; {root:storage_key} = SK
        assertEquals(
            listOf(
                "https://img.example.com/releases/SK/hq/a.jpg",
                "https://img.example.com/releases/SK/hq/b.jpg",
            ),
            pages,
        )
    }

    @Test
    fun pages_use_webp_dir_when_webp_present() = runTest {
        val manga = SourceMangaRef("feat-json", "en", "x", "https://f.example.com/series/1", "")
        val chapter = SourceChapter("2", "c2", "https://f.example.com/ch-webp", null)
        val pages = GenericSourceEngine(source(), RecordingHttp(FEAT_RESPONSES), FakeHeaderStore())
            .pages(manga, chapter).valueOrFail().map { it.url }
        // webp_pages non-empty -> coalesce picks `webp_pages`, dir token `hq_webp`
        assertEquals(listOf("https://img.example.com/releases/SK2/hq_webp/w1.webp"), pages)
    }
}

private class RecordingHttp(private val responses: Map<String, String>) : SourceTransport {
    var lastJsonBody: String? = null
    override suspend fun execute(request: SourceRequest): SourceResponse {
        if (request.jsonBody != null) lastJsonBody = request.jsonBody
        return responses[request.url]?.let { SourceResponse(200, it) } ?: SourceResponse(404, "missing: ${request.url}")
    }
}

private const val FEAT_CONFIG = """
{
  "schemaVersion": 1, "revision": 1,
  "sources": [
    {
      "api": "feat-json", "language": "en",
      "baseUrl": "https://f.example.com", "imageBase": "https://img.example.com",
      "engine": "generic",
      "pagination": { "type": "page-number", "param": "page", "start": 1 },
      "endpoints": {
        "home":   { "url": "{baseUrl}/home?page={page}", "format": "json", "root": "items" },
        "search": { "url": "{baseUrl}/search", "method": "post-json", "format": "json", "root": "[*].data[*]",
          "jsonBody": "{\"query\":\"{queryEncoded}\",\"includes\":[\"Manga\"]}",
          "listFilters": [
            { "path": "series_type.name", "op": "equals", "value": "Novel", "mode": "exclude" },
            { "path": "deleted_at", "op": "isNull", "mode": "include" }
          ] },
        "pages":  { "url": "{chapterUrl}", "format": "json", "root": "webp_pages,pages", "rootDirs": [ "hq_webp", "hq" ] }
      },
      "fields": {
        "item.title": { "path": "title" },
        "item.url":   { "path": "url" },
        "page.image": { "template": "{imageBase}/releases/{storageKey}/{dir}/{pageUrl}",
          "vars": { "storageKey": "root:storage_key", "dir": "root:__dir", "pageUrl": "url" } }
      }
    }
  ]
}
"""

private val FEAT_RESPONSES: Map<String, String> = mapOf(
    "https://f.example.com/search" to """
    [ { "class": "Manga", "data": [
      { "title": "Naruto",  "url": "/n", "series_type": { "name": "Japanese" }, "deleted_at": null },
      { "title": "A Novel", "url": "/x", "series_type": { "name": "Novel" },    "deleted_at": null },
      { "title": "Deleted", "url": "/d", "series_type": { "name": "Japanese" }, "deleted_at": "2020-01-01" }
    ] } ]
    """,
    "https://f.example.com/ch-jpg" to """
    { "storage_key": "SK", "webp_pages": [], "pages": [ { "url": "a.jpg", "order": 0 }, { "url": "b.jpg", "order": 1 } ] }
    """,
    "https://f.example.com/ch-webp" to """
    { "storage_key": "SK2", "webp_pages": [ { "url": "w1.webp", "order": 0 } ], "pages": [ { "url": "a.jpg", "order": 0 } ] }
    """,
)
