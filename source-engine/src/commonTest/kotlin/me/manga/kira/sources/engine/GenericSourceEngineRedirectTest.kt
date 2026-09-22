package me.manga.kira.source.engine

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancel
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.test.runTest
import me.manga.kira.source.contracts.SourceChallengeListener
import me.manga.kira.source.contracts.SourceEngineError
import me.manga.kira.source.contracts.SourceEngineResult
import me.manga.kira.source.contracts.SourceHeaderProvider
import me.manga.kira.source.contracts.SourceHttpMethod
import me.manga.kira.source.contracts.SourceRequest
import me.manga.kira.source.contracts.SourceResponse
import me.manga.kira.source.contracts.SourceTransport
import me.manga.kira.source.contracts.model.EndpointSpec
import me.manga.kira.source.contracts.model.FieldSpec
import me.manga.kira.source.contracts.model.FilterDefinition
import me.manga.kira.source.contracts.model.FilterOptionSpec
import me.manga.kira.source.contracts.model.FilterRequestSpec
import me.manga.kira.source.contracts.model.SourceConfig
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue
import kotlin.test.fail

class GenericSourceEngineRedirectTest {
    private val statuses = listOf(301, 302, 303, 307, 308)
    private val static = mapOf("Referer" to "fixture-static", "X-Precedence" to "static")
    private val captured = mapOf("cOoKiE" to "fixture-cookie", "X-Arbitrary" to "fixture-value", "X-Precedence" to "captured")
    private val merged = static + captured
    private val body = """{"items":[{"title":"Fixture","url":"https://api.test/manga/1"}]}"""

    private class RecordingTransport(private val respond: suspend (SourceRequest, Int) -> SourceResponse) : SourceTransport {
        val requests = mutableListOf<SourceRequest>()
        override suspend fun execute(request: SourceRequest): SourceResponse {
            requests += request
            return respond(request, requests.size)
        }
    }

    private fun config() = SourceConfig(
        api = "redirect-fixture", language = "en", engine = "generic", baseUrl = "https://api.test", imageBase = "https://img.test",
        headers = static,
        endpoints = mapOf(
            "home" to EndpointSpec("{baseUrl}/start", root = "items"),
            "search" to EndpointSpec("{baseUrl}/search?q={queryEncoded}", root = "items"),
        ),
        fields = mapOf("item.title" to FieldSpec(path = "title"), "item.url" to FieldSpec(path = "url")),
    )

    private fun redirect(status: Int, location: String) = SourceResponse(status, "", mapOf("Location" to location))

    private fun <T> SourceEngineResult<T>.valueOrFail(): T = when (this) {
        is SourceEngineResult.Success -> value
        is SourceEngineResult.Failure -> fail("Expected synthetic fixture success, got $error")
    }

    @Test
    fun every_allowed_get_redirect_status_is_followed_through_the_single_hop_port() = runTest {
        for (status in statuses) {
            val http = RecordingTransport { _, send -> if (send == 1) redirect(status, "/after") else SourceResponse(200, body) }
            val result = GenericSourceEngine(config(), http, FakeHeaderStore(captured)).home(1).valueOrFail()
            assertEquals("Fixture", result.single().title)
            assertEquals(listOf("https://api.test/start", "https://api.test/after"), http.requests.map { it.url })
            assertEquals(listOf(merged, merged), http.requests.map { it.headers })
            assertTrue(http.requests.all { it.method == SourceHttpMethod.GET && it.formBody == null && it.jsonBody == null })
        }
    }

    @Test
    fun trusted_foreign_trusted_chain_reselects_the_original_static_captured_and_filter_snapshot() = runTest {
        val providerMap = captured.toMutableMap()
        var reads = 0
        val source = config().copy(filters = listOf(
            FilterDefinition("precedence", "Fixture", "text", default = "filter", request = FilterRequestSpec("header", "X-Precedence")),
            FilterDefinition("extra", "Fixture", "text", default = "filter-only", request = FilterRequestSpec("header", "X-Filter")),
        ))
        val http = RecordingTransport { _, send ->
            when (send) {
                1 -> {
                    providerMap.clear()
                    providerMap["X-Later"] = "mutated"
                    redirect(302, "//foreign.test/step")
                }
                2 -> redirect(307, "https://img.test/done")
                else -> SourceResponse(200, body)
            }
        }
        GenericSourceEngine(source, http, SourceHeaderProvider { reads++; providerMap }).search("fixture", 1).valueOrFail()
        val expected = merged + mapOf("X-Precedence" to "filter", "X-Filter" to "filter-only")
        assertEquals(listOf("https://api.test/search?q=fixture", "https://foreign.test/step", "https://img.test/done"), http.requests.map { it.url })
        assertEquals(listOf(expected, emptyMap(), expected), http.requests.map { it.headers })
        assertEquals(1, reads)
    }

