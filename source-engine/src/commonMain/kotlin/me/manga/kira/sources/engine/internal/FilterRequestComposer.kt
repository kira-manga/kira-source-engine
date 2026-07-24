package me.manga.kira.sources.engine.internal

import me.manga.kira.sources.contracts.SourceEngineError
import me.manga.kira.sources.contracts.SourceEngineResult
import me.manga.kira.sources.contracts.SourceFilterSelections
import me.manga.kira.sources.contracts.model.FilterDefinition

/**
 * The request contribution of one composed filter set — what [FilterRequestComposer] hands the
 * engine to merge into an outgoing request. All fields are additive over the endpoint's static
 * template parts.
 */
internal data class ComposedFilters(
    /** Raw (unencoded) query parameters, in filter-declaration order. Percent-encoded at append time. */
    val queryPairs: List<Pair<String, String>> = emptyList(),
    /** Form entries appended AFTER the endpoint's static formBody. Sent raw (Ktor form-encodes). */
    val formEntries: List<Pair<String, String>> = emptyList(),
    /** Headers merged OVER the computed request headers (filter wins on same name). */
    val headerEntries: Map<String, String> = emptyMap(),
    /** Extra template vars for `path` (percent-encoded) and `body-json` (JSON-escaped/array) targets. */
    val templateVars: Map<String, String> = emptyMap(),
) {
    companion object {
        val EMPTY = ComposedFilters()
    }
}

/**
 * The single, deterministic mapping from declared filters + user selections to request parts
 * (config-driven filters, 2026-07 — docs/sources/CONFIG_DRIVEN_FILTERS_PLAN.md §3). Pure function
 * of its inputs; NO api branching, NO expression evaluation — behavior comes only from the
 * validated [FilterDefinition] vocabulary.
 *
 * Guarantees (each pinned by FilterRequestComposerTest):
 * - unknown selection ids and unknown option values are dropped (stale UI state can never inject
 *   arbitrary parameters);
 * - select conflicting values resolve to the FIRST by option-declaration order;
 * - defaults apply when no selection exists; empty + omitIfEmpty ⇒ the parameter is absent;
 * - a hidden filter (unsatisfied visibleWhen) contributes nothing;
 * - exclude counterparts drop values also present on their include side (include wins);
 * - a required filter that still resolves empty fails closed (`Validation.Required("filter:<id>")`).
 */
internal object FilterRequestComposer {
    fun compose(
        filters: List<FilterDefinition>,
        verb: String,
        selections: SourceFilterSelections,
    ): SourceEngineResult<ComposedFilters> {
        val applicable = filters.filter { verb in it.appliesTo }
        if (applicable.isEmpty()) return SourceEngineResult.Success(ComposedFilters.EMPTY)

        // Logical values (option values / "true"|"false" / free text) for EVERY declared filter —
        // visibility conditions reference logical values, never wire encodings.
        val logical = LinkedHashMap<String, List<String>>()
        for (filter in filters) logical[filter.id] = logicalValueOf(filter, selections)
        // Include/exclude pairs: a value on both sides is dropped from the exclude side.
        for (filter in filters) {
            val includeId = filter.excludeOf.takeIf { it.isNotBlank() } ?: continue
            val included = logical[includeId].orEmpty().toSet()
            logical[filter.id] = logical[filter.id].orEmpty().filterNot { it in included }
        }

        fun isVisible(filter: FilterDefinition): Boolean =
            filter.visibleWhen.all { condition ->
                logical[condition.filter].orEmpty().any { it in condition.anyOf }
            }

        val queryPairs = mutableListOf<Pair<String, String>>()
        val formEntries = mutableListOf<Pair<String, String>>()
        val headerEntries = LinkedHashMap<String, String>()
        val templateVars = LinkedHashMap<String, String>()

        for (filter in applicable) {
            if (!isVisible(filter)) continue
            val values = wireValueOf(filter, logical[filter.id].orEmpty())
            val request = filter.request

            if (values.isEmpty()) {
                if (filter.required) {
                    return SourceEngineResult.Failure(SourceEngineError.Required("filter:${filter.id}"))
                }
                when (request.target) {
                    // Placeholder holes can never be omitted — deterministic empty expansions.
                    // (`path` is validator-guaranteed a non-empty default; this is the defensive floor.)
                    "path" -> templateVars[request.param] = ""
                    "body-json" -> templateVars[request.param] = if (request.encode == "json-array") "[]" else ""
                    else -> if (!request.omitIfEmpty) addPairs(filter, listOf(""), queryPairs, formEntries, headerEntries)
                }
                continue
            }

            when (request.target) {
                "query", "form", "header" -> addPairs(filter, values, queryPairs, formEntries, headerEntries)
                "path" ->
                    templateVars[request.param] =
                        values.joinToString(request.delimiter) { UrlEncode.encode(it) }
                "body-json" ->
                    templateVars[request.param] =
                        if (request.encode == "json-array") {
                            values.joinToString(",", prefix = "[", postfix = "]") { "\"${JsonEscape.escape(it)}\"" }
                        } else {
                            JsonEscape.escape(values.first())
                        }
            }
        }

        return SourceEngineResult.Success(
            ComposedFilters(
                queryPairs = queryPairs,
                formEntries = formEntries,
                headerEntries = headerEntries,
                templateVars = templateVars,
            ),
        )
    }

