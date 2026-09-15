package me.manga.kira.source.engine

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.yield
import me.manga.kira.source.contracts.SourceChapter
import me.manga.kira.source.contracts.SourceEngine
import me.manga.kira.source.contracts.SourceEngineError
import me.manga.kira.source.contracts.SourceEngineResult
import me.manga.kira.source.contracts.SourceMangaRef
import me.manga.kira.source.contracts.SourcePage
import me.manga.kira.source.contracts.SourceRequest
import me.manga.kira.source.contracts.SourceResponse
import me.manga.kira.source.contracts.model.EndpointSpec
import me.manga.kira.source.contracts.model.FieldSpec
import me.manga.kira.source.contracts.model.SourceConfig
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertSame

/** Network-free provider vectors shared across all public verbs, including pages' early header read. */
class GenericSourceEngineFailureBoundaryTest {
    @Test
    fun base_url_provider_failures_are_typed_for_every_verb() = runTest {
        assertProviderFailures(Provider.BASE_URL)
    }

    @Test
    fun captured_header_provider_failures_are_typed_for_every_verb_including_pages() = runTest {
        assertProviderFailures(Provider.HEADERS)
    }

    @Test
    fun provider_cancellation_escapes_unchanged_for_every_verb() = runTest {
        for (provider in Provider.entries) {
            for (verb in Verb.entries) {
                val cancellation = CancellationException("cancel $provider $verb")
                val fixture = Fixture(provider, cancellation)

                val thrown = assertFailsWith<CancellationException>("$provider $verb") {
                    invoke(verb, fixture.engine)
                }

                assertSame(cancellation, thrown, "$provider $verb")
                assertEquals(provider.callsThroughFailure(), fixture.providerCalls, "$provider $verb")
                assertEquals(emptyList(), fixture.requests, "$provider $verb")
            }
        }
    }

    @Test
    fun pages_reuses_one_header_read_for_the_request_and_returned_images() = runTest {
        val fixture = Fixture()
        val headers = CONFIG.headers + CAPTURED_HEADERS

        assertEquals(
            SourceEngineResult.Success(listOf(SourcePage("$LIVE_BASE_URL/page/1.webp", headers))),
            fixture.engine.pages(MANGA, CHAPTER),
        )
        assertEquals(listOf(Provider.BASE_URL, Provider.HEADERS), fixture.providerCalls)
        assertEquals(SourceRequest(CHAPTER.url, headers = headers), fixture.requests.single())
    }

    private suspend fun assertProviderFailures(provider: Provider) {
        val vectors = listOf(
            IllegalStateException("synthetic provider failure") to SourceEngineError.Unexpected("IllegalStateException"),
            IllegalStateException("provider timed out") to SourceEngineError.Timeout,
        )
        for (verb in Verb.entries) {
            for ((failure, expected) in vectors) {
                val fixture = Fixture(provider, failure)
                val label = "$provider $verb $expected"

                assertEquals(SourceEngineResult.Failure(expected), invoke(verb, fixture.engine), label)
                assertEquals(provider.callsThroughFailure(), fixture.providerCalls, label)
                assertEquals(emptyList(), fixture.requests, label)
            }
        }
    }

    private suspend fun invoke(verb: Verb, engine: SourceEngine): SourceEngineResult<*> = when (verb) {
        Verb.HOME -> engine.home(2)
        Verb.FEATURED -> engine.featured(2)
        Verb.SEARCH -> engine.search("one piece", 2)
        Verb.DETAILS -> engine.details(MANGA)
        Verb.PAGES -> engine.pages(MANGA, CHAPTER)
    }

    private enum class Verb { HOME, FEATURED, SEARCH, DETAILS, PAGES }

    private enum class Provider {
        BASE_URL, HEADERS;

        fun callsThroughFailure(): List<Provider> = entries.take(ordinal + 1)
    }

    private class Fixture(
        private val failingProvider: Provider? = null,
        private val failure: Throwable? = null,
    ) {
        val providerCalls = mutableListOf<Provider>()
        val requests = mutableListOf<SourceRequest>()
        val engine: SourceEngine = GenericSourceEngine(
            config = CONFIG,
            baseUrlProvider = {
                readProvider(Provider.BASE_URL)
                LIVE_BASE_URL
            },
            headerStore = {
                readProvider(Provider.HEADERS)
                CAPTURED_HEADERS
            },
            http = { request ->
                requests += request
                SourceResponse(status = 200, body = """{"pages":["/page/1.webp"]}""")
            },
        )

        private suspend fun readProvider(provider: Provider) {
            providerCalls += provider
            // A real suspension verifies the boundary also catches failures delivered on resumption.
            yield()
            if (failingProvider == provider) throw checkNotNull(failure)
        }
    }

    private companion object {
        const val API = "provider-fixture"
        const val LIVE_BASE_URL = "https://live.example"
        val CAPTURED_HEADERS = mapOf("X-Captured" to "captured", "Referer" to LIVE_BASE_URL)
        val MANGA = SourceMangaRef(API, "en", "Manga", "$LIVE_BASE_URL/manga/one", "")
        val CHAPTER = SourceChapter("1", "Chapter 1", "$LIVE_BASE_URL/manga/one/chapter/1", null)
        val CONFIG = SourceConfig(
            api = API,
            language = "en",
            baseUrl = "https://configured.example",
            engine = "generic",
            headers = mapOf("X-Static" to "static", "Referer" to "https://configured.example"),
            endpoints = mapOf(
                "home" to EndpointSpec(url = "{baseUrl}/home?page={page}", format = "json", root = "items"),
                "featured" to EndpointSpec(url = "{baseUrl}/featured?page={page}", format = "json", root = "items"),
                "search" to EndpointSpec(url = "{baseUrl}/search?q={queryEncoded}&page={page}", format = "json", root = "items"),
                "details" to EndpointSpec(url = "{itemUrl}", format = "json"),
                "pages" to EndpointSpec(url = "{chapterUrl}", format = "json", root = "pages"),
            ),
            fields = mapOf("page.image" to FieldSpec(path = "")),
        )
    }
}
