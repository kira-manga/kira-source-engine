package me.manga.kira.source.engine

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.test.runTest
import me.manga.kira.source.contracts.*
import me.manga.kira.source.contracts.model.*
import kotlin.test.*

/** Synthetic provider-independent contract fixtures; no captured site data or network. */
class GenericSourceChapterContextTest {
    private val source = SourceConfig(
        api = "nested", language = "en", baseUrl = "https://books.example", engine = "generic",
        endpoints = mapOf(
            "home" to EndpointSpec("{baseUrl}/home", format = "json", root = "data"),
            "featured" to EndpointSpec("{baseUrl}/featured", format = "json", root = "data"),
            "details" to EndpointSpec("{itemUrl}", format = "json"),
            "pages" to EndpointSpec("{baseUrl}/reader/{id}/{chapterId}", format = "json", root = "pages"),
        ),
        fields = mapOf(
            "item.title" to FieldSpec(path = "title"),
            "item.url" to FieldSpec(template = "{baseUrl}/series/{slug}", vars = mapOf("slug" to "slug")),
            "item.recentChapters" to FieldSpec(listPath = "chapters"),
            "detail.title" to FieldSpec(path = "title"),
            "detail.chapters" to FieldSpec(listPath = "chapters"),
            "chapter.number" to FieldSpec(path = "number"),
            "chapter.url" to FieldSpec(template = "{baseUrl}/series/{series}/{slug}", vars = mapOf("series" to "root:slug", "slug" to "slug")),
            "chapter.locked" to FieldSpec(path = "locked"),
            "page.image" to FieldSpec(path = "url"),
        ),
    )
    private fun engine(config: SourceConfig = source, respond: suspend (SourceRequest) -> SourceResponse): GenericSourceEngine {
        val result = DefaultSourceConfigValidator(DefaultStrategyRegistry()).validate(SourceConfigDocument(1, sources = listOf(config)))
        assertTrue(result.isValid, result.errors.joinToString())
        return GenericSourceEngine(config, SourceTransport { respond(it) }, object : SourceHeaderProvider {
            override suspend fun headersFor(api: String): Map<String, String> = emptyMap()
        })
    }
    private fun manga(slug: String = "one") = SourceMangaRef("nested", "en", slug, "https://books.example/series/$slug", "")
    private fun body(slug: String) = """{"slug":"$slug","title":"$slug","chapters":[{"slug":"chapter-2","number":"2"},{"slug":"chapter-1","number":"1"},{"slug":"paid","locked":true}]}"""
    private fun <T> SourceEngineResult<T>.valueOrFail(): T = when (this) {
        is SourceEngineResult.Success -> value
        is SourceEngineResult.Failure -> fail("Unexpected failure: $error")
    }

    @Test fun parent_scope_is_local_to_each_feed_item_and_details_call() = runTest {
        val engine = engine { request -> SourceResponse(200, if (request.url.endsWith("/home")) """{"data":[${body("one")},${body("two")}]}""" else body("one")) }
        val feed = engine.home(1).valueOrFail()
        assertEquals(listOf("one", "two"), feed.map { it.title })
        assertEquals(listOf("https://books.example/series/one/chapter-2", "https://books.example/series/two/chapter-2"), feed.map { it.recentChapters.first().url })
        assertTrue(feed.all { it.recentChapters.size == 2 })
        val details = engine.details(manga()).valueOrFail()
        assertEquals(feed.first().recentChapters.map { it.url }, details.chapters.map { it.url })
        assertEquals(listOf("2", "1"), details.chapters.map { it.number })
    }

    @Test fun concurrent_and_separate_chapter_responses_do_not_share_parent_identity() = runTest {
        val arrived = CompletableDeferred<Unit>()
        val release = CompletableDeferred<Unit>()
        val config = source.copy(
            endpoints = source.endpoints + ("chapters" to EndpointSpec("{itemUrl}/chapters", format = "json", root = "chapters")),
            fields = source.fields - "detail.chapters",
        )
        val engine = engine(config) { request ->
            val slug = request.url.substringAfter("/series/").substringBefore('/')
            if (slug == "one" && request.url.endsWith("/chapters")) { arrived.complete(Unit); release.await() }
            SourceResponse(200, body(slug))
        }
        val one = async { engine.details(manga("one")).valueOrFail() }
        arrived.await()
        val two = engine.details(manga("two")).valueOrFail()
        release.complete(Unit)
        assertTrue(two.chapters.all { "/two/" in it.url })
        assertTrue(one.await().chapters.all { "/one/" in it.url })
    }

    @Test fun missing_parent_identity_fails_instead_of_emitting_plausible_chapter_urls() = runTest {
        val result = engine { SourceResponse(200, """{"chapters":[{"slug":"chapter-1"}]}""") }.details(manga())
        assertIs<SourceEngineError.Required>(assertIs<SourceEngineResult.Failure>(result).error)
    }