    /**
     * Append raw query pairs to an already-expanded URL, percent-encoding names AND values with the
     * engine's [UrlEncode] (RFC 3986; `genre[]` → `genre%5B%5D`, space → `%20` — byte-parity with
     * the legacy builders).
     */
    fun appendQueryPairs(
        url: String,
        pairs: List<Pair<String, String>>,
    ): String {
        if (pairs.isEmpty()) return url
        val queryString = pairs.joinToString("&") { (name, value) -> "${UrlEncode.encode(name)}=${UrlEncode.encode(value)}" }
        return url + (if (url.contains('?')) "&" else "?") + queryString
    }

    private fun addPairs(
        filter: FilterDefinition,
        values: List<String>,
        queryPairs: MutableList<Pair<String, String>>,
        formEntries: MutableList<Pair<String, String>>,
        headerEntries: MutableMap<String, String>,
    ) {
        val request = filter.request
        val pairs =
            when (request.encode) {
                "repeat" -> values.map { request.param to it }
                "csv" -> listOf(request.param to values.joinToString(request.delimiter))
                else -> listOf(request.param to values.first())
            }
        when (request.target) {
            "query" -> queryPairs += pairs
            "form" -> formEntries += pairs
            "header" -> headerEntries[request.param] = pairs.joinToString(request.delimiter) { it.second }
        }
    }

    /**
     * The filter's LOGICAL value: selection if present, else default. Select/multiselect values are
     * pruned to declared options and re-ordered to option-declaration order (deterministic across
     * callers); a select keeps only the first survivor. A toggle resolves to `"true"`/`"false"`
     * (unset + no default = off). Number drops non-numeric input; text passes through non-empty.
     */
    private fun logicalValueOf(
        filter: FilterDefinition,
        selections: SourceFilterSelections,
    ): List<String> {
        val raw =
            selections.byId[filter.id]
                ?: if (filter.type == "multiselect") filter.defaults else listOf(filter.default).filter { it.isNotBlank() }
        return when (filter.type) {
            "select" -> orderedKnownValues(filter, raw).take(1)
            "multiselect" -> orderedKnownValues(filter, raw)
            "toggle" -> listOf(if (raw.firstOrNull() == "true") "true" else "false")
            "number" -> raw.take(1).filter { it.isNotBlank() && it.toDoubleOrNull() != null }
            else -> raw.take(1).filter { it.isNotBlank() } // text
        }
    }

    /** The WIRE value: identical to logical except toggles map to their declared true/false values. */
    private fun wireValueOf(
        filter: FilterDefinition,
        logical: List<String>,
    ): List<String> {
        if (filter.type != "toggle") return logical
        val wire = if (logical.firstOrNull() == "true") filter.request.trueValue else filter.request.falseValue
        return if (wire.isEmpty()) emptyList() else listOf(wire)
    }

    private fun orderedKnownValues(
        filter: FilterDefinition,
        raw: List<String>,
    ): List<String> {
        if (raw.isEmpty()) return emptyList()
        val indexByValue = filter.options.withIndex().associate { (index, option) -> option.value to index }
        return raw.filter { it in indexByValue }.distinct().sortedBy { indexByValue.getValue(it) }
    }
}
