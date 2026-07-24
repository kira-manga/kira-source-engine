package me.manga.kira.sources.engine.internal

/**
 * Minimal, dependency-free JSON-string escaper (the engine has no kotlinx.serialization on this path).
 * Escapes the characters that are illegal inside a JSON string per RFC 8259 — `"`, `\`, and the C0
 * control range (`U+0000`..`U+001F`, using the short escapes for the common whitespace controls and
 * `\uXXXX` for the rest, including form feed) — so a raw search term is safe to interpolate into a
 * `jsonBody` template via the `{queryJson}` variable. The result does NOT include the surrounding
 * quotes; the template supplies those.
 */
internal object JsonEscape {
    fun escape(value: String): String {
        val sb = StringBuilder(value.length + 8)
        for (c in value) {
            when (c) {
                '"' -> sb.append("\\\"")
                '\\' -> sb.append("\\\\")
                '\b' -> sb.append("\\b")
                '\t' -> sb.append("\\t")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                else -> if (c < ' ') {
                    sb.append("\\u")
                    val code = c.code
                    sb.append(HEX[(code shr 12) and 0xF])
                    sb.append(HEX[(code shr 8) and 0xF])
                    sb.append(HEX[(code shr 4) and 0xF])
                    sb.append(HEX[code and 0xF])
                } else {
                    sb.append(c)
                }
            }
        }
        return sb.toString()
    }

    private val HEX = "0123456789abcdef".toCharArray()
}
