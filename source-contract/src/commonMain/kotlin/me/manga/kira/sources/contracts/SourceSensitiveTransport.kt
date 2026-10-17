package me.manga.kira.source.contracts

/**
 * Explicit host admission for a sensitive source protocol. A regular [SourceTransport] is NOT an
 * implicit substitute: this port must use a client with no automatic redirects, authentication,
 * cookies, retries, HTTP logging, URL/header tracing or persistent response cache. Execute exactly
 * one hop/attempt, honor cancellation, and enforce [maxResponseBytes] while reading, before creating
 * the response String. Caller-owned session headers must never be retained or forwarded elsewhere.
 */
fun interface SourceSensitiveTransport {
    suspend fun execute(request: SourceRequest, maxResponseBytes: Int): SourceResponse
}