    @Test fun chapter_identity_preserves_encoded_segment_but_not_query_fragment_or_trailing_slash() = runTest {
        val sent = mutableListOf<SourceRequest>()
        val engine = engine { sent += it; SourceResponse(200, """{"pages":[{"url":"https://cdn.example/1.webp"}]}""") }
        for (url in listOf("https://books.example/manga/one/chapter%2034/?tracking=wrong/id#other", "https://books.example/series/one/chapter%2034")) {
            assertEquals(1, engine.pages(manga(), SourceChapter("34", "", url, null)).valueOrFail().size)
        }
        assertEquals(List(2) { "https://books.example/reader/one/chapter%2034" }, sent.map { it.url })
        assertTrue(sent.all { it.headers.isEmpty() })
    }

    @Test fun invalid_or_absent_chapter_identity_fails_before_transport() = runTest {
        var sends = 0
        val engine = engine { sends++; SourceResponse(200, "{}") }
        for (url in listOf("", "https://books.example/", "https://user@books.example/1", "https://books.example/a\\1", "https://books.example/a/..")) {
            assertEquals(SourceEngineResult.Failure(SourceEngineError.Required("request:chapterId")), engine.pages(manga(), SourceChapter("1", "", url, null)))
        }
        assertEquals(0, sends)
    }

    @Test fun featured_deduplicates_only_identical_urls_in_first_seen_order() = runTest {
        val config = source.copy(fields = source.fields - "item.recentChapters")
        val items = """{"data":[{"slug":"one","title":"First"},{"slug":"two","title":"Second"},{"slug":"one","title":"Clone"},{"slug":"three","title":"First"}]}"""
        assertEquals(listOf("First", "Second", "First"), engine(config) { SourceResponse(200, items) }.featured(1).valueOrFail().map { it.title })
    }

    @Test fun numeric_order_is_global_across_pages_and_preserves_equal_number_identities() = runTest {
        val fields = source.fields + ("chapter.order" to FieldSpec(path = "number"))
        val config = source.copy(
            fields = fields - "detail.chapters",
            endpoints = source.endpoints + ("chapters" to EndpointSpec("{itemUrl}/chapters?page={page}", format = "json", root = "chapters", pageParam = "page", lastPageLocator = "next")),
        )
        val engine = engine(config) { request ->
            SourceResponse(200, when {
                request.url.endsWith("page=1") -> """{"slug":"one","next":true,"chapters":[{"slug":"1","number":"1"},{"slug":"2-a","number":"2"}]}"""
                request.url.endsWith("page=2") -> """{"slug":"one","next":false,"chapters":[{"slug":"2-b","number":"2"},{"slug":"12","number":"12"},{"slug":"1-half","number":"1.5"}]}"""
                else -> "{}"
            })
        }
        val chapters = engine.details(manga()).valueOrFail().chapters
        assertEquals(listOf("12", "2-a", "2-b", "1-half", "1"), chapters.map { it.url.substringAfterLast('/') })
        val inline = engine(source.copy(fields = fields)) { SourceResponse(200, body("one")) }
        assertEquals(listOf("2", "1"), inline.details(manga()).valueOrFail().chapters.map { it.number })
    }

    @Test fun malformed_required_order_fails_without_returning_a_partial_list() = runTest {
        val config = source.copy(fields = source.fields + ("chapter.order" to FieldSpec(path = "number")))
        for (number in listOf("", "NaN", "Infinity", "extra")) {
            val engine = engine(config) { SourceResponse(200, """{"slug":"one","chapters":[{"slug":"1","number":"1"},{"slug":"bad","number":"$number"}]}""") }
            assertEquals(SourceEngineResult.Failure(SourceEngineError.InvalidResponse), engine.details(manga()))
            val singleton = engine(config) { SourceResponse(200, """{"data":[{"slug":"one","chapters":[{"slug":"bad","number":"$number"}]}]}""") }
            assertEquals(SourceEngineResult.Failure(SourceEngineError.InvalidResponse), singleton.home(1))
        }
        val recent = engine(config) { SourceResponse(200, """{"data":[{"slug":"one","chapters":[{"slug":"1","number":"1"}]},{"slug":"two","chapters":[{"slug":"1","number":"1"},{"slug":"12","number":"12"}]}]}""") }.home(1).valueOrFail()
        assertEquals(listOf("https://books.example/series/one/1"), recent.first().recentChapters.map { it.url })
        assertEquals(listOf("12", "1"), recent.last().recentChapters.map { it.number })
    }

    @Test fun reader_transport_failure_and_cancellation_are_not_empty_success() = runTest {
        val chapter = SourceChapter("1", "", "https://books.example/series/one/1", null)
        assertIs<SourceEngineResult.Failure>(engine { SourceResponse(503, "") }.pages(manga(), chapter))
        assertFailsWith<CancellationException> { engine { throw CancellationException("fixture") }.pages(manga(), chapter) }
    }
}
