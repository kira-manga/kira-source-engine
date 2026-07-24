package me.manga.kira.sources.engine.internal

import me.manga.kira.sources.contracts.model.TransformSpec
import kotlin.test.Test
import kotlin.test.assertEquals

class TransformsTest {
    private fun t(fn: String, args: Map<String, String> = emptyMap()) = TransformSpec(fn = fn, args = args)

    @Test
    fun strip_html_and_trim_chain() {
        assertEquals("Pirates", Transforms.apply("  <b>Pirates</b> ", listOf(t("strip-html"), t("trim"))))
    }

    @Test
    fun regex_replace() {
        val out = Transforms.apply("Chapter 12: Dawn", listOf(t("regex-replace", mapOf("pattern" to "[^0-9]", "replacement" to ""))))
        assertEquals("12", out)
    }

    @Test
    fun regex_extract_first_last_and_group() {
        // first numeric token (default which=first, group=0)
        assertEquals("610", Transforms.apply("610 - name [01]", listOf(t("regex-extract", mapOf("pattern" to "\\d+")))))
        // last numeric token (3asq chapter number = last \d+(\.\d+)? of the title)
        assertEquals("01", Transforms.apply("610 - name [01]", listOf(t("regex-extract", mapOf("pattern" to "\\d+(\\.\\d+)?", "which" to "last")))))
        // capture group
        assertEquals("5.5", Transforms.apply("Chapter 5.5", listOf(t("regex-extract", mapOf("pattern" to "Chapter (\\d+(?:\\.\\d+)?)", "group" to "1")))))
        // no match -> passthrough
        assertEquals("none", Transforms.apply("none", listOf(t("regex-extract", mapOf("pattern" to "\\d+")))))
    }

    @Test
    fun enum_map_with_default() {
        val spec = t("enum-map", mapOf("1" to "Ongoing", "2" to "Completed", "__default__" to "Unknown"))
        assertEquals("Ongoing", Transforms.apply("1", listOf(spec)))
        assertEquals("Unknown", Transforms.apply("7", listOf(spec)))
    }

    @Test
    fun substring_before_after_and_default() {
        assertEquals("one-piece", Transforms.apply("one-piece?x=1", listOf(t("substring-before", mapOf("delimiter" to "?")))))
        assertEquals("1", Transforms.apply("page=1", listOf(t("substring-after", mapOf("delimiter" to "=")))))
        assertEquals("fallback", Transforms.apply("", listOf(t("default", mapOf("value" to "fallback")))))
    }

    @Test
    fun prepend_append_replace_remove() {
        assertEquals("/api/x", Transforms.apply("x", listOf(t("prepend", mapOf("value" to "/api/")))))
        assertEquals("x.json", Transforms.apply("x", listOf(t("append", mapOf("value" to ".json")))))
        assertEquals("a-b", Transforms.apply("a_b", listOf(t("replace", mapOf("from" to "_", "to" to "-")))))
        assertEquals("ab", Transforms.apply("a b", listOf(t("remove", mapOf("value" to " ")))))
    }

    @Test
    fun unknown_transform_is_inert() {
        assertEquals("x", Transforms.apply("x", listOf(t("not-a-real-fn"))))
    }

    @Test
    fun clean_html_strips_tags_decodes_entities_and_collapses_whitespace() {
        // Mirrors legacy cleanHtmlContent (Azora descriptions).
        val raw = "<p>Pirates  &amp; adventure</p>\n  &quot;quoted&quot;&nbsp;&#39;x&#39;  <br>"
        assertEquals("Pirates & adventure \"quoted\" 'x'", Transforms.apply(raw, listOf(t("clean-html"))))
    }

    @Test
    fun format_number_drops_trailing_zero_for_whole_values() {
        assertEquals("12", Transforms.apply("12.0", listOf(t("format-number"))))
        assertEquals("12", Transforms.apply("12", listOf(t("format-number"))))
        assertEquals("12.5", Transforms.apply("12.5", listOf(t("format-number"))))
        assertEquals("abc", Transforms.apply("abc", listOf(t("format-number")))) // non-numeric passes through
    }

    @Test
    fun decimal_renders_canonical_double_string() {
        // matches a Kotlin Double.toString() boundary (e.g. Azora averageRating?.toString())
        assertEquals("8.0", Transforms.apply("8", listOf(t("decimal"))))
        assertEquals("8.5", Transforms.apply("8.5", listOf(t("decimal"))))
        assertEquals("", Transforms.apply("", listOf(t("decimal")))) // blank passes through (then a later default fires)
        assertEquals("N/A", Transforms.apply("N/A", listOf(t("decimal")))) // non-numeric passes through
    }
}