    @Test
    fun each_reference_resolves_against_the_current_requested_hop_not_final_url_metadata() = runTest {
        val source = config().copy(endpoints = mapOf("home" to EndpointSpec("https://api.test/a/b/start?original=1", root = "items")))
        val locations = listOf("../next?one=1", "?two=2", "#view", "//foreign.test/root/branch", "../final", "/absolute/./final", "https://api.test/back")
        val http = RecordingTransport { _, send ->
            if (send <= locations.size) redirect(302, locations[send - 1]).copy(finalUrl = "https://metadata.test/not-a-base/")
            else SourceResponse(200, body)
        }
        GenericSourceEngine(source, http, FakeHeaderStore(captured)).home(1).valueOrFail()
        assertEquals(listOf(
            "https://api.test/a/b/start?original=1", "https://api.test/a/next?one=1", "https://api.test/a/next?two=2",
            "https://api.test/a/next?two=2#view", "https://foreign.test/root/branch", "https://foreign.test/final",
            "https://foreign.test/absolute/final", "https://api.test/back",
        ), http.requests.map { it.url })
        assertEquals(listOf(merged, merged, merged, merged, emptyMap(), emptyMap(), emptyMap(), merged), http.requests.map { it.headers })
    }

    @Test
    fun location_lookup_is_case_insensitive_and_does_not_split_commas_in_a_single_uri() = runTest {
        for (key in listOf("Location", "location", "LOCATION")) {
            val http = RecordingTransport { _, send ->
                if (send == 1) SourceResponse(302, "", mapOf(key to "/after,a?value=b,c")) else SourceResponse(200, body)
            }
            GenericSourceEngine(config(), http, FakeHeaderStore(captured)).home(1).valueOrFail()
            assertEquals("https://api.test/after,a?value=b,c", http.requests.last().url)
            assertEquals(2, http.requests.size)
        }
    }

    @Test
    fun missing_location_remains_an_http_redirect_failure_without_a_second_send() = runTest {
        for (status in statuses) {
            val http = RecordingTransport { _, _ -> SourceResponse(status, "", mapOf("X-Other" to "fixture")) }
            val result = GenericSourceEngine(config(), http, FakeHeaderStore(captured)).home(1)
            assertEquals(SourceEngineResult.Failure(SourceEngineError.Http(status)), result)
            assertEquals(1, http.requests.size)
        }
    }

    @Test
    fun conflicting_case_variant_locations_fail_closed_while_identical_values_are_unambiguous() = runTest {
        val conflicting = RecordingTransport { _, _ -> SourceResponse(302, "", mapOf("Location" to "/one", "location" to "https://foreign.test/two")) }
        val result = GenericSourceEngine(config(), conflicting, FakeHeaderStore(captured)).home(1)
        assertEquals(SourceEngineResult.Failure(SourceEngineError.InvalidResponse), result)
        assertEquals(1, conflicting.requests.size)

        val identical = RecordingTransport { _, send ->
            if (send == 1) SourceResponse(302, "", mapOf("Location" to "/one", "location" to "/one")) else SourceResponse(200, body)
        }
        GenericSourceEngine(config(), identical, FakeHeaderStore(captured)).home(1).valueOrFail()
        assertEquals(listOf("https://api.test/start", "https://api.test/one"), identical.requests.map { it.url })
    }

    @Test
    fun unsafe_redirect_destinations_are_rejected_before_any_followup_transmission() = runTest {
        val locations = listOf(
            "https://user@api.test/after", "//%61pi.test/after", "//api.test:0/after", "//api.test:+443/after",
            "//api.test:65536/after", "https://api.test\\@foreign.test/after", " //foreign.test/after", "/after\n",
            "//127.1/after", "//0x7f000001/after", "//[fe80::1%25en0]/after", "///foreign.test/after",
            "https:foreign.test/after", "ftp://foreign.test/after", "next%GG",
        )
        for (location in locations) {
            val http = RecordingTransport { _, _ -> redirect(302, location) }
            val result = GenericSourceEngine(config(), http, FakeHeaderStore(captured)).home(1)
            assertEquals(SourceEngineResult.Failure(SourceEngineError.InvalidResponse), result)
            assertEquals(1, http.requests.size)
        }
    }

