package me.manga.kira.source.engine

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.test.runTest
import me.manga.kira.source.contracts.SourceEngineError
import me.manga.kira.source.contracts.SourceEngineResult
import me.manga.kira.source.contracts.SourceMangaRef
import me.manga.kira.source.contracts.SourceConfigParser
import me.manga.kira.source.contracts.SourceResponse
import me.manga.kira.source.contracts.SourceTransport
import me.manga.kira.source.contracts.model.SourceConfig
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertSame
import kotlin.test.assertTrue
import kotlin.test.fail

/**
 * Golden test for detail chapter-list pagination: when the `chapters` endpoint declares a `pageParam`,
 * `details()` loops `?page=1,2,…` and concatenates, deciding "more?" from `lastPageLocator` — an HTML
 * pagination widget (numeric → loop while page < max) or a JSON `has_next` flag (loop while true).
 */
class GenericSourceClientChapterPaginationTest {

    private fun source(api: String): SourceConfig {
        val doc = (SourceConfigParser.parse(PAGINATION_CONFIG) as SourceEngineResult.Success).value
        val v = DefaultSourceConfigValidator(DefaultStrategyRegistry()).validate(doc)
        assertTrue(v.isValid, "config invalid: ${v.errors}")
        return doc.sources.first { it.api == api }
    }

    private fun client(api: String, http: SourceTransport = FakeHttpExecutor(PAGINATION_RESPONSES)) =
        GenericSourceEngine(source(api), http, FakeHeaderStore())

    private val jsonManga = SourceMangaRef("pg-json", "en", "x", "https://j.example.com/series/5", "")

    private fun jsonPageUrl(page: Int): String = "${jsonManga.url}/episodes?page=$page"

    private fun requiredPageTransport(
        page: Int,
        requested: MutableList<String>,
        response: () -> SourceResponse,
    ): SourceTransport {
        // Keep page 2 nonterminal so a required page-3 failure exercises an accumulated two-page list.
        val continuingPage = PAGINATION_RESPONSES.getValue(jsonPageUrl(2))
            .replace("\"has_next\": false", "\"has_next\": true")
        val http = FakeHttpExecutor(PAGINATION_RESPONSES + (jsonPageUrl(2) to continuingPage))
        return SourceTransport { request ->
            requested += request.url
            if (request.url == jsonPageUrl(page)) response() else http.execute(request)
        }
    }

    private fun <T> SourceEngineResult<T>.valueOrFail(): T = when (this) {
        is SourceEngineResult.Success -> value
        is SourceEngineResult.Failure -> fail("expected success, got $error")
    }

    @Test
    fun html_pagination_loops_until_max_page() = runTest {
        val manga = SourceMangaRef("pg-html", "en", "x", "https://h.example.com/series/x", "")
        val d = client("pg-html").details(manga).valueOrFail()
        assertEquals("HtmlTitle", d.title)
        // pagination links 1,2,3 → fetch pages 1..3 → 2+2+1 = 5 chapters
        assertEquals(listOf("43", "42", "41", "40", "39"), d.chapters.map { it.number })
        assertEquals("https://h.example.com/series/x/43", d.chapters[0].url)
    }

    @Test
    fun json_pagination_loops_while_has_next() = runTest {
        val http = FakeHttpExecutor(PAGINATION_RESPONSES)
        val d = client("pg-json", http).details(jsonManga).valueOrFail()
        assertEquals("JsonTitle", d.title)
        // page1 has_next=true → fetch page2 (has_next=false) → 2+1 = 3 episodes
        assertEquals(listOf("E1", "E2", "E3"), d.chapters.map { it.name })
        assertEquals(listOf(jsonManga.url, jsonPageUrl(1), jsonPageUrl(2)), http.requested)
    }

