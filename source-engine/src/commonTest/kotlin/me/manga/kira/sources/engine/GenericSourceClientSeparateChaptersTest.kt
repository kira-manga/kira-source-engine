package me.manga.kira.source.engine

import kotlinx.coroutines.test.runTest
import kotlinx.datetime.LocalDate
import me.manga.kira.source.contracts.SourceEngineResult
import me.manga.kira.source.contracts.SourceMangaRef
import me.manga.kira.source.contracts.SourceConfigParser
import me.manga.kira.source.contracts.model.SourceConfig
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import kotlin.test.fail

/**
 * Golden test for the two-request "separated details" feature: a source whose manga scalars and chapter
 * list live at two DISTINCT endpoints (the legacy `SeparatedDetailsSites` shape — Mangamello, SwatManga,
 * …). When `endpoints["chapters"]` is declared, `details()` must issue a SECOND request and build the
 * chapter list from THAT body (its own `root`), while the scalars come from the details body.
 *
 * Also pins the safety contract: if the chapters request fails, the whole `details()` call fails
 * instead of surfacing a misleading chapter-less success.
 */
class GenericSourceClientSeparateChaptersTest {

    private fun source(): SourceConfig {
        val document = (SourceConfigParser.parse(SEP_CONFIG_JSON) as SourceEngineResult.Success).value
        val validation = DefaultSourceConfigValidator(DefaultStrategyRegistry()).validate(document)
        assertTrue(validation.isValid, "config invalid: ${validation.errors}")
        return document.sources.first { it.api == "sep-json" }
    }

    private fun client(responses: Map<String, String>) =
        GenericSourceEngine(source(), FakeHttpExecutor(responses), FakeHeaderStore())

    private fun <T> SourceEngineResult<T>.valueOrFail(): T = when (this) {
        is SourceEngineResult.Success -> value
        is SourceEngineResult.Failure -> fail("expected success, got $error")
    }

    private val manga = SourceMangaRef("sep-json", "en", "One Piece", "https://api.sep.com/series/op", "")

    @Test
    fun details_fetches_chapters_from_separate_endpoint_and_filters_locked() = runTest {
        val http = FakeHttpExecutor(SEP_RESPONSES)
        val details = GenericSourceEngine(source(), http, FakeHeaderStore()).details(manga).valueOrFail()

        // scalars come from the details body
        assertEquals("One Piece", details.title)
        assertEquals("Ongoing", details.status) // enum-map "1" -> "Ongoing"

        // chapters come from the SECOND request's body (root "results"); the locked one is filtered out
        assertEquals(2, details.chapters.size)
        val ch1 = details.chapters[0]
        assertEquals("SourceChapter 1", ch1.number) // format-number + prepend "SourceChapter "
        assertEquals("Romance Dawn", ch1.name)
        assertEquals("https://api.sep.com/chapters/11", ch1.url)
        assertEquals(LocalDate(2023, 11, 14), ch1.date) // epoch-seconds 1700000000

        // both endpoints were actually requested (the chapters URL is templated from {itemUrl})
        assertTrue(http.requested.any { it == "https://api.sep.com/series/op" }, "details not requested")
        assertTrue(http.requested.any { it == "https://api.sep.com/series/op/chapters" }, "chapters not requested")
    }

    @Test
    fun details_fails_when_separate_chapters_request_fails() = runTest {
        // Only the details body is served; the chapters URL 404s, so the whole request must fail.
        val onlyDetails = mapOf("https://api.sep.com/series/op" to SEP_DETAILS_BODY)
        val result = client(onlyDetails).details(manga)
        assertTrue(result is SourceEngineResult.Failure, "expected Failure when chapters endpoint fails, got $result")
    }
}

private const val SEP_CONFIG_JSON: String = """
{
  "schemaVersion": 1,
  "revision": 1,
  "sources": [
    {
      "api": "sep-json",
      "language": "en",
      "displayName": "Separated JSON",
      "baseUrl": "https://api.sep.com",
      "engine": "generic",
      "pagination": { "type": "page-number", "param": "page", "start": 1 },
      "endpoints": {
        "home":     { "url": "{baseUrl}/home?page={page}", "format": "json", "root": "data" },
        "details":  { "url": "{baseUrl}/series/{id}", "format": "json" },
        "chapters": { "url": "{itemUrl}/chapters", "format": "json", "root": "results" },
        "pages":    { "url": "{chapterUrl}", "format": "json", "root": "pages" }
      },
      "fields": {
        "item.title": { "path": "title" },
        "item.url":   { "path": "url" },
        "detail.title":  { "path": "title" },
        "detail.status": { "path": "is_completed", "transform": [ { "fn": "enum-map", "args": { "1": "Ongoing", "2": "Completed" } } ] },
        "chapter.number": { "path": "num", "transform": [ { "fn": "format-number" }, { "fn": "prepend", "args": { "value": "SourceChapter " } } ] },
        "chapter.name":   { "path": "title" },
        "chapter.url":    { "template": "{baseUrl}/chapters/{id}", "vars": { "id": "id" } },
        "chapter.date":   { "path": "released", "dateStrategy": "epoch-seconds" },
        "chapter.locked": { "path": "locked" }
      }
    }
  ]
}
"""

private const val SEP_DETAILS_BODY = """{ "title": "One Piece", "is_completed": "1" }"""

private const val SEP_CHAPTERS_BODY = """
{ "results": [
  { "num": 1, "title": "Romance Dawn", "id": 11, "released": 1700000000, "locked": false },
  { "num": 2, "title": "Buggy",        "id": 12, "released": 1700100000, "locked": false },
  { "num": 3, "title": "Paid",         "id": 13, "released": 1700200000, "locked": true }
] }
"""

private val SEP_RESPONSES: Map<String, String> = mapOf(
    "https://api.sep.com/series/op" to SEP_DETAILS_BODY,
    "https://api.sep.com/series/op/chapters" to SEP_CHAPTERS_BODY,
)
