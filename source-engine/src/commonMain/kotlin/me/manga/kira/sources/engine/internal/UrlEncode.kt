package me.manga.kira.sources.engine.internal

/**
 * Minimal, dependency-free percent-encoder for query values (the engine has no Ktor). Encodes
 * everything outside the RFC 3986 unreserved set so a search term is safe to interpolate into a URL
 * template via the `{queryEncoded}` variable. Space → `%20` (not `+`); callers that need form-style
 * `+` can post a form body instead.
 */
internal object UrlEncode {
    private const val UNRESERVED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"

    fun encode(value: String): String {
        val sb = StringBuilder(value.length)
        for (byte in value.encodeToByteArray()) {
            val c = byte.toInt() and 0xFF
            if (c.toChar() in UNRESERVED) {
                sb.append(c.toChar())
            } else {
                sb.append('%')
                sb.append(HEX[c shr 4])
                sb.append(HEX[c and 0x0F])
            }
        }
        return sb.toString()
    }

    private val HEX = "0123456789ABCDEF".toCharArray()
}
