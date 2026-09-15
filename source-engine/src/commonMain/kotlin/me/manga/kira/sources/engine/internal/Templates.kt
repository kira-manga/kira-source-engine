package me.manga.kira.source.engine.internal

/**
 * `{placeholder}` substitution for URL templates and image/url field templates. Unknown placeholders
 * are left intact (so a malformed template is visible rather than silently producing a wrong URL).
 * Request and field namespaces differ: request names are [REQUEST_VARIABLES]; fields seed only
 * [FIELD_VARIABLES] plus their own declared vars. Neither namespace adds expression evaluation.
 */
internal object Templates {
    private val PLACEHOLDER = Regex("""\{([a-zA-Z0-9_]+)\}""")

    val REQUEST_VARIABLES: Set<String> = setOf(
        "baseUrl", "imageBase", "page", "pageOffset", "query", "queryEncoded", "queryJson",
        "itemUrl", "chapterUrl", "id",
    )
    val FIELD_VARIABLES: Set<String> = setOf("baseUrl", "imageBase")

    /** Inspect exactly the tokens [expand] consumes, not JSON object braces or literal form keys. */
    fun references(template: String): Set<String> =
        PLACEHOLDER.findAll(template).map { it.groupValues[1] }.toSet()

    fun isVariableName(name: String): Boolean = PLACEHOLDER.matches("{$name}")

    fun expand(template: String, vars: Map<String, String>): String =
        PLACEHOLDER.replace(template) { match ->
            vars[match.groupValues[1]] ?: match.value
        }
}
