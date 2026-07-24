package me.manga.kira.sources.engine

import me.manga.kira.sources.contracts.SourceHeaderProvider
import me.manga.kira.sources.contracts.SourceRequest
import me.manga.kira.sources.contracts.SourceResponse
import me.manga.kira.sources.contracts.SourceTransport

/**
 * The golden-fixture harness for the generic engine. Everything the engine does — request templating,
 * extraction, transforms, absolutisation, mapping to domain models — is exercised against a fixed
 * config document + canned HTTP responses, so a behavior change shows up as a failing assertion. The
 * fixtures are embedded strings (no filesystem) so the suite runs identically on Desktop/Android/iOS.
 *
 * Two sources are described in one document: a JSON-API source and an HTML-scraped source. They are
 * the two families the engine must handle; per-source families (Madara, Comick, …) layer on later as
 * additional configs, not new code.
 */

/** Exact-match fake transport: a [SourceRequest.url] not in the map is a 404 (catches template bugs). */
class FakeHttpExecutor(private val responses: Map<String, String>) : SourceTransport {
    val requested = mutableListOf<String>()

    override suspend fun execute(request: SourceRequest): SourceResponse {
        requested += request.url
        val body = responses[request.url]
            ?: return SourceResponse(status = 404, body = "not found: ${request.url}")
        return SourceResponse(status = 200, body = body)
    }
}

class FakeHeaderStore(private val headers: Map<String, String> = emptyMap()) : SourceHeaderProvider {
    override suspend fun headersFor(api: String): Map<String, String> = headers
}

// --- the config document (parsed end-to-end in tests) -----------------------------------------------

const val GOLDEN_CONFIG_JSON: String = """
{
  "schemaVersion": 1,
  "revision": 7,
  "sources": [
    {
      "api": "example-json",
      "language": "en",
      "displayName": "Example JSON",
      "baseUrl": "https://api.example.com",
      "imageBase": "https://img.example.com",
      "engine": "generic",
      "pagination": { "type": "page-number", "param": "page", "start": 1 },
      "endpoints": {
        "home":    { "url": "{baseUrl}/home?page={page}", "format": "json", "root": "data.items" },
        "search":  { "url": "{baseUrl}/search?q={queryEncoded}&page={page}", "format": "json", "root": "data.items" },
        "details": { "url": "{baseUrl}/manga/{id}", "format": "json" },
        "pages":   { "url": "{chapterUrl}", "format": "json", "root": "pages" }
      },
      "fields": {
        "item.title":  { "path": "title" },
        "item.url":    { "path": "slug" },
        "item.cover":  { "path": "thumb" },
        "item.rating": { "path": "score" },
        "detail.title":       { "path": "title" },
        "detail.description":  { "path": "synopsis", "transform": [ { "fn": "strip-html" }, { "fn": "trim" } ] },
        "detail.author":      { "path": "author" },
        "detail.status":      { "path": "status", "transform": [ { "fn": "enum-map", "args": { "1": "Ongoing", "2": "Completed" } } ] },
        "detail.rating":      { "path": "score" },
        "detail.cover":       { "path": "thumb" },
        "detail.genres":      { "listPath": "genres[*]" },
        "detail.chapters":    { "listPath": "chapters" },
        "chapter.number": { "path": "num" },
        "chapter.name":   { "path": "title" },
        "chapter.url":    { "path": "id" },
        "chapter.date":   { "path": "released", "dateStrategy": "epoch-seconds" },
        "page.image":     { "path": "" }
      }
    },
    {
      "api": "example-html",
      "language": "en",
      "displayName": "Example HTML",
      "baseUrl": "https://html.example.com",
      "engine": "generic",
      "endpoints": {
        "home":    { "url": "{baseUrl}/", "format": "html", "listSelector": "div.item" },
        "search":  { "url": "{baseUrl}/search?q={queryEncoded}", "format": "html", "listSelector": "div.item" },
        "details": { "url": "{itemUrl}", "format": "html" },
        "pages":   { "url": "{chapterUrl}", "format": "html", "listSelector": "img.page" }
      },
      "fields": {
        "item.title": { "selector": "h3.title", "attr": "text" },
        "item.url":   { "selector": "a.link", "attr": "href" },
        "item.cover": { "selector": "img.cover", "attr": "abs:src" },
        "detail.title":       { "selector": "h1", "attr": "text" },
        "detail.description":  { "selector": "div.summary", "attr": "text", "transform": [ { "fn": "trim" } ] },
        "detail.author":      { "selector": "span.author", "attr": "text" },
        "detail.status":      { "selector": "span.status", "attr": "text" },
        "detail.rating":      { "selector": "span.rating", "attr": "text" },
        "detail.cover":       { "selector": "img.cover", "attr": "abs:src" },
        "detail.genres":      { "listSelector": "a.genre", "attr": "text" },
        "detail.chapters":    { "listSelector": "li.chapter" },
        "chapter.number": { "selector": "span.num", "attr": "text" },
        "chapter.name":   { "selector": "a", "attr": "text" },
        "chapter.url":    { "selector": "a", "attr": "href" },
        "chapter.date":   { "selector": "span.date", "attr": "text", "dateStrategy": "iso" },
        "page.image":     { "attr": "abs:src" }
      }
    }
  ]
}
"""

