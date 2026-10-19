package me.manga.kira.source.engine

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertIs
import kotlin.test.assertSame
import kotlin.test.assertTrue
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.test.runTest
import me.manga.kira.source.contracts.*
import me.manga.kira.source.contracts.model.*
import me.manga.kira.source.engine.internal.DilarChapterProtocol

/** Control-flow/transport tests with synthetic crypto ports. Real AEAD vectors are tested separately. */
class DilarChapterProtocolTest {
    private val readerUrl = "https://dilar.tube/api/chapters/42"
    private val manga = SourceMangaRef("Dilar", "(AR)", "Fixture", "https://dilar.tube/api/mangas/7", "")
    private val chapter = SourceChapter("1", "", "https://dilar.tube/r/42", null)
    private val config = SourceConfig(
        api = "Dilar", language = "(AR)", baseUrl = "https://dilar.tube", engine = "generic", usesCapturedHeaders = false,
        endpoints = mapOf(
            "home" to EndpointSpec("{baseUrl}/api/series/", format = "json", root = "series"),
            "details" to EndpointSpec("{baseUrl}/api/series/{id}", format = "json"),
            "pages" to EndpointSpec("{baseUrl}/api/chapters/{chapterId}", format = DilarChapterProtocol.FORMAT, root = "pages"),
        ),
        fields = mapOf(
            "item.title" to FieldSpec(path = "title"), "item.url" to FieldSpec(path = "url"),
            "detail.title" to FieldSpec(path = "title"), "chapter.number" to FieldSpec(path = "chapter"),
            "chapter.url" to FieldSpec(path = "url"), "page.image" to FieldSpec(path = "url"),
            "page.order" to FieldSpec(path = "order"),
        ),
    )
    private val ready = """{"id":"42","series_id":"7","storage_key":"123/fixture","media_token":"synthetic +&/?","pages":[{"url":"002.webp","order":2},{"url":"001.webp","order":1}],"webp_pages":[],"showable":true,"encoded":true}"""
    private val needsPass = """{"id":"42","series_id":"7","free_pass_required":true,"has_rev_link":false,"pages":[]}"""
    private val grant = """{"token":"synthetic-free-pass","expires_at":1900000000000}"""
    // Well-shaped envelope only. These ports intentionally do not claim real authentication.
    private val envelope = """{"v":16,"e":1729000000,"epk":"BAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","iv":"AA","ct":"AQ","tag":"AAAAAAAAAAAAAAAAAAAAAA"}"""

    @Test
    fun indicated_free_pass_has_three_confined_sends_and_preserves_saved_identity() = runTest {
        val harness = Harness(listOf(needsPass, ready), listOf(SourceResponse(200, envelope), SourceResponse(200, grant), SourceResponse(200, envelope)))
        val result = assertIs<SourceEngineResult.Success<List<SourcePage>>>(engine(harness).pages(manga, chapter))
        assertEquals(listOf(
            "https://dilar.tube/uploads/releases/123/fixture/hq/002.webp?t=synthetic%20%2B%26%2F%3F",
            "https://dilar.tube/uploads/releases/123/fixture/hq/001.webp?t=synthetic%20%2B%26%2F%3F",
        ), result.value.map { it.url })
        assertTrue(result.value.all { it.headers.isEmpty() })
        assertEquals(listOf(readerUrl, "$readerUrl/unlock/free", readerUrl), harness.requests.map { it.url })
        assertEquals(listOf(SourceHttpMethod.GET, SourceHttpMethod.POST_JSON, SourceHttpMethod.GET), harness.requests.map { it.method })
        assertEquals("{}", harness.requests[1].jsonBody)
        assertEquals("synthetic-free-pass", harness.requests[2].headers["X-Unlock-Free-Chapter"])
        assertTrue(harness.requests.take(2).all { "X-Unlock-Free-Chapter" !in it.headers })
        assertEquals(1, harness.requests.map { it.headers["X-DH-Pub"] }.distinct().size)
        assertTrue(harness.requests.all { it.headers["X-Crypto-Caps"] == "16" })
        assertEquals(1, harness.created)
        assertEquals(1, harness.closed)
        assertEquals("https://dilar.tube/r/42", chapter.url)
        assertEquals("https://dilar.tube/api/mangas/7", manga.url)
    }

    @Test
    fun ordinary_webp_strings_use_hq_array_order_and_misaligned_entries_fall_back() = runTest {
        val aligned = ready.replace("\"webp_pages\":[]", "\"webp_pages\":[\"alternate-2.webp\",\"alternate-1.webp\"]")
        val mismatched = ready.replace("\"webp_pages\":[]", "\"webp_pages\":[\"unused.webp\"]")
        for ((body, expected) in listOf(
            aligned to listOf("alternate-2.webp", "alternate-1.webp"),
            mismatched to listOf("002.webp", "001.webp"),
        )) {
            val harness = Harness(listOf(body), listOf(SourceResponse(200, envelope)))
            val pages = assertIs<SourceEngineResult.Success<List<SourcePage>>>(engine(harness).pages(manga, chapter)).value
            assertEquals(expected.map { "https://dilar.tube/uploads/releases/123/fixture/hq/$it?t=synthetic%20%2B%26%2F%3F" }, pages.map { it.url })
            assertEquals(1, harness.closed)
        }
    }

