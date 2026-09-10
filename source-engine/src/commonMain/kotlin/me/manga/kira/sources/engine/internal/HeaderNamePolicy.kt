package me.manga.kira.source.engine.internal

/** Name-only policy for header filters; Backend static-header value policy remains separate. */
internal object HeaderNamePolicy {
    private val FORBIDDEN_NAMES = setOf("cookie", "set-cookie", "proxy-authorization")
    private val SENSITIVE_NAMES = setOf("authorization", "x-api-key", "api-key", "x-auth-token")
    private val SENSITIVE_SUBSTRINGS = listOf("token", "secret", "password")
    private const val HTTP_TOKEN_PUNCTUATION = "!#\$%&'*+-.^_`|~"

    /** RFC 9110 field-name = token: non-empty, ASCII-only, and never trimmed or repaired. */
    fun isHttpFieldName(name: String): Boolean =
        name.isNotEmpty() &&
            name.all { char ->
                char in 'a'..'z' ||
                    char in 'A'..'Z' ||
                    char in '0'..'9' ||
                    char in HTTP_TOKEN_PUNCTUATION
            }

    fun isForbidden(name: String): Boolean = name.lowercase() in FORBIDDEN_NAMES

    fun isSensitive(name: String): Boolean {
        val lower = name.lowercase()
        return lower in SENSITIVE_NAMES || SENSITIVE_SUBSTRINGS.any { it in lower }
    }
}