    @Test
    fun https_downgrade_is_stopped_even_when_the_http_origin_was_explicitly_signed() = runTest {
        val original = config()
        val sources = listOf(
            original.copy(imageBase = "http://images.test"),
            original.copy(baseUrl = "http://api.test", imageBase = "https://images.test", endpoints = mapOf("home" to EndpointSpec("https://images.test/start", root = "items"))),
        )
        for (source in sources) {
            val target = if (source.baseUrl.startsWith("http:")) "http://api.test/done" else "http://images.test/done"
            val http = RecordingTransport { _, _ -> redirect(302, target) }
            val result = GenericSourceEngine(source, http, FakeHeaderStore(captured)).home(1)
            assertEquals(SourceEngineResult.Failure(SourceEngineError.Http(302)), result)
            assertEquals(1, http.requests.size)
            assertEquals(merged, http.requests.single().headers)
        }
    }

    @Test
    fun an_http_upgrade_can_follow_but_does_not_implicitly_authorize_the_https_origin() = runTest {
        for (imageBase in listOf("", "https://api.test")) {
            val source = config().copy(baseUrl = "http://api.test", imageBase = imageBase)
            val http = RecordingTransport { _, send -> if (send == 1) redirect(301, "https://api.test/done") else SourceResponse(200, body) }
            GenericSourceEngine(source, http, FakeHeaderStore(captured)).home(1).valueOrFail()
            assertEquals(listOf("http://api.test/start", "https://api.test/done"), http.requests.map { it.url })
            assertEquals(listOf(merged, if (imageBase.isEmpty()) emptyMap() else merged), http.requests.map { it.headers })
        }
    }

    @Test
    fun public_header_free_sources_still_follow_valid_foreign_redirects_without_storage_reads() = runTest {
        val source = config().copy(headers = emptyMap(), usesCapturedHeaders = false)
        val http = RecordingTransport { _, send -> if (send == 1) redirect(302, "https://foreign.test/public") else SourceResponse(200, body) }
        val result = GenericSourceEngine(source, http, SourceHeaderProvider { fail("Storage must not be read") }).home(1).valueOrFail()
        assertEquals("Fixture", result.single().title)
        assertEquals(listOf("https://api.test/start", "https://foreign.test/public"), http.requests.map { it.url })
        assertTrue(http.requests.all { it.headers.isEmpty() })
    }

    @Test
    fun redirects_and_empty_reference_loops_share_a_cap_of_twenty_total_sends() = runTest {
        for (loop in listOf(false, true)) {
            var reads = 0
            val http = RecordingTransport { _, send -> redirect(302, if (loop) "" else "/hop/$send") }
            val result = GenericSourceEngine(config(), http, SourceHeaderProvider { reads++; captured }).home(1)
            assertEquals(SourceEngineResult.Failure(SourceEngineError.InvalidResponse), result)
            assertEquals(20, http.requests.size)
            assertEquals(if (loop) "https://api.test/start" else "https://api.test/hop/19", http.requests.last().url)
            assertEquals(1, reads)
        }
    }

    @Test
    fun the_twentieth_send_can_succeed_without_an_off_by_one_rejection() = runTest {
        val http = RecordingTransport { _, send -> if (send < 20) redirect(308, "/hop/$send") else SourceResponse(200, body) }
        GenericSourceEngine(config(), http, FakeHeaderStore(captured)).home(1).valueOrFail()
        assertEquals(20, http.requests.size)
        assertEquals("https://api.test/hop/19", http.requests.last().url)
    }

    @Test
    fun neither_post_body_type_is_replayed_or_rewritten_for_any_redirect_status() = runTest {
        for (method in listOf(SourceHttpMethod.POST_FORM, SourceHttpMethod.POST_JSON)) {
            for (foreign in listOf(false, true)) {
                for (status in statuses) {
                    val target = if (foreign) "https://foreign.test/submit" else "https://api.test/submit"
                    val form = method == SourceHttpMethod.POST_FORM
                    val endpoint = EndpointSpec(
                        url = target, method = if (form) "post-form" else "post-json", root = "items",
                        formBody = linkedMapOf("action" to "fixture", "q" to "{query}", "tag" to "static"),
                        jsonBody = """{"q":"{queryJson}","tag":"{tag_json}"}""",
                    )
                    val filter = if (form) {
                        FilterDefinition("tags", "Fixture", "multiselect", options = listOf(FilterOptionSpec("one"), FilterOptionSpec("two")),
                            defaults = listOf("one", "two"), request = FilterRequestSpec("form", "tag", encode = "repeat"))
                    } else {
                        FilterDefinition("tag", "Fixture", "text", default = "fixture\"value", request = FilterRequestSpec("body-json", "tag_json"))
                    }
                    val source = config().copy(endpoints = mapOf("search" to endpoint), filters = listOf(filter))
                    val http = RecordingTransport { _, _ -> redirect(status, "https://response-selected.test/replay") }
                    val result = GenericSourceEngine(source, http, FakeHeaderStore(captured)).search("a\"b\\c", 1)
                    assertEquals(SourceEngineResult.Failure(SourceEngineError.Http(status)), result)
                    val sent = http.requests.single()
                    assertEquals(target, sent.url)
                    assertEquals(method, sent.method)
                    assertEquals(if (foreign) emptyMap() else merged, sent.headers)
                    assertEquals(if (form) listOf("action" to "fixture", "q" to "a\"b\\c", "tag" to "static", "tag" to "one", "tag" to "two") else null, sent.formBody)
                    assertEquals(if (form) null else """{"q":"a\"b\\c","tag":"fixture\"value"}""", sent.jsonBody)
                }
            }
        }
    }