    @Test
    fun complete_webp_urls_are_not_given_tokens_and_scrambled_originals_are_not_plain_pages() = runTest {
        val complete = listOf("https://dilar.tube/uploads/releases/123/fixture/hq/a.webp?existing=1", "https://dilar.tube/uploads/releases/123/fixture/hq/b.webp")
        val body = ready.replace("\"webp_pages\":[]", "\"webp_pages\":[\"${complete[0]}\",\"${complete[1]}\"]")
        val harness = Harness(listOf(body), listOf(SourceResponse(200, envelope)))
        assertEquals(complete, assertIs<SourceEngineResult.Success<List<SourcePage>>>(engine(harness).pages(manga, chapter)).value.map { it.url })
        for (bad in listOf(body.replace("https://dilar.tube/uploads", "https://other.example/uploads"), body.replace("/hq/a.webp", "/../a.webp"))) {
            val invalid = Harness(listOf(bad), listOf(SourceResponse(200, envelope)))
            assertEquals(SourceEngineResult.Failure(SourceEngineError.InvalidResponse), engine(invalid).pages(manga, chapter))
        }
        val scrambled = ready.replace("\"url\":\"002.webp\"", "\"url\":\"002.webp\",\"s2\":{\"n\":4},\"dir\":\"tiles\"")
        val blocked = Harness(listOf(scrambled), listOf(SourceResponse(200, envelope)))
        assertEquals(SourceEngineResult.Failure(SourceEngineError.Required("source-access:image-scheme")), engine(blocked).pages(manga, chapter))
        val plainAlternative = scrambled.replace("\"webp_pages\":[]", "\"webp_pages\":[\"plain-2.webp\",\"plain-1.webp\"]")
        val plain = Harness(listOf(plainAlternative), listOf(SourceResponse(200, envelope)))
        assertTrue(assertIs<SourceEngineResult.Success<List<SourcePage>>>(engine(plain).pages(manga, chapter)).value.all { "/hq/plain-" in it.url })
    }

    @Test
    fun ready_reader_never_calls_unlock_and_each_operation_owns_a_new_session() = runTest {
        val harness = Harness(listOf(ready, ready), List(2) { SourceResponse(200, envelope) })
        val client = engine(harness)
        repeat(2) { assertIs<SourceEngineResult.Success<*>>(client.pages(manga, chapter)) }
        assertEquals(listOf(readerUrl, readerUrl), harness.requests.map { it.url })
        assertEquals(2, harness.requests.map { it.headers["X-DH-Pub"] }.distinct().size)
        assertEquals(2, harness.created)
        assertEquals(2, harness.closed)
    }

    @Test
    fun redirects_and_access_statuses_are_not_replayed_or_reinterpreted() = runTest {
        for (status in listOf(302, 307, 401, 403, 409, 426, 428, 429)) {
            val harness = Harness(emptyList(), listOf(SourceResponse(status, "denied", mapOf("Location" to "https://other.example/"))))
            assertEquals(SourceEngineResult.Failure(SourceEngineError.Http(status)), engine(harness).pages(manga, chapter))
            assertEquals(1, harness.requests.size)
            assertEquals(1, harness.closed)
        }
        val harness = Harness(listOf(needsPass), listOf(SourceResponse(200, envelope), SourceResponse(409, "locked")))
        assertEquals(SourceEngineResult.Failure(SourceEngineError.Http(409)), engine(harness).pages(manga, chapter))
        assertEquals(2, harness.requests.size)
        assertEquals(1, harness.closed)
    }

    @Test
    fun protected_or_unqualified_assets_never_become_empty_reader_success() = runTest {
        for (field in listOf("\"app_exclusive\":true", "\"has_rev_link\":true", "\"showable\":false", "\"assets_enc\":{}", "\"scheme\":{\"id\":2}")) {
            val body = """{"id":"42","series_id":"7",$field,"pages":[]}"""
            val harness = Harness(listOf(body), listOf(SourceResponse(200, envelope)))
            assertIs<SourceEngineError.Required>(assertIs<SourceEngineResult.Failure>(engine(harness).pages(manga, chapter)).error)
            assertEquals(1, harness.requests.size)
            assertEquals(1, harness.closed)
        }
    }

