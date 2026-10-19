package me.manga.kira.source.engine

import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertFailsWith
import me.manga.kira.source.engine.internal.HChaCha20

class HChaCha20Test {
    @Test
    fun published_subkey_vector_preserves_inputs() {
        // draft-irtf-cfrg-xchacha-03 §2.2.1; synthetic public test key/nonce, not credentials.
        val key = ByteArray(32) { it.toByte() }
        val nonce = hex("000000090000004a0000000031415927")
        val originalKey = key.copyOf()
        val originalNonce = nonce.copyOf()
        assertContentEquals(
            hex("82413b4227b27bfed30e42508a877d73a0f9e4d58a74a853c12ec41326d3ecdc"),
            HChaCha20.derive(key, nonce),
        )
        assertContentEquals(originalKey, key)
        assertContentEquals(originalNonce, nonce)
    }

    @Test
    fun wrong_key_or_nonce_length_is_refused() {
        assertFailsWith<IllegalArgumentException> { HChaCha20.derive(ByteArray(31), ByteArray(16)) }
        assertFailsWith<IllegalArgumentException> { HChaCha20.derive(ByteArray(32), ByteArray(24)) }
    }

    private fun hex(value: String): ByteArray = value.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
}
