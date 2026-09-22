package me.manga.kira.source.engine.internal

/** RFC 3986 components kept encoded: reference resolution must not decode authority or dot segments. */
internal data class HttpReference(
    val scheme: String?,
    val authority: String?,
    val path: String,
    val query: String?,
    val fragment: String?,
) {
    /** RFC 3986 section 5.2. The base is the current *requested* hop, never response finalUrl. */
    fun resolveAgainst(base: HttpReference): HttpReference {
        if (scheme != null) return copy(path = removeDotSegments(path))
        if (authority != null) return copy(scheme = base.scheme, path = removeDotSegments(path))
        return copy(
            scheme = base.scheme,
            authority = base.authority,
            path = when {
                path.isEmpty() -> base.path
                path.startsWith('/') -> removeDotSegments(path)
                else -> {
                    val prefix = if (base.authority != null && base.path.isEmpty()) {
                        "/"
                    } else {
                        base.path.substringBeforeLast('/', "") + "/"
                    }
                    removeDotSegments(prefix + path)
                }
            },
            query = if (path.isEmpty()) query ?: base.query else query,
        )
    }

    fun encodedUrl(): String = buildString {
        scheme?.let { append(it).append(':') }
        authority?.let { append("//").append(it) }
        append(path)
        query?.let { append('?').append(it) }
        fragment?.let { append('#').append(it) }
    }

    companion object {
        fun parse(value: String): HttpReference? {
            // Never trim or repair URL syntax into a credential-bearing destination. Native clients
            // differ on whitespace, backslashes and encoded authorities; reject ambiguity upstream.
            if (value.any { it.isWhitespace() || it.code <= 0x20 || it.code in 0x7f..0x9f || it in "\\\"<>^`{|}" }) return null
            var index = 0
            while (index < value.length) {
                if (value[index] == '%') {
                    if (index + 2 >= value.length || !value[index + 1].isAsciiHex() || !value[index + 2].isAsciiHex()) return null
                    index += 3
                } else {
                    index++
                }
            }

            val beforeFragment = value.substringBefore('#')
            val fragment = if ('#' in value) value.substringAfter('#') else null
            if (fragment?.contains('#') == true) return null
            val beforeQuery = beforeFragment.substringBefore('?')
            val query = if ('?' in beforeFragment) beforeFragment.substringAfter('?') else null
            val colon = beforeQuery.indexOf(':')
            val slash = beforeQuery.indexOf('/')
            val hasScheme = colon >= 0 && (slash < 0 || colon < slash)
            val scheme = if (hasScheme) beforeQuery.substring(0, colon) else null
            if (scheme != null && (scheme.isEmpty() || !scheme.first().isAsciiLetter() ||
                    scheme.any { !it.isAsciiLetter() && it !in '0'..'9' && it !in "+-." })) return null
            val rest = if (hasScheme) beforeQuery.substring(colon + 1) else beforeQuery
            if (!rest.startsWith("//")) return HttpReference(scheme, null, rest, query, fragment)
            val authorityEnd = rest.indexOf('/', startIndex = 2).let { if (it < 0) rest.length else it }
            return HttpReference(scheme, rest.substring(2, authorityEnd), rest.substring(authorityEnd), query, fragment)
        }
    }
}

internal fun String.asciiLowercase(): String = buildString(length) {
    for (char in this@asciiLowercase) append(if (char in 'A'..'Z') char + ('a' - 'A') else char)
}

internal fun Char.isAsciiLetter(): Boolean = this in 'a'..'z' || this in 'A'..'Z'

internal fun Char.isAsciiHex(): Boolean = this in '0'..'9' || this in 'a'..'f' || this in 'A'..'F'

/** Literal dot segments only, preserving repeated slashes and encoded path/query/fragment data. */
private fun removeDotSegments(path: String): String {
    var remaining = path
    val output = StringBuilder()
    fun removeLastSegment() {
        output.deleteRange(output.lastIndexOf('/').coerceAtLeast(0), output.length)
    }
    while (remaining.isNotEmpty()) {
        remaining = when {
            remaining.startsWith("../") -> remaining.substring(3)
            remaining.startsWith("./") -> remaining.substring(2)
            remaining.startsWith("/./") -> remaining.substring(2)
            remaining == "/." -> "/"
            remaining.startsWith("/../") -> {
                removeLastSegment()
                remaining.substring(3)
            }
            remaining == "/.." -> {
                removeLastSegment()
                "/"
            }
            remaining == "." || remaining == ".." -> ""
            else -> {
                val end = remaining.indexOf('/', startIndex = if (remaining.startsWith('/')) 1 else 0)
                    .let { if (it < 0) remaining.length else it }
                output.append(remaining.substring(0, end))
                remaining.substring(end)
            }
        }
    }
    return output.toString()
}
