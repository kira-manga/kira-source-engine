package me.manga.kira.sources.testkit

import kotlinx.coroutines.test.runTest
import me.manga.kira.source.contracts.SourceHttpMethod
import me.manga.kira.source.contracts.SourceRequest
import me.manga.kira.source.contracts.SourceResponse
import kotlin.test.Test
import kotlin.test.assertEquals

class FixtureTransportTest {
    @Test
    fun matches_method_and_url_and_records_requests() = runTest {
        val key = FixtureRequestKey(SourceHttpMethod.POST_JSON, "https://example.test/search")
        val expected = SourceResponse(status = 200, body = """{"items":[]}""")
        val transport = FixtureSourceTransport(mapOf(key to expected))
        val request =
            SourceRequest(
                url = key.url,
                method = key.method,
                jsonBody = """{"query":"kira"}""",
            )

        assertEquals(expected, transport.execute(request))
        assertEquals(listOf(request), transport.requests)
    }

    @Test
    fun unknown_request_fails_closed() = runTest {
        val transport = FixtureSourceTransport(emptyMap())

        val response = transport.execute(SourceRequest("https://example.test/unknown"))

        assertEquals(404, response.status)
        assertEquals("", response.body)
    }
}
