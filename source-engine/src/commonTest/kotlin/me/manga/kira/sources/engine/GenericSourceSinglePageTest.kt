package me.manga.kira.source.engine

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertTrue
import kotlinx.coroutines.test.runTest
import me.manga.kira.source.contracts.*
import me.manga.kira.source.contracts.model.*

class GenericSourceSinglePageTest {
    private val endpoint = EndpointSpec("{baseUrl}/feed", format = "json", root = "items", singlePage = true)
    private val config = SourceConfig(
        api = "finite", language = "en", baseUrl = "https://finite.example", engine = "generic",
        pagination = PaginationSpec(start = 3),
        endpoints = mapOf("home" to endpoint, "featured" to endpoint, "search" to endpoint,
            "details" to endpoint.copy(singlePage = false), "pages" to endpoint.copy(singlePage = false)),
        fields = mapOf("item.title" to FieldSpec(path = "title"), "item.url" to FieldSpec(path = "url"),
            "detail.title" to FieldSpec(path = "title"), "chapter.number" to FieldSpec(path = "number"),
            "chapter.url" to FieldSpec(path = "url"), "page.image" to FieldSpec(path = "url")),
    )

    @Test
    fun finite_feeds_obey_the_declared_start_and_stop_before_any_io() = runTest {
        var requests = 0
        var headers = 0
        var origins = 0
        val client = GenericSourceEngine(config, {
            requests++
            SourceResponse(200, """{"items":[{"title":"Fixture","url":"/work/1"}]}""")
        }, { headers++; emptyMap() }, baseUrlProvider = { origins++; null })
        assertEquals(1, assertIs<SourceEngineResult.Success<List<SourceListItem>>>(client.home(3)).value.size)
        assertIs<SourceEngineResult.Success<*>>(client.featured(3))
        assertIs<SourceEngineResult.Success<*>>(client.search("fixture", 3))
        assertEquals(3, requests)
        val before = listOf(requests, headers, origins)
        for (page in listOf(2, 4, Int.MAX_VALUE)) {
            assertEquals(SourceEngineResult.Success(emptyList<SourceListItem>()), client.home(page))
            assertEquals(SourceEngineResult.Success(emptyList<SourceFeaturedItem>()), client.featured(page))
            assertEquals(SourceEngineResult.Success(emptyList<SourceListItem>()), client.search("fixture", page))
        }
        assertEquals(before, listOf(requests, headers, origins))
    }

    @Test
    fun finite_first_page_errors_are_not_empty_success_and_normal_paging_is_unchanged() = runTest {
        var requests = 0
        val transport = SourceTransport { requests++; SourceResponse(503, "unavailable") }
        val finite = GenericSourceEngine(config, transport, { emptyMap() })
        assertEquals(SourceEngineResult.Failure(SourceEngineError.Http(503)), finite.search("x", 3))
        assertEquals(SourceEngineResult.Success(emptyList<SourceListItem>()), finite.search("x", 4))
        val normal = GenericSourceEngine(config.copy(endpoints = config.endpoints +
            ("search" to endpoint.copy(singlePage = false))), transport, { emptyMap() })
        assertEquals(SourceEngineResult.Failure(SourceEngineError.Http(503)), normal.search("x", 4))
        assertEquals(2, requests)
    }

    @Test
    fun single_page_on_non_feed_verbs_is_rejected_instead_of_silently_ignored() {
        val validator = DefaultSourceConfigValidator(DefaultStrategyRegistry())
        fun valid(source: SourceConfig) = validator.validate(SourceConfigDocument(1, sources = listOf(source))).isValid
        assertTrue(valid(config))
        for (verb in listOf("details", "chapters", "pages")) {
            assertTrue(!valid(config.copy(endpoints = config.endpoints + (verb to endpoint))))
        }
    }
}
