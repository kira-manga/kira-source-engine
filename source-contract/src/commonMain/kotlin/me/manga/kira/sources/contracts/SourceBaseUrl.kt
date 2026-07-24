package me.manga.kira.sources.contracts

/**
 * Returns the normalized host of an absolute HTTP(S) source URL, or `null` when [value] is not a
 * usable network origin.
 *
 * Source base URLs also live in a user-editable Room projection. Keep this check independent of
 * the signed-config validator so a corrupt or stale persisted value such as `about:about` cannot
 * override a valid immutable descriptor or be mistaken for a user-configured mirror.
 */
fun sourceBaseUrlHost(value: String): String? {
    val url = value.trim()
    val schemeEnd = url.indexOf("://")
    val hasScheme = schemeEnd > 0
    val scheme = if (hasScheme) url.substring(0, schemeEnd).lowercase() else ""
    val authority =
        if (hasScheme) {
            url
                .substring(schemeEnd + SCHEME_SEPARATOR_LENGTH)
                .substringBefore('/')
                .substringBefore('?')
                .substringBefore('#')
        } else {
            ""
        }
    val host =
        when {
            !hasScheme -> null
            scheme != "http" && scheme != "https" -> null
            authority.isBlank() || '@' in authority || authority.any(Char::isWhitespace) -> null
            authority.startsWith('[') -> bracketedHost(authority)
            else -> domainHost(authority)
        }
    return host?.lowercase()?.takeIf { it.isNotBlank() }
}

fun isValidSourceBaseUrl(value: String): Boolean = sourceBaseUrlHost(value) != null

private fun bracketedHost(authority: String): String? {
    val closingBracket = authority.indexOf(']')
    val suffix = authority.substring((closingBracket + 1).coerceAtMost(authority.length))
    val hasValidSuffix = suffix.isEmpty() || isValidPortSuffix(suffix)
    return authority
        .substring(1, closingBracket.coerceAtLeast(1))
        .takeIf { closingBracket > 1 && hasValidSuffix }
}

private fun domainHost(authority: String): String? {
    val separator = authority.lastIndexOf(':')
    val host = authority.substringBefore(':')
    val hasSingleSeparator = separator == authority.indexOf(':')
    val hasValidPort = separator < 0 || isValidPortSuffix(authority.substring(separator))
    return host.takeIf { hasSingleSeparator && hasValidPort }
}

private fun isValidPortSuffix(suffix: String): Boolean =
    suffix
        .removePrefix(":")
        .toIntOrNull()
        ?.let { it in MIN_PORT..MAX_PORT } == true

private const val SCHEME_SEPARATOR_LENGTH = 3
private const val MIN_PORT = 1
private const val MAX_PORT = 65_535
