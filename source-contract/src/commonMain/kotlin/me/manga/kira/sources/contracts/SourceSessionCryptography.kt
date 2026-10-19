package me.manga.kira.source.contracts

/**
 * Host-supplied standard primitives for compiled, authenticated source protocols. Implementations
 * must use maintained platform providers, never perform network I/O, log inputs, or return plaintext
 * after an authentication failure. This port adds no provider dependency to the shared package.
 */
interface SourceSessionCryptography {
    suspend fun createP256Session(): SourceP256Session

    suspend fun sha256(input: ByteArray): ByteArray

    suspend fun sha384(input: ByteArray): ByteArray

    suspend fun hmacSha384(key: ByteArray, input: ByteArray): ByteArray

    /** IETF ChaCha20-Poly1305: 32-byte key, 12-byte nonce, ciphertext followed by a 16-byte tag. */
    suspend fun decryptChaCha20Poly1305(
        key: ByteArray,
        nonce: ByteArray,
        ciphertextAndTag: ByteArray,
        associatedData: ByteArray,
    ): ByteArray
}

/** A fresh, request-owned key pair. Never share it between logical source operations. */
interface SourceP256Session {
    /** A copy of the 65-byte SEC1 uncompressed P-256 public point. */
    val publicKey: ByteArray

    /** Validate/import the peer point on P-256 and return the 32-byte ECDH secret. */
    suspend fun sharedSecret(peerPublicKey: ByteArray): ByteArray

    /** Release private-key references/resources, including on cancellation; must not throw. */
    fun close()
}
