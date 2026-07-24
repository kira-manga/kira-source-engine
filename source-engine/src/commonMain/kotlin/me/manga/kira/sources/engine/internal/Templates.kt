package me.manga.kira.sources.engine.internal

/**
 * `{placeholder}` substitution for URL templates and image/url field templates. Unknown placeholders
 * are left intact (so a malformed template is visible rather than silently producing a wrong URL).
 * The canonical variables are seeded by the engine: `baseUrl`, `imageBase`, `page`, `query`,
 * `itemUrl`, `chapterUrl`, `id` — plus any per-field `vars` resolved from the response.
 */
internal object Templates {
    private val PLACEHOLDER = Regex("""\{([a-zA-Z0-9_]+)\}""")

    fun expand(template: String, vars: Map<String, String>): String =
        PLACEHOLDER.replace(template) { match ->
            vars[match.groupValues[1]] ?: match.value
        }
}
