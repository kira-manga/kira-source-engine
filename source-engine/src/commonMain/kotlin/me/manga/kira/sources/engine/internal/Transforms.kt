package me.manga.kira.sources.engine.internal

import me.manga.kira.sources.contracts.model.TransformSpec

/**
 * The library of generic, named string transforms a config may chain onto an extracted field. Every
 * transform is pure (String → String) and compiled into the binary — a config can only *reference*
 * these by name, never define new ones. That is the safety floor of the data-only-config model.
 *
 * Adding a transform here (and registering its name in [DefaultStrategyRegistry]) is the supported way
 * to extend the engine; configs then opt in by name.
 */
internal object Transforms {

    /** Names recognised by [apply]; mirrored by the strategy registry for validation. */
    val NAMES: Set<String> = setOf(
        "trim",
        "lowercase",
        "uppercase",
        "strip-html",
        "clean-html",
        "regex-replace",
        "regex-extract",
        "replace",
        "remove",
        "prepend",
        "append",
        "substring-before",
        "substring-after",
        "default",
        "enum-map",
        "format-number",
        "decimal",
    )

    fun apply(value: String, specs: List<TransformSpec>): String {
        var acc = value
        for (spec in specs) acc = applyOne(acc, spec)
        return acc
    }

    private fun applyOne(value: String, spec: TransformSpec): String = when (spec.fn) {
        "trim" -> value.trim()
        "lowercase" -> value.lowercase()
        "uppercase" -> value.uppercase()
        "strip-html" -> STRIP_HTML.replace(value, "").trim()
        // Mirror of legacy cleanHtmlContent: strip tags, decode the common named/numeric entities,
        // collapse whitespace runs, trim. Kept faithful so config-driven descriptions match legacy.
        "clean-html" -> STRIP_HTML.replace(value, "")
            .replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", "\"")
            .replace("&#39;", "'")
            .replace(WHITESPACE, " ")
            .trim()
        // Mirror of legacy formatChapterNumber: a whole-valued number drops its fractional ".0".
        "format-number" -> value.toDoubleOrNull()?.let { d ->
            if (d == d.toLong().toDouble()) d.toLong().toString() else d.toString()
        } ?: value
        // Render as a Double's canonical string (e.g. "8" -> "8.0"), matching a Kotlin `Double.toString()`
        // boundary like Azora's `averageRating?.toString()`. Non-numeric / blank values pass through.
        "decimal" -> value.toDoubleOrNull()?.toString() ?: value
        "regex-replace" -> {
            val pattern = spec.args["pattern"].orEmpty()
            val replacement = spec.args["replacement"].orEmpty()
            if (pattern.isEmpty()) value else runCatching { Regex(pattern).replace(value, replacement) }.getOrDefault(value)
        }
        // Pull a substring out by regex: `pattern` (required), `group` (capture index, default 0 = whole
        // match), `which` ("first" default | "last"). No match → value passes through unchanged. E.g.
        // extract the LAST numeric token of a chapter title (3asq) with pattern "\d+(\.\d+)?", which=last.
        "regex-extract" -> {
            val pattern = spec.args["pattern"].orEmpty()
            if (pattern.isEmpty()) {
                value
            } else {
                runCatching {
                    val matches = Regex(pattern).findAll(value).toList()
                    val match = if (spec.args["which"] == "last") matches.lastOrNull() else matches.firstOrNull()
                    val group = spec.args["group"]?.toIntOrNull() ?: 0
                    match?.groupValues?.getOrNull(group)?.takeIf { it.isNotEmpty() } ?: value
                }.getOrDefault(value)
            }
        }
        "replace" -> value.replace(spec.args["from"].orEmpty(), spec.args["to"].orEmpty())
        "remove" -> value.replace(spec.args["value"].orEmpty(), "")
        "prepend" -> spec.args["value"].orEmpty() + value
        "append" -> value + spec.args["value"].orEmpty()
        "substring-before" -> spec.args["delimiter"]?.let { value.substringBefore(it) } ?: value
        "substring-after" -> spec.args["delimiter"]?.let { value.substringAfter(it) } ?: value
        "default" -> value.ifBlank { spec.args["value"].orEmpty() }
        "enum-map" -> spec.args[value] ?: spec.args["__default__"] ?: value
        else -> value // unknown transforms are rejected by the validator; be inert if one slips through
    }

    private val STRIP_HTML = Regex("<[^>]*>")
    private val WHITESPACE = Regex("\\s+")
}