    @Test
    fun wrong_identity_bad_grant_and_wrong_final_destination_fail_closed() = runTest {
        for (body in listOf(ready.replace("\"42\"", "\"43\""), ready.replace("\"7\"", "\"8\""), ready.replace("001.webp", "../001.webp"), ready.replace("\"showable\":true", "\"showable\":\"false\""))) {
            val harness = Harness(listOf(body), listOf(SourceResponse(200, envelope)))
            assertEquals(SourceEngineResult.Failure(SourceEngineError.InvalidResponse), engine(harness).pages(manga, chapter))
            assertEquals(1, harness.closed)
        }
        val invalidGrant = Harness(listOf(needsPass), listOf(SourceResponse(200, envelope), SourceResponse(200, """{"token":"","expires_at":1}""")))
        assertEquals(SourceEngineResult.Failure(SourceEngineError.InvalidResponse), engine(invalidGrant).pages(manga, chapter))
        assertEquals(2, invalidGrant.requests.size)
        val moved = Harness(emptyList(), listOf(SourceResponse(200, envelope, finalUrl = "https://other.example/")))
        assertEquals(SourceEngineResult.Failure(SourceEngineError.InvalidResponse), engine(moved).pages(manga, chapter))
        assertEquals(1, moved.closed)
    }

    @Test
    fun cancellation_closes_session_and_keeps_the_original_exception() = runTest {
        val cancelled = CancellationException("synthetic cancellation")
        val harness = Harness(emptyList(), emptyList(), cancelled)
        assertSame(cancelled, assertFailsWith<CancellationException> { engine(harness).pages(manga, chapter) })
        assertEquals(1, harness.requests.size)
        assertEquals(1, harness.closed)
    }

    @Test
    fun format_admission_and_missing_private_transport_never_fall_back() = runTest {
        val validator = DefaultSourceConfigValidator(DefaultStrategyRegistry())
        assertTrue(validator.validate(SourceConfigDocument(1, sources = listOf(config))).isValid)
        val endpoint = config.endpoints.getValue("pages")
        val bad = listOf(
            config.copy(usesCapturedHeaders = true), config.copy(baseUrl = "https://other.example"),
            config.copy(headers = mapOf("Cookie" to "synthetic")),
            config.copy(endpoints = config.endpoints + ("home" to endpoint)),
            config.copy(endpoints = config.endpoints + ("pages" to endpoint.copy(url = "https://other.example/42"))),
        )
        assertTrue(bad.all { !validator.validate(SourceConfigDocument(1, sources = listOf(it))).isValid })
        val client = GenericSourceEngine(config, { error("Regular transport must not be used") }, { error("Captured headers must not be read") })
        assertEquals(SourceEngineResult.Failure(SourceEngineError.Required("capability:sensitive-transport")), client.pages(manga, chapter))
        val harness = Harness(emptyList(), emptyList())
        val moved = config.copy(endpoints = config.endpoints + ("pages" to endpoint.copy(url = "https://other.example/api/chapters/{chapterId}")))
        assertEquals(SourceEngineResult.Failure(SourceEngineError.InvalidResponse), engine(harness, moved).pages(manga, chapter))
        assertEquals(0, harness.created)
        assertTrue(harness.requests.isEmpty())
    }

    private fun engine(harness: Harness, source: SourceConfig = config): GenericSourceEngine = GenericSourceEngine(
        source, { error("Regular transport must not be used") }, { error("Captured credentials must not be read") },
        sensitiveHttp = harness, sessionCryptography = harness,
    )

    private class Harness(
        plaintexts: List<String>, responses: List<SourceResponse>, private val cancellation: CancellationException? = null,
    ) : SourceSensitiveTransport, SourceSessionCryptography {
        val requests = mutableListOf<SourceRequest>()
        private val plaintexts = ArrayDeque(plaintexts)
        private val responses = ArrayDeque(responses)
        var created = 0
        var closed = 0
        override suspend fun execute(request: SourceRequest, maxResponseBytes: Int): SourceResponse {
            assertEquals(2 * 1024 * 1024, maxResponseBytes)
            requests += request
            cancellation?.let { throw it }
            return responses.removeFirst()
        }
        override suspend fun createP256Session(): SourceP256Session {
            val id = ++created
            return object : SourceP256Session {
                override val publicKey: ByteArray get() = ByteArray(65).also { it[0] = 4; it[64] = id.toByte() }
                override suspend fun sharedSecret(peerPublicKey: ByteArray): ByteArray = ByteArray(32)
                override fun close() { closed++ }
            }
        }
        override suspend fun sha256(input: ByteArray): ByteArray = ByteArray(32)
        override suspend fun sha384(input: ByteArray): ByteArray = ByteArray(48)
        override suspend fun hmacSha384(key: ByteArray, input: ByteArray): ByteArray = ByteArray(48)
        override suspend fun decryptChaCha20Poly1305(key: ByteArray, nonce: ByteArray, ciphertextAndTag: ByteArray, associatedData: ByteArray): ByteArray =
            plaintexts.removeFirst().encodeToByteArray()
    }
}
