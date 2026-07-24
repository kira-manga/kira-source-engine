package me.manga.kira.source.engine

import kotlinx.coroutines.test.runTest
import me.manga.kira.source.contracts.SourceEngineResult
import me.manga.kira.source.contracts.SourceConfigParser
import me.manga.kira.source.contracts.model.SourceConfig
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import kotlin.test.fail

/**
 * Golden test for `blacklistGenres` filtering: a list item whose genres intersect the source's
 * blacklist (case-insensitive substring, mirroring legacy `hasBlacklistedGenres`) is dropped from the
 * home/search/featured feed. A source with no blacklist never filters (covered by the other golden tests).
 */
class GenericSourceClientBlacklistTest {

    private fun source(): SourceConfig {
        val doc = (SourceConfigParser.parse(BL_CONFIG_JSON) as SourceEngineResult.Success).value
        val validation = DefaultSourceConfigValidator(DefaultStrategyRegistry()).validate(doc)
        assertTrue(validation.isValid, "config invalid: ${validation.errors}")
        return doc.sources.first { it.api == "bl-json" }
    }

    private fun <T> SourceEngineResult<T>.valueOrFail(): T = when (this) {
        is SourceEngineResult.Success -> value
        is SourceEngineResult.Failure -> fail("expected success, got $error")
    }

    @Test
    fun blacklisted_genre_items_are_dropped() = runTest {
        val client = GenericSourceEngine(source(), FakeHttpExecutor(BL_RESPONSES), FakeHeaderStore())
        val home = client.home(1).valueOrFail()
        // "Yaoi One" (genre "Yaoi" ~ "yaoi") and "Smutty" (genre "Smut Romance" ~ "smut") are dropped;
        // only the clean item survives.
        assertEquals(listOf("Clean Action"), home.map { it.title })
    }
}

private const val BL_CONFIG_JSON = """
{
  "schemaVersion": 1,
  "revision": 1,
  "sources": [
    {
      "api": "bl-json",
      "language": "en",
      "baseUrl": "https://bl.example.com",
      "engine": "generic",
      "blacklistGenres": [ "yaoi", "smut" ],
      "pagination": { "type": "page-number", "param": "page", "start": 1 },
      "endpoints": { "home": { "url": "{baseUrl}/home?page={page}", "format": "json", "root": "items" } },
      "fields": {
        "item.title":  { "path": "title" },
        "item.url":    { "path": "url" },
        "item.genres": { "listPath": "genres[*]" }
      }
    }
  ]
}
"""

private val BL_RESPONSES: Map<String, String> = mapOf(
    "https://bl.example.com/home?page=1" to """
    { "items": [
      { "title": "Clean Action", "url": "/a", "genres": ["Action", "Drama"] },
      { "title": "Yaoi One",     "url": "/b", "genres": ["Romance", "Yaoi"] },
      { "title": "Smutty",       "url": "/c", "genres": ["Smut Romance"] }
    ] }
    """,
)