    @Test
    fun ordinary_http_transport_and_parse_failures_keep_existing_error_types() = runTest {
        for (status in listOf(300, 304, 305, 400, 401, 404, 500)) {
            val http = RecordingTransport { _, _ -> SourceResponse(status, "", mapOf("Location" to "/not-followed")) }
            assertEquals(SourceEngineResult.Failure(SourceEngineError.Http(status)), GenericSourceEngine(config(), http, FakeHeaderStore()).home(1))
            assertEquals(1, http.requests.size)
        }
        val failures = listOf(
            IllegalStateException("synthetic timeout") to SourceEngineError.Timeout,
            IllegalStateException("synthetic unknown host") to SourceEngineError.NoConnectivity,
            IllegalStateException("synthetic other") to SourceEngineError.Unexpected("IllegalStateException"),
        )
        for ((exception, expected) in failures) {
            val http = RecordingTransport { _, _ -> throw exception }
            assertEquals(SourceEngineResult.Failure(expected), GenericSourceEngine(config(), http, FakeHeaderStore()).home(1))
        }
        val malformed = RecordingTransport { _, _ -> SourceResponse(200, "{") }
        assertEquals(SourceEngineResult.Failure(SourceEngineError.InvalidResponse), GenericSourceEngine(config(), malformed, FakeHeaderStore()).home(1))
    }

    @Test
    fun challenges_keep_typed_failure_and_the_original_logical_request_signal() = runTest {
        for (status in listOf(403, 503)) {
            val signals = mutableListOf<Pair<String, String>>()
            val http = RecordingTransport { _, send ->
                if (send == 1) redirect(302, "https://foreign.test/challenge") else SourceResponse(status, "Just a moment - synthetic fixture")
            }
            val engine = GenericSourceEngine(config(), http, FakeHeaderStore(captured), SourceChallengeListener { api, url -> signals += api to url })
            assertEquals(SourceEngineResult.Failure(SourceEngineError.Http(403)), engine.home(1))
            assertEquals(listOf("redirect-fixture" to "https://api.test/start"), signals)
            assertEquals(listOf(merged, emptyMap()), http.requests.map { it.headers })
        }
    }

    @Test
    fun transport_cancellation_is_propagated_instead_of_classified_as_an_error() = runTest {
        val http = RecordingTransport { _, _ -> throw CancellationException("Synthetic cancellation") }
        val engine = GenericSourceEngine(config(), http, FakeHeaderStore(captured))
        assertFailsWith<CancellationException> { engine.home(1) }
        assertEquals(1, http.requests.size)
    }

    @Test
    fun cancellation_during_a_foreign_hop_does_not_execute_another_destination() = runTest {
        val entered = CompletableDeferred<Unit>()
        val http = RecordingTransport { _, send ->
            if (send == 1) redirect(302, "https://foreign.test/wait") else {
                entered.complete(Unit)
                awaitCancellation()
            }
        }
        val engine = GenericSourceEngine(config(), http, FakeHeaderStore(captured))
        val pending = async { engine.home(1) }
        entered.await()
        pending.cancel(CancellationException("Synthetic cancellation"))
        assertFailsWith<CancellationException> { pending.await() }
        pending.join()
        assertEquals(listOf("https://api.test/start", "https://foreign.test/wait"), http.requests.map { it.url })
        assertEquals(listOf(merged, emptyMap()), http.requests.map { it.headers })
    }

    @Test
    fun cancellation_between_hops_stops_even_a_transport_that_returns_after_cancellation() = runTest {
        val http = RecordingTransport { _, _ ->
            currentCoroutineContext().cancel(CancellationException("Synthetic cancellation"))
            redirect(302, "https://foreign.test/not-sent")
        }
        val engine = GenericSourceEngine(config(), http, FakeHeaderStore(captured))
        val pending = async { engine.home(1) }
        assertFailsWith<CancellationException> { pending.await() }
        pending.join()
        assertEquals(1, http.requests.size)
    }
}