    @Test
    fun required_later_page_failures_preserve_the_typed_error() = runTest {
        val failures: List<Pair<SourceEngineError, () -> SourceResponse>> = listOf(
            SourceEngineError.NoConnectivity to { throw IllegalStateException("network is unreachable") },
            SourceEngineError.Timeout to { throw IllegalStateException("request timed out") },
            SourceEngineError.Http(503) to { SourceResponse(503, "unavailable") },
            SourceEngineError.InvalidResponse to { SourceResponse(200, "{") },
            SourceEngineError.Required("var:chapter.url:id") to {
                SourceResponse(
                    200,
                    """{"data":{"episodes":[{"name":"Missing id"}],"pagination":{"has_next":false}}}""",
                )
            },
        )
        for (page in 2..3) {
            for ((error, response) in failures) {
                val requested = mutableListOf<String>()
                val http = requiredPageTransport(page, requested, response)
                assertEquals(
                    SourceEngineResult.Failure(error),
                    client("pg-json", http).details(jsonManga),
                    "required page $page: $error must not become partial success",
                )
                assertEquals(listOf(jsonManga.url) + (1..page).map(::jsonPageUrl), requested)
            }
        }
    }

    @Test
    fun required_later_page_cancellation_rethrows_the_same_instance() = runTest {
        for (page in 2..3) {
            val cancellation = CancellationException("cancel required page $page")
            val requested = mutableListOf<String>()
            val http = requiredPageTransport(page, requested) { throw cancellation }
            val thrown = assertFailsWith<CancellationException> {
                client("pg-json", http).details(jsonManga)
            }
            assertSame(cancellation, thrown)
            assertEquals(listOf(jsonManga.url) + (1..page).map(::jsonPageUrl), requested)
        }
    }

    @Test
    fun empty_terminal_page_succeeds_without_requesting_another_page() = runTest {
        val requested = mutableListOf<String>()
        val http = requiredPageTransport(2, requested) {
            // An empty successful page remains terminal even if the server leaves has_next true.
            SourceResponse(200, """{"data":{"episodes":[],"pagination":{"has_next":true}}}""")
        }
        val details = client("pg-json", http).details(jsonManga).valueOrFail()
        assertEquals("JsonTitle", details.title)
        assertEquals(listOf("E1", "E2"), details.chapters.map { it.name })
        assertEquals(listOf(jsonManga.url, jsonPageUrl(1), jsonPageUrl(2)), requested)
    }

    @Test
    fun html_required_last_page_http_failure_is_not_partial_success() = runTest {
        val manga = SourceMangaRef("pg-html", "en", "x", "https://h.example.com/series/x", "")
        val http = FakeHttpExecutor(PAGINATION_RESPONSES - "${manga.url}?page=3")
        assertEquals(SourceEngineResult.Failure(SourceEngineError.Http(404)), client("pg-html", http).details(manga))
        assertEquals(listOf(manga.url) + (1..3).map { "${manga.url}?page=$it" }, http.requested)
    }

    @Test
    fun json_pagination_loops_while_next_cursor_present() = runTest {
        // DRF-style: lastPageLocator "next" is a URL string on page1 (→ more) and null on page2 (→ stop).
        // Mirrors SwatManga, whose server caps page_size at 200 so a long series needs this to be complete.
        val manga = SourceMangaRef("pg-next", "en", "x", "https://d.example.com/series/7", "")
        val d = client("pg-next").details(manga).valueOrFail()
        assertEquals("DrfTitle", d.title)
        assertEquals(listOf("C1", "C2", "C3"), d.chapters.map { it.number }) // page1 (C1,C2) + page2 (C3)
    }
}

