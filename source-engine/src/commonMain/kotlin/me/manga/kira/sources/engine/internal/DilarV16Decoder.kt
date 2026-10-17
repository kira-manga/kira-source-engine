package me.manga.kira.source.engine.internal

import kotlin.io.encoding.Base64
import kotlin.io.encoding.ExperimentalEncodingApi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.longOrNull
import me.manga.kira.source.contracts.SourceP256Session
import me.manga.kira.source.contracts.SourceSessionCryptography

/** Fixed, authenticated ECIES-v16 protocol; no negotiated downgrade or plaintext fallback. */
@OptIn(ExperimentalEncodingApi::class)
internal class DilarV16Decoder(private val crypto: SourceSessionCryptography) {
    suspend fun decrypt(body: String, session: SourceP256Session, clientPoint: ByteArray): String {
        val sensitive = mutableListOf<ByteArray>()
        fun secret(bytes: ByteArray): ByteArray = bytes.also { sensitive += it }
        try {
            currentCoroutineContext().ensureActive()
            require(body.length <= 2 * 1024 * 1024)
            require(clientPoint.size == 65 && clientPoint[0] == 4.toByte())
            val envelope = Json.parseToJsonElement(body) as? JsonObject ?: invalid()
            require(envelope.keys == setOf("v", "e", "epk", "iv", "ct", "tag"))
            fun number(key: String): Long {
                val value = envelope[key] as? JsonPrimitive ?: invalid()
                require(!value.isString)
                return value.longOrNull ?: invalid()
            }
            fun bytes(key: String, limit: Int): ByteArray {
                val value = envelope[key] as? JsonPrimitive ?: invalid()
                require(value.isString && value.content.length <= ((limit + 2) / 3) * 4)
                return decodeBase64Url(value.content).also { require(it.size <= limit) }
            }
            require(number("v") == 16L)
            val epoch = number("e")
            require(epoch in 0..9_007_199_254_740_991L) // Preserve the browser's exact integer text.
            val serverPoint = bytes("epk", 65)
            require(serverPoint.size == 65 && serverPoint[0] == 4.toByte())
            val iv = bytes("iv", 256)
            require(iv.isNotEmpty())
            val ciphertext = bytes("ct", 1024 * 1024)
            require(ciphertext.isNotEmpty())
            val tag = bytes("tag", 16)
            require(tag.size == 16)
            // The host imports/validates the point on P-256, not merely its length/prefix.
            val shared = secret(session.sharedSecret(serverPoint))
            require(shared.size == 32)
            val salt = crypto.hmacSha384(clientPoint + iv, lp(serverPoint)).also { require(it.size == 48) }
            val suffix = encodeBase64Url(crypto.sha384(lp(iv) + lp(serverPoint)).also { require(it.size == 48) }).take(22)
            val info = "$LABEL|$epoch|$suffix".encodeToByteArray()
            // RFC5869 extract/expand, SHA384, fixed L=56: exactly two blocks. No custom hash/MAC.
            val prk = secret(crypto.hmacSha384(salt, shared))
            require(prk.size == 48)
            val first = secret(crypto.hmacSha384(prk, info + byteArrayOf(1)))
            require(first.size == 48)
            val expandInput = secret(ByteArray(first.size + info.size + 1))
            first.copyInto(expandInput)
            info.copyInto(expandInput, first.size)
            expandInput[expandInput.lastIndex] = 2
            val second = secret(crypto.hmacSha384(prk, expandInput))
            require(second.size == 48)
            val key = secret(first.copyOfRange(0, 32))
            val nonce = secret(ByteArray(24))
            first.copyInto(nonce, destinationOffset = 0, startIndex = 32, endIndex = 48)
            second.copyInto(nonce, destinationOffset = 16, startIndex = 0, endIndex = 8)
            val aad = crypto.sha256(
                lp(LABEL.encodeToByteArray()) + lp("16".encodeToByteArray()) + lp(epoch.toString().encodeToByteArray()) +
                    lp(serverPoint) + lp(iv) + lp(u32(ciphertext.size)),
            ).also { require(it.size == 32) }
            val noncePrefix = secret(ByteArray(16))
            nonce.copyInto(noncePrefix, endIndex = 16)
            val subkey = secret(HChaCha20.derive(key, noncePrefix))
            val ietfNonce = secret(ByteArray(12))
            nonce.copyInto(ietfNonce, destinationOffset = 4, startIndex = 16, endIndex = 24)
            currentCoroutineContext().ensureActive()
            // XChaCha construction: HChaCha subkey + 00000000 || final8 nonce bytes, same AAD/tag.
            val plaintext = secret(crypto.decryptChaCha20Poly1305(subkey, ietfNonce, ciphertext + tag, aad))
            currentCoroutineContext().ensureActive()
            return plaintext.decodeToString(throwOnInvalidSequence = true)
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (_: Exception) {
            // Never include provider messages, key material, ciphertext or upstream payloads.
            throw InvalidEncryptedSourceResponse()
        } finally {
            sensitive.forEach { it.fill(0) }
        }
    }

    companion object {
        private const val LABEL = "dilar.response.ecies.v16"
        private val BASE64URL = Regex("[A-Za-z0-9_-]+={0,2}")

        fun encodeBase64Url(bytes: ByteArray): String = Base64.UrlSafe.encode(bytes).trimEnd('=')

        private fun decodeBase64Url(value: String): ByteArray {
            require(BASE64URL.matches(value))
            val raw = value.trimEnd('=')
            require(raw.length % 4 != 1)
            require(value == raw || value.length % 4 == 0)
            val bytes = Base64.UrlSafe.decode(raw + "=".repeat((4 - raw.length % 4) % 4))
            require(encodeBase64Url(bytes) == raw) // Reject noncanonical trailing bits.
            return bytes
        }

        private fun lp(bytes: ByteArray): ByteArray {
            require(bytes.size <= 65535)
            return byteArrayOf((bytes.size ushr 8).toByte(), bytes.size.toByte()) + bytes
        }

        private fun u32(value: Int): ByteArray = ByteArray(4) { (value ushr ((3 - it) * 8)).toByte() }

        private fun invalid(): Nothing = throw IllegalArgumentException()
    }
}

internal class InvalidEncryptedSourceResponse : Exception()
