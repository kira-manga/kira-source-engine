package me.manga.kira.source.engine

import java.math.BigInteger
import java.security.AlgorithmParameters
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec
import java.security.spec.ECPoint
import java.security.spec.ECPrivateKeySpec
import java.security.spec.ECPublicKeySpec
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyAgreement
import javax.crypto.Mac
import javax.crypto.spec.IvParameterSpec
import javax.crypto.spec.SecretKeySpec
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertSame
import kotlin.test.assertTrue
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import me.manga.kira.source.contracts.SourceP256Session
import me.manga.kira.source.contracts.SourceSessionCryptography
import me.manga.kira.source.engine.internal.DilarV16Decoder
import me.manga.kira.source.engine.internal.InvalidEncryptedSourceResponse

/** Real JDK primitives vs independently generated Python/libsodium ciphertext; no network. */
class DilarV16DecoderTest {
    private val fixture = Json.parseToJsonElement(
        requireNotNull(javaClass.getResourceAsStream("/dilar-v16-vector.json")).use { it.readBytes().decodeToString() },
    ).jsonObject
    private val envelope = fixture.getValue("envelope").jsonObject
    private val point = Base64.getUrlDecoder().decode(fixture.getValue("clientPublicKey").jsonPrimitive.content)

    @Test
    fun authenticates_independent_libsodium_vector_and_clears_owned_secret_buffers() = runTest {
        val crypto = JdkCrypto()
        val result = DilarV16Decoder(crypto).decrypt(envelope.toString(), crypto.session(), point)
        assertEquals(fixture.getValue("expectedPlaintext").jsonPrimitive.content, result)
        assertEquals(1, crypto.decryptions)
        assertTrue(crypto.returnedSecrets.size >= 5)
        assertTrue(crypto.returnedSecrets.all { buffer -> buffer.all { it == 0.toByte() } })
        assertTrue(requireNotNull(crypto.aeadKey).all { it == 0.toByte() })
    }

    @Test
    fun refuses_modified_ciphertext_tag_and_epoch_without_plaintext() = runTest {
        for (field in listOf("ct", "tag", "e")) {
            val edited = envelope.toMutableMap()
            edited[field] = if (field == "e") JsonPrimitive(1729000001) else {
                val bytes = Base64.getUrlDecoder().decode(envelope.getValue(field).jsonPrimitive.content)
                bytes[0] = (bytes[0].toInt() xor 1).toByte()
                JsonPrimitive(Base64.getUrlEncoder().withoutPadding().encodeToString(bytes))
            }
            val crypto = JdkCrypto()
            assertFailsWith<InvalidEncryptedSourceResponse>(field) {
                DilarV16Decoder(crypto).decrypt(JsonObject(edited).toString(), crypto.session(), point)
            }
            assertTrue(crypto.returnedSecrets.all { buffer -> buffer.all { it == 0.toByte() } })
        }
    }

    @Test
    fun malformed_or_unknown_envelopes_never_reach_key_agreement() = runTest {
        val cases = listOf(
            JsonObject(envelope + ("v" to JsonPrimitive(12))),
            JsonObject(envelope + ("e" to JsonPrimitive("1729000000"))),
            JsonObject(envelope + ("e" to JsonPrimitive(9007199254740992L))),
            JsonObject(envelope + ("tag" to JsonPrimitive("not base64!"))),
            JsonObject(envelope - "epk"),
            JsonObject(envelope + ("unexpected" to JsonPrimitive(true))),
        )
        for (value in cases) {
            val crypto = JdkCrypto()
            assertFailsWith<InvalidEncryptedSourceResponse> {
                DilarV16Decoder(crypto).decrypt(value.toString(), crypto.session(), point)
            }
            assertEquals(0, crypto.agreements)
            assertEquals(0, crypto.decryptions)
        }
    }

    @Test
    fun invalid_curve_point_is_rejected_by_the_real_provider() = runTest {
        val invalidPoint = ByteArray(65).also { it[0] = 4 }
        val edited = JsonObject(envelope + ("epk" to JsonPrimitive(Base64.getUrlEncoder().withoutPadding().encodeToString(invalidPoint))))
        val crypto = JdkCrypto()
        assertFailsWith<InvalidEncryptedSourceResponse> {
            DilarV16Decoder(crypto).decrypt(edited.toString(), crypto.session(), point)
        }
        assertEquals(1, crypto.agreements)
        assertEquals(0, crypto.decryptions)
    }

    @Test
    fun cancellation_preserves_identity_and_clears_the_derived_secret() = runTest {
        val cancelled = CancellationException("synthetic cancellation")
        val crypto = JdkCrypto(cancelled)
        val actual = assertFailsWith<CancellationException> {
            DilarV16Decoder(crypto).decrypt(envelope.toString(), crypto.session(), point)
        }
        assertSame(cancelled, actual)
        assertTrue(crypto.returnedSecrets.isNotEmpty())
        assertTrue(crypto.returnedSecrets.all { buffer -> buffer.all { it == 0.toByte() } })
        assertEquals(0, crypto.decryptions)
    }

    private inner class JdkCrypto(private val cancellation: CancellationException? = null) : SourceSessionCryptography {
        var agreements = 0
        var decryptions = 0
        var macs = 0
        var aeadKey: ByteArray? = null
        val returnedSecrets = mutableListOf<ByteArray>()

        fun session(): SourceP256Session = object : SourceP256Session {
            override val publicKey: ByteArray get() = point.copyOf()

            override suspend fun sharedSecret(peerPublicKey: ByteArray): ByteArray {
                agreements++
                val parameters = AlgorithmParameters.getInstance("EC").apply { init(ECGenParameterSpec("secp256r1")) }
                    .getParameterSpec(ECParameterSpec::class.java)
                val factory = KeyFactory.getInstance("EC")
                // Public synthetic scalar1 from the independent fixture; never a production key.
                val privateKey = factory.generatePrivate(ECPrivateKeySpec(BigInteger.ONE, parameters))
                val peer = ECPoint(BigInteger(1, peerPublicKey.copyOfRange(1, 33)), BigInteger(1, peerPublicKey.copyOfRange(33, 65)))
                val publicKey = factory.generatePublic(ECPublicKeySpec(peer, parameters))
                return KeyAgreement.getInstance("ECDH").run {
                    init(privateKey)
                    doPhase(publicKey, true)
                    generateSecret().also { returnedSecrets += it }
                }
            }

            override fun close() = Unit
        }

        override suspend fun createP256Session(): SourceP256Session = session()
        override suspend fun sha256(input: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(input)
        override suspend fun sha384(input: ByteArray): ByteArray = MessageDigest.getInstance("SHA-384").digest(input)

        override suspend fun hmacSha384(key: ByteArray, input: ByteArray): ByteArray {
            cancellation?.let { throw it }
            macs++
            return Mac.getInstance("HmacSHA384").run {
                init(SecretKeySpec(key, "HmacSHA384"))
                // The first HMAC is a public salt. Track subsequent extract/expand buffers only.
                doFinal(input).also { if (macs > 1) returnedSecrets += it }
            }
        }

        override suspend fun decryptChaCha20Poly1305(
            key: ByteArray,
            nonce: ByteArray,
            ciphertextAndTag: ByteArray,
            associatedData: ByteArray,
        ): ByteArray {
            decryptions++
            aeadKey = key
            return Cipher.getInstance("ChaCha20-Poly1305").run {
                init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "ChaCha20"), IvParameterSpec(nonce))
                updateAAD(associatedData)
                doFinal(ciphertextAndTag).also { returnedSecrets += it }
            }
        }
    }
}
