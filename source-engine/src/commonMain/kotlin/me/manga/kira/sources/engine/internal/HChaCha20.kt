package me.manga.kira.source.engine.internal

/**
 * HChaCha20 subkey derivation from draft-irtf-cfrg-xchacha-03 §2.2. This is not an AEAD
 * implementation: authentication and IETF ChaCha20-Poly1305 stay with the host's standard provider.
 * Unlike the ChaCha block function, HChaCha neither adds the initial state nor emits all 16 words.
 */
internal object HChaCha20 {
    fun derive(key: ByteArray, nonce: ByteArray): ByteArray {
        require(key.size == 32 && nonce.size == 16)
        val state = IntArray(16)
        try {
            state[0] = 0x61707865
            state[1] = 0x3320646e
            state[2] = 0x79622d32
            state[3] = 0x6b206574
            repeat(8) { state[4 + it] = word(key, it * 4) }
            repeat(4) { state[12 + it] = word(nonce, it * 4) }
            repeat(10) {
                quarter(state, 0, 4, 8, 12)
                quarter(state, 1, 5, 9, 13)
                quarter(state, 2, 6, 10, 14)
                quarter(state, 3, 7, 11, 15)
                quarter(state, 0, 5, 10, 15)
                quarter(state, 1, 6, 11, 12)
                quarter(state, 2, 7, 8, 13)
                quarter(state, 3, 4, 9, 14)
            }
            return ByteArray(32).also { result ->
                for (i in 0 until 8) {
                    val value = state[if (i < 4) i else i + 8]
                    repeat(4) { result[i * 4 + it] = (value ushr (it * 8)).toByte() }
                }
            }
        } finally {
            state.fill(0)
        }
    }

    private fun word(bytes: ByteArray, offset: Int): Int =
        (bytes[offset].toInt() and 255) or
            ((bytes[offset + 1].toInt() and 255) shl 8) or
            ((bytes[offset + 2].toInt() and 255) shl 16) or
            ((bytes[offset + 3].toInt() and 255) shl 24)

    private fun rotate(value: Int, bits: Int): Int = (value shl bits) or (value ushr (32 - bits))

    private fun quarter(s: IntArray, a: Int, b: Int, c: Int, d: Int) {
        s[a] += s[b]
        s[d] = rotate(s[d] xor s[a], 16)
        s[c] += s[d]
        s[b] = rotate(s[b] xor s[c], 12)
        s[a] += s[b]
        s[d] = rotate(s[d] xor s[a], 8)
        s[c] += s[d]
        s[b] = rotate(s[b] xor s[c], 7)
    }
}