private const val PAGINATION_CONFIG = """
{
  "schemaVersion": 1, "revision": 1,
  "sources": [
    {
      "api": "pg-html", "language": "en", "baseUrl": "https://h.example.com", "engine": "generic",
      "pagination": { "type": "page-number", "param": "page", "start": 1 },
      "endpoints": {
        "home":     { "url": "{baseUrl}/?page={page}", "format": "html", "listSelector": "div.box" },
        "details":  { "url": "{itemUrl}", "format": "html" },
        "chapters": { "url": "{itemUrl}?page={page}", "format": "html", "listSelector": "div.chapter-card",
          "pageParam": "page", "lastPageLocator": "ul.pagination li.page-item a.page-link" }
      },
      "fields": {
        "detail.title":   { "selector": "h1", "attr": "text" },
        "chapter.number": { "selector": "", "attr": "data-number" },
        "chapter.name":   { "selector": "div.chapter-title", "attr": "text" },
        "chapter.url":    { "selector": "a.chapter-link", "attr": "abs:href" }
      }
    },
    {
      "api": "pg-json", "language": "en", "baseUrl": "https://j.example.com", "engine": "generic",
      "pagination": { "type": "page-number", "param": "page", "start": 1 },
      "endpoints": {
        "home":     { "url": "{baseUrl}/home?page={page}", "format": "json", "root": "items" },
        "details":  { "url": "{itemUrl}", "format": "json" },
        "chapters": { "url": "{baseUrl}/series/{id}/episodes?page={page}", "format": "json", "root": "data.episodes",
          "pageParam": "page", "lastPageLocator": "data.pagination.has_next" }
      },
      "fields": {
        "detail.title":   { "path": "title" },
        "chapter.number": { "path": "id" },
        "chapter.name":   { "path": "name" },
        "chapter.url":    { "template": "{baseUrl}/ep/{id}", "vars": { "id": "id" } }
      }
    },
    {
      "api": "pg-next", "language": "en", "baseUrl": "https://d.example.com", "engine": "generic",
      "pagination": { "type": "page-number", "param": "page", "start": 1 },
      "endpoints": {
        "home":     { "url": "{baseUrl}/home?page={page}", "format": "json", "root": "results" },
        "details":  { "url": "{itemUrl}", "format": "json" },
        "chapters": { "url": "{baseUrl}/series/{id}/chapters/?page={page}&page_size=200", "format": "json", "root": "results",
          "pageParam": "page", "lastPageLocator": "next" }
      },
      "fields": {
        "detail.title":   { "path": "title" },
        "chapter.number": { "path": "chapter" },
        "chapter.name":   { "path": "chapter" },
        "chapter.url":    { "template": "{baseUrl}/ch/{id}", "vars": { "id": "id" } }
      }
    }
  ]
}
"""

private val PAGINATION_RESPONSES: Map<String, String> = mapOf(
    "https://h.example.com/series/x" to "<html><body><h1>HtmlTitle</h1></body></html>",
    "https://h.example.com/series/x?page=1" to htmlChapters(listOf(43, 42)),
    "https://h.example.com/series/x?page=2" to htmlChapters(listOf(41, 40)),
    "https://h.example.com/series/x?page=3" to htmlChapters(listOf(39)),
    "https://j.example.com/series/5" to """{ "title": "JsonTitle" }""",
    "https://j.example.com/series/5/episodes?page=1" to
        """{ "data": { "episodes": [ { "id": "E1", "name": "E1" }, { "id": "E2", "name": "E2" } ], "pagination": { "has_next": true } } }""",
    "https://j.example.com/series/5/episodes?page=2" to
        """{ "data": { "episodes": [ { "id": "E3", "name": "E3" } ], "pagination": { "has_next": false } } }""",
    "https://d.example.com/series/7" to """{ "title": "DrfTitle" }""",
    // DRF cursor style: `next` is a URL string while pages remain, null once exhausted.
    "https://d.example.com/series/7/chapters/?page=1&page_size=200" to
        """{ "count": 3, "next": "https://d.example.com/series/7/chapters/?page=2&page_size=200", "previous": null, "results": [ { "chapter": "C1", "id": "1" }, { "chapter": "C2", "id": "2" } ] }""",
    "https://d.example.com/series/7/chapters/?page=2&page_size=200" to
        """{ "count": 3, "next": null, "previous": "x", "results": [ { "chapter": "C3", "id": "3" } ] }""",
)

private fun htmlChapters(numbers: List<Int>): String {
    val cards = numbers.joinToString("") { n ->
        """<div class="chapter-card" data-number="$n"><div class="chapter-title">T$n</div><a class="chapter-link" href="/series/x/$n">l</a></div>"""
    }
    val pagination = """<ul class="pagination"><li class="page-item"><a class="page-link">1</a></li><li class="page-item"><a class="page-link">2</a></li><li class="page-item"><a class="page-link">3</a></li></ul>"""
    return "<html><body>$cards$pagination</body></html>"
}
