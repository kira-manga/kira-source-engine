package me.manga.kira.source.engine.internal

/**
 * Frozen signed credential authority. No live mirror, migration history, response URL, endpoint
 * literal or suffix match may enlarge it. The entire source map is sensitive, regardless of names.
 */
internal class SourceHeaderPolicy(baseUrl: String, imageBase: String, trustedHosts: List<String>) {
    private val origins: Set<CredentialOrigin> = buildSet {
        val base = HttpDestination.parse(baseUrl)
        base?.credentialOrigin?.let(::add)
        HttpDestination.parse(imageBase)?.credentialOrigin
            ?.takeIf { it.scheme == "https" || base?.scheme == "http" }
            ?.let(::add)
        // Parse now into a private set, retaining no reference to the caller's mutable list.
        trustedHosts.toList().mapNotNull { HttpDestination.trustedHostOrigin(it) }.forEach(::add)
    }

    fun headersFor(destination: HttpDestination, snapshot: Map<String, String>): Map<String, String> =
        if (destination.credentialOrigin != null && destination.credentialOrigin in origins) snapshot else emptyMap()
}
