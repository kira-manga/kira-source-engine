package me.manga.kira.source.engine.internal

internal data class CredentialOrigin(val scheme: String, val host: String, val port: Int)

/**
 * A sendable HTTP(S) destination, not a native client's URL canonicalizer. A null [credentialOrigin]
 * means an otherwise usable spelling (such as Unicode or a trailing dot) must stay anonymous.
 * Malformed/ambiguous syntax instead fails [parse], before a request can reach a transport.
 */
internal class HttpDestination private constructor(
    val url: String,
    val scheme: String,
    val credentialOrigin: CredentialOrigin?,
    private val reference: HttpReference,
) {
    fun resolve(location: String): HttpDestination? =
        HttpReference.parse(location)?.resolveAgainst(reference)?.encodedUrl()?.let { parse(it) }

    companion object {
        fun parse(url: String): HttpDestination? {
            val reference = HttpReference.parse(url) ?: return null
            val scheme = reference.scheme?.asciiLowercase()?.takeIf { it == "http" || it == "https" } ?: return null
            val authority = reference.authority?.takeIf { it.isNotEmpty() } ?: return null
            if ('@' in authority || '%' in authority) return null

            val host: String
            val portSuffix: String
            val credentialHost: String?
            if (authority.startsWith('[')) {
                val end = authority.indexOf(']')
                if (end < 0 || !isIpv6Literal(authority.substring(1, end))) return null
                host = authority.substring(0, end + 1)
                portSuffix = authority.substring(end + 1)
                credentialHost = host.asciiLowercase()
            } else {
                if ('[' in authority || ']' in authority || authority.count { it == ':' } > 1) return null
                host = authority.substringBefore(':')
                portSuffix = authority.substring(host.length)
                if (!isUsableRegName(host)) return null
                // Numeric aliases are interpreted as IPv4 by some native stacks. Never let an
                // integer/octal/hex/short address fall through to an apparently ordinary DNS name.
                val lastLabel = host.removeSuffix(".").substringAfterLast('.').asciiLowercase()
                val numericLooking = lastLabel.all { it in '0'..'9' } ||
                    (lastLabel.startsWith("0x") && lastLabel.drop(2).all { it.isAsciiHex() })
                if (numericLooking && !isCanonicalIpv4(host)) return null
                credentialHost = host.takeIf { isCredentialDnsName(it) || isCanonicalIpv4(it) }?.asciiLowercase()
            }
            val port = if (portSuffix.isEmpty()) {
                if (scheme == "https") 443 else 80
            } else {
                if (!portSuffix.startsWith(':')) return null
                val digits = portSuffix.substring(1)
                if (digits.isEmpty() || digits.any { it !in '0'..'9' }) return null
                digits.toIntOrNull()?.takeIf { it in 1..65_535 } ?: return null
            }
            return HttpDestination(url, scheme, credentialHost?.let { CredentialOrigin(scheme, it, port) }, reference)
        }

        /** Page extraction may keep its existing relative-base rules, but not repair an unsafe URI. */
        fun isSafeImageReference(value: String): Boolean {
            val reference = HttpReference.parse(value) ?: return false
            return when {
                reference.scheme != null -> parse(value) != null
                reference.authority != null -> parse("https:$value") != null
                else -> true
            }
        }

        /** Extra trust entries are *bare* hosts, not endpoints or arbitrary-port authorities. */
        fun trustedHostOrigin(host: String): CredentialOrigin? {
            if (host.any { it in ":/?#" }) return null
            return parse("https://$host")?.credentialOrigin
        }
    }
}

private fun isUsableRegName(host: String): Boolean {
    if (host.isEmpty()) return false
    if (host.any { it.code < 0xa0 && !it.isAsciiLetter() && it !in '0'..'9' && it !in "-._~!$&'()*+,;=" }) return false
    val labels = host.removeSuffix(".").split('.')
    if (labels.any { it.isEmpty() }) return false
    if (host.all { it.code < 0x80 } && (host.removeSuffix(".").length > 253 || labels.any { it.length > 63 })) return false
    return true
}

private fun isCredentialDnsName(host: String): Boolean =
    host.length in 1..253 && !host.endsWith('.') && host.split('.').all { label ->
        label.length in 1..63 && label.first().isAsciiLetterOrDigit() && label.last().isAsciiLetterOrDigit() &&
            label.all { it.isAsciiLetterOrDigit() || it == '-' }
    }

private fun Char.isAsciiLetterOrDigit(): Boolean = isAsciiLetter() || this in '0'..'9'

private fun isCanonicalIpv4(host: String): Boolean {
    if (host.startsWith('.') || host.endsWith('.') || host.count { it == '.' } != 3) return false
    val octets = host.split('.')
    return octets.size == 4 && octets.all { octet ->
        octet.length in 1..3 && octet.all { it in '0'..'9' } &&
            (octet.length == 1 || octet.first() != '0') && (octet.toIntOrNull() ?: 256) <= 255
    }
}

private fun isIpv6Literal(literal: String): Boolean {
    if (':' !in literal) return false
    val compression = literal.indexOf("::")
    val groups = if (compression >= 0) {
        if (literal.indexOf("::", startIndex = compression + 2) >= 0) return false
        val before = literal.substring(0, compression)
        val after = literal.substring(compression + 2)
        (if (before.isEmpty()) emptyList() else before.split(':')) +
            (if (after.isEmpty()) emptyList() else after.split(':'))
    } else {
        literal.split(':')
    }
    var width = 0
    for ((index, group) in groups.withIndex()) {
        if ('.' in group) {
            if (index != groups.lastIndex || !literal.endsWith(group) || !isCanonicalIpv4(group)) return false
            width += 2
        } else {
            if (group.length !in 1..4 || group.any { !it.isAsciiHex() }) return false
            width++
        }
    }
    return if (compression >= 0) width < 8 else width == 8
}
