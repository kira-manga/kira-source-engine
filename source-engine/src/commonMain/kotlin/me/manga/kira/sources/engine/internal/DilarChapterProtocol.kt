package me.manga.kira.source.engine.internal

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.longOrNull
import me.manga.kira.source.contracts.SourceEngineError
import me.manga.kira.source.contracts.SourceEngineResult
import me.manga.kira.source.contracts.SourceHttpMethod
import me.manga.kira.source.contracts.SourceRequest
import me.manga.kira.source.contracts.SourceResponse
import me.manga.kira.source.contracts.SourceSensitiveTransport
import me.manga.kira.source.contracts.SourceSessionCryptography

/** Compiled ordinary reader protocol; not a script engine, account client, or unlock bypass. */
internal class DilarChapterProtocol(
    private val crypto: SourceSessionCryptography,
    private val transport: SourceSensitiveTransport,
) {
    suspend fun read(url: String, seriesId: String): SourceEngineResult<SourceResponse> {
        val releaseId = READER_URL.matchEntire(url)?.groupValues?.get(1)
            ?: return SourceEngineResult.Failure(SourceEngineError.InvalidResponse)
        if (!ID.matches(seriesId)) return SourceEngineResult.Failure(SourceEngineError.InvalidResponse)
        currentCoroutineContext().ensureActive()
        val session = crypto.createP256Session()
        try {
            val point = session.publicKey.copyOf()
            require(point.size == 65 && point[0] == 4.toByte())
            val headers = mapOf(
                "Accept" to "application/json",
                "X-DH-Pub" to DilarV16Decoder.encodeBase64Url(point),
                "X-Crypto-Caps" to "16",
            )
            val decoder = DilarV16Decoder(crypto)
            suspend fun reader(freePass: String? = null): JsonObject {
                val selectedHeaders = if (freePass == null) headers else headers + ("X-Unlock-Free-Chapter" to freePass)
                val response = send(SourceRequest(url, headers = selectedHeaders))
                val value = objectOf(decoder.decrypt(response.body, session, point))
                require(value.text("id") == releaseId && value.text("series_id") == seriesId)
                accessGuard(value)
                return value
            }
            var payload = reader()
            if (payload.flag("free_pass_required")) {
                // Only this explicitly indicated ordinary flow is supported. No stored/guessed grant,
                // enrollment, shortener or user-account flow, and no transport retry of the POST.
                val response = send(SourceRequest(
                    "$url/unlock/free",
                    SourceHttpMethod.POST_JSON,
                    headers + ("Content-Type" to "application/json"),
                    jsonBody = "{}",
                ))
                val root = objectOf(response.body)
                val grant = if (root.keys.any { it in ENVELOPE_FIELDS }) {
                    objectOf(decoder.decrypt(response.body, session, point))
                } else root
                val token = grant.text("token")
                require(token.isNotEmpty() && token.length <= 8192 && token.all { it.code in 33..126 })
                val expires = grant["expires_at"] as? JsonPrimitive ?: throw IllegalArgumentException()
                require(!expires.isString && (expires.longOrNull ?: 0) in 1..9_007_199_254_740_991L)
                // The freshly issued token is used once immediately, never cached/persisted. The
                // server remains authoritative for expiry; a rejected/expired grant cannot succeed.
                payload = reader(token)
            }
            if (payload.flag("free_pass_required")) throw AccessRequired("free-pass")
            val pages = ordinaryPages(payload)
            // Normalize only authenticated, supported ordinary assets. Configs cannot accidentally
            // extract unqualified scheme2/encrypted metadata or turn a withheld response into [] PASS.
            return SourceEngineResult.Success(SourceResponse(200, JsonObject(mapOf("pages" to pages)).toString()))
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (failure: HttpFailure) {
            return SourceEngineResult.Failure(SourceEngineError.Http(failure.status))
        } catch (failure: AccessRequired) {
            return SourceEngineResult.Failure(SourceEngineError.Required("source-access:${failure.kind}"))
        } catch (_: InvalidEncryptedSourceResponse) {
            return SourceEngineResult.Failure(SourceEngineError.InvalidResponse)
        } catch (_: IllegalArgumentException) {
            return SourceEngineResult.Failure(SourceEngineError.InvalidResponse)
        } finally {
            session.close()
        }
    }

    private suspend fun send(request: SourceRequest): SourceResponse {
        currentCoroutineContext().ensureActive()
        val response = transport.execute(request, MAX_RESPONSE_BYTES)
        currentCoroutineContext().ensureActive()
        require(response.body.length <= MAX_RESPONSE_BYTES)
        require(response.finalUrl == null || response.finalUrl == request.url)
        if (response.status !in 200..299) throw HttpFailure(response.status)
        return response
    }

    private fun accessGuard(value: JsonObject) {
        if (value.flag("app_exclusive")) throw AccessRequired("app-exclusive")
        if (value.flag("lock") || (value["showable"] != null && value["showable"] != JsonNull && !value.flag("showable"))) {
            throw AccessRequired("locked")
        }
        if (value.flag("has_rev_link") && value.text("storage_key").isEmpty()) throw AccessRequired("shortener")
        if (value["assets_enc"] != null && value["assets_enc"] != JsonNull) throw AccessRequired("encrypted-assets")
        val scheme = value["scheme"]
        if (scheme != null && scheme != JsonNull && (scheme as? JsonObject)?.get("id") != JsonPrimitive(1)) {
            throw AccessRequired("image-scheme")
        }
    }

    private fun ordinaryPages(value: JsonObject): JsonArray {
        val storage = value.text("storage_key").split('/')
        require(storage.size == 2 && ID.matches(storage[0]) && SEGMENT.matches(storage[1]))
        require(storage[1] != "." && storage[1] != "..")
        val token = value.text("media_token")
        require(token.isNotEmpty() && token.length <= 8192 && token.none { it.code < 32 || it.code == 127 })
        fun array(key: String): JsonArray = when (val element = value[key]) {
            null, JsonNull -> JsonArray(emptyList())
            is JsonArray -> element
            else -> throw IllegalArgumentException()
        }
        val pages = array("pages")
        val webp = array("webp_pages").map {
            val name = it as? JsonPrimitive ?: throw IllegalArgumentException()
            require(name.isString && name.content.isNotEmpty())
            name.content
        }
        require(pages.size <= 2000 && webp.size <= 2000)
        // Current ordinary webp entries are strings, not old-parser {url,order} objects.
        // Absolute entries are already complete URLs: never append/transfer the media token.
        if (webp.isNotEmpty() && webp.all { it.startsWith("http") }) {
            val prefix = "$ORIGIN/uploads/releases/${storage[0]}/${storage[1]}/"
            require(webp.distinct().size == webp.size)
            return JsonArray(webp.mapIndexed { index, url ->
                require(url.startsWith(prefix) && '#' !in url && HttpDestination.parse(url) != null)
                val components = url.removePrefix(prefix).substringBefore('?').split('/')
                require(components.size == 2 && components.all { SEGMENT.matches(it) && it != "." && it != ".." })
                normalizedPage(url, index)
            })
        }
        require(pages.isNotEmpty())
        val alignedWebp = webp.isNotEmpty() && webp.size == pages.size
        val paths = mutableSetOf<String>()
        return JsonArray(pages.mapIndexed { index, element ->
            val page = element as? JsonObject ?: throw IllegalArgumentException()
            val original = page.text("url")
            val file = if (alignedWebp) webp[index] else original
            require(SEGMENT.matches(file) && file != "." && file != ".." && paths.add(file))
            // The website uses an alternate plain webp when available. Its s2/dir branch is
            // a different image transform: do not present scrambled bytes as ordinary pages.
            if (file == original && page["s2"] != null && page["s2"] != JsonNull) throw AccessRequired("image-scheme")
            // Ordinary images use hq even for .webp; dir applies only to the separate s2 path.
            // Array order is the current reader contract. Normalized indices also prevent a
            // generic page.order declaration from re-sorting by obsolete provider metadata.
            normalizedPage("$ORIGIN/uploads/releases/${storage[0]}/${storage[1]}/hq/$file?t=${UrlEncode.encode(token)}", index)
        })
    }

    private fun normalizedPage(url: String, index: Int): JsonObject = JsonObject(mapOf(
        "url" to JsonPrimitive(url), "order" to JsonPrimitive(index),
    ))

    private fun JsonObject.text(key: String): String = (get(key) as? JsonPrimitive)?.let {
        if (it.isString) it.content else ""
    }.orEmpty()

    private fun JsonObject.flag(key: String): Boolean {
        val value = get(key) ?: return false
        if (value == JsonNull) return false
        val primitive = value as? JsonPrimitive ?: throw IllegalArgumentException()
        require(!primitive.isString)
        return primitive.booleanOrNull ?: throw IllegalArgumentException()
    }

    private fun objectOf(body: String): JsonObject = Json.parseToJsonElement(body) as? JsonObject ?: throw IllegalArgumentException()
    private class AccessRequired(val kind: String) : Exception()
    private class HttpFailure(val status: Int) : Exception()

    companion object {
        const val FORMAT = "dilar-ecies-v16"
        private const val ORIGIN = "https://dilar.tube"
        private const val MAX_RESPONSE_BYTES = 2 * 1024 * 1024
        private val ID = Regex("[1-9][0-9]{0,18}")
        private val READER_URL = Regex("https://dilar\\.tube/api/chapters/([1-9][0-9]{0,18})")
        private val SEGMENT = Regex("[A-Za-z0-9_.-]{1,160}")
        private val ENVELOPE_FIELDS = setOf("v", "epk", "iv", "ct", "tag")
    }
}
