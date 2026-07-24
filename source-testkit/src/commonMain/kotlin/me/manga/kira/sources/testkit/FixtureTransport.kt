package me.manga.kira.sources.testkit

import me.manga.kira.source.contracts.SourceBaseUrlProvider
import me.manga.kira.source.contracts.SourceHeaderProvider
import me.manga.kira.source.contracts.SourceHttpMethod
import me.manga.kira.source.contracts.SourceRequest
import me.manga.kira.source.contracts.SourceResponse
import me.manga.kira.source.contracts.SourceTransport

/**
 * Exact request key used by [FixtureSourceTransport].
 *
 * Matching both method and URL prevents a preview or parity test from accidentally accepting the
 * right endpoint with the wrong HTTP verb.
 */
data class FixtureRequestKey(
    val method: SourceHttpMethod,
    val url: String,
)

/**
 * Deterministic transport for backend, app, and shared-engine parity tests.
 *
 * Unknown requests fail closed with HTTP 404. Every request is retained in order so callers can
 * assert headers and request bodies without using a real network.
 */
class FixtureSourceTransport(
    private val responses: Map<FixtureRequestKey, SourceResponse>,
) : SourceTransport {
    val requests: MutableList<SourceRequest> = mutableListOf()

    override suspend fun execute(request: SourceRequest): SourceResponse {
        requests += request
        return responses[FixtureRequestKey(request.method, request.url)]
            ?: SourceResponse(status = 404, body = "")
    }
}

class StaticSourceHeaderProvider(
    private val values: Map<String, Map<String, String>> = emptyMap(),
) : SourceHeaderProvider {
    override suspend fun headersFor(api: String): Map<String, String> = values[api].orEmpty()
}

class StaticSourceBaseUrlProvider(
    private val values: Map<String, String> = emptyMap(),
) : SourceBaseUrlProvider {
    override suspend fun baseUrlFor(api: String): String? = values[api]
}