// --- JSON-API canned responses ----------------------------------------------------------------------

val JSON_RESPONSES: Map<String, String> = mapOf(
    "https://api.example.com/home?page=1" to JSON_LIST,
    "https://api.example.com/search?q=one%20piece&page=1" to JSON_LIST,
    "https://api.example.com/manga/one-piece" to JSON_DETAILS,
    "https://api.example.com/one-piece/1" to JSON_PAGES,
)

private const val JSON_LIST_RAW = """
{ "data": { "items": [
  { "title": "One Piece", "slug": "one-piece", "thumb": "/covers/op.jpg", "score": 9 },
  { "title": "Naruto",   "slug": "naruto",    "thumb": "https://img.example.com/covers/naruto.jpg", "score": 8 }
] } }
"""

private const val JSON_DETAILS_RAW = """
{
  "title": "One Piece",
  "synopsis": "<p>Pirates adventure</p>",
  "author": "Oda",
  "status": "1",
  "score": "9.2",
  "thumb": "/covers/op.jpg",
  "genres": ["Action", "Adventure"],
  "chapters": [
    { "num": "1", "title": "Romance Dawn", "id": "one-piece/1", "released": 1700000000 },
    { "num": "2", "title": "Buggy",        "id": "one-piece/2", "released": 1700100000 }
  ]
}
"""

private const val JSON_PAGES_RAW = """
{ "pages": [ "https://img.example.com/op/1/1.webp", "https://img.example.com/op/1/2.webp" ] }
"""

// indirection so the map can reference the consts above (Kotlin const ordering)
val JSON_LIST: String get() = JSON_LIST_RAW
val JSON_DETAILS: String get() = JSON_DETAILS_RAW
val JSON_PAGES: String get() = JSON_PAGES_RAW

// --- HTML canned responses --------------------------------------------------------------------------

val HTML_RESPONSES: Map<String, String> = mapOf(
    "https://html.example.com/" to HTML_LIST,
    "https://html.example.com/search?q=one%20piece" to HTML_LIST,
    "https://html.example.com/manga/op" to HTML_DETAILS,
    "https://html.example.com/manga/op/1" to HTML_PAGES,
)

private const val HTML_LIST_RAW = """
<html><body>
  <div class="item"><a class="link" href="/manga/op"><h3 class="title">One Piece</h3></a><img class="cover" src="/covers/op.jpg"></div>
  <div class="item"><a class="link" href="/manga/naruto"><h3 class="title">Naruto</h3></a><img class="cover" src="/covers/naruto.jpg"></div>
</body></html>
"""

private const val HTML_DETAILS_RAW = """
<html><body>
  <h1>One Piece</h1>
  <img class="cover" src="/covers/op.jpg">
  <div class="summary">   Pirates adventure   </div>
  <span class="author">Oda</span>
  <span class="status">Ongoing</span>
  <span class="rating">9.2</span>
  <a class="genre">Action</a><a class="genre">Adventure</a>
  <ul>
    <li class="chapter"><span class="num">1</span><a href="/manga/op/1">Romance Dawn</a><span class="date">2024-01-15</span></li>
    <li class="chapter"><span class="num">2</span><a href="/manga/op/2">Buggy</a><span class="date">2024-01-22</span></li>
  </ul>
</body></html>
"""

private const val HTML_PAGES_RAW = """
<html><body>
  <img class="page" src="/op/1/1.webp">
  <img class="page" src="/op/1/2.webp">
</body></html>
"""

val HTML_LIST: String get() = HTML_LIST_RAW
val HTML_DETAILS: String get() = HTML_DETAILS_RAW
val HTML_PAGES: String get() = HTML_PAGES_RAW
