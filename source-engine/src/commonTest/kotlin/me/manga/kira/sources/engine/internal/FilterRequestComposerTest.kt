package me.manga.kira.source.engine.internal

import me.manga.kira.source.contracts.SourceEngineError
import me.manga.kira.source.contracts.SourceEngineResult
import me.manga.kira.source.contracts.SourceFilterSelections
import me.manga.kira.source.contracts.model.FilterConditionSpec
import me.manga.kira.source.contracts.model.FilterDefinition
import me.manga.kira.source.contracts.model.FilterOptionSpec
import me.manga.kira.source.contracts.model.FilterRequestSpec
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * The deterministic composition rules (CONFIG_DRIVEN_FILTERS_PLAN.md §3), pinned one by one:
 * defaults, omission, encodings, conflicting values, unknown ids/values, visibility, include/
 * exclude pairs, toggles, and the required fail-closed path.
 */
class FilterRequestComposerTest {
    private fun genres(
        encode: String = "csv",
        defaults: List<String> = emptyList(),
        excludeOf: String = "",
        id: String = "genres",
        param: String = "genre",
    ) = FilterDefinition(
        id = id,
        label = id,
        type = "multiselect",
        options = listOf(FilterOptionSpec("action"), FilterOptionSpec("drama"), FilterOptionSpec("isekai")),
        defaults = defaults,
        request = FilterRequestSpec(target = "query", param = param, encode = encode),
        excludeOf = excludeOf,
    )

    private fun sort(default: String = "") =
        FilterDefinition(
            id = "sort",
            label = "Sort",
            type = "select",
            options = listOf(FilterOptionSpec("latest"), FilterOptionSpec("views")),
            default = default,
            request = FilterRequestSpec(target = "query", param = "orderby"),
        )

    private fun selections(vararg pairs: Pair<String, List<String>>) = SourceFilterSelections(mapOf(*pairs))

    private fun composed(
        filters: List<FilterDefinition>,
        selections: SourceFilterSelections = SourceFilterSelections(),
    ): ComposedFilters =
        when (val r = FilterRequestComposer.compose(filters, "search", selections)) {
            is SourceEngineResult.Success -> r.value
            is SourceEngineResult.Failure -> throw AssertionError("expected composition success, got ${r.error}")
        }

    @Test
    fun defaults_apply_when_no_selection_exists_and_selection_overrides_them() {
        val filters = listOf(sort(default = "latest"))
        assertEquals(listOf("orderby" to "latest"), composed(filters).queryPairs)
        assertEquals(
            listOf("orderby" to "views"),
            composed(filters, selections("sort" to listOf("views"))).queryPairs,
        )
    }

    @Test
    fun empty_optional_values_are_omitted_and_omitIfEmpty_false_sends_an_empty_param() {
        assertEquals(emptyList(), composed(listOf(sort())).queryPairs)
        val explicit =
            sort().copy(request = FilterRequestSpec(target = "query", param = "orderby", omitIfEmpty = false))
        assertEquals(listOf("orderby" to ""), composed(listOf(explicit)).queryPairs)
    }

    @Test
    fun csv_repeat_and_single_encodings_are_deterministic() {
        val picked = selections("genres" to listOf("action", "drama"))
        assertEquals(listOf("genre" to "action,drama"), composed(listOf(genres("csv")), picked).queryPairs)
        assertEquals(
            listOf("genre" to "action", "genre" to "drama"),
            composed(listOf(genres("repeat")), picked).queryPairs,
        )
        assertEquals(listOf("genre" to "action"), composed(listOf(genres("single")), picked).queryPairs)
        // custom delimiter
        val piped =
            genres().copy(request = FilterRequestSpec(target = "query", param = "genre", encode = "csv", delimiter = "|"))
        assertEquals(listOf("genre" to "action|drama"), composed(listOf(piped), picked).queryPairs)
    }

    @Test
    fun multiselect_values_are_reordered_to_option_declaration_order_and_deduplicated() {
        val picked = selections("genres" to listOf("isekai", "action", "isekai"))
        assertEquals(listOf("genre" to "action,isekai"), composed(listOf(genres("csv")), picked).queryPairs)
    }

    @Test
    fun conflicting_select_values_resolve_to_the_first_by_option_order() {
        val picked = selections("sort" to listOf("views", "latest"))
        assertEquals(listOf("orderby" to "latest"), composed(listOf(sort()), picked).queryPairs)
    }

    @Test
    fun unknown_selection_ids_and_unknown_option_values_are_dropped() {
        val picked =
            selections(
                "ghost" to listOf("boo"),
                "genres" to listOf("action", "not-a-genre"),
            )
        assertEquals(listOf("genre" to "action"), composed(listOf(genres("csv")), picked).queryPairs)
    }

    @Test
    fun toggle_maps_to_declared_true_false_values_and_empty_false_value_is_omitted() {
        val adult =
            FilterDefinition(
                id = "adult",
                label = "Adult",
                type = "toggle",
                request = FilterRequestSpec(target = "query", param = "adult", trueValue = "1", falseValue = "0"),
            )
        assertEquals(listOf("adult" to "1"), composed(listOf(adult), selections("adult" to listOf("true"))).queryPairs)
        assertEquals(listOf("adult" to "0"), composed(listOf(adult), selections("adult" to listOf("false"))).queryPairs)
        // unset + no default = off
        assertEquals(listOf("adult" to "0"), composed(listOf(adult)).queryPairs)
        // falseValue "" ⇒ the off state vanishes from the request
        val quiet = adult.copy(request = FilterRequestSpec(target = "query", param = "adult", trueValue = "1"))
        assertEquals(emptyList(), composed(listOf(quiet)).queryPairs)
    }

    @Test
    fun number_and_text_values_pass_through_and_non_numeric_input_is_dropped() {
        val rating =
            FilterDefinition(
                id = "min_rating",
                label = "Min rating",
                type = "number",
                request = FilterRequestSpec(target = "query", param = "min_rating"),
            )
        val author =
            FilterDefinition(
                id = "author",
                label = "Author",
                type = "text",
                request = FilterRequestSpec(target = "query", param = "author"),
            )
        val result =
            composed(
                listOf(rating, author),
                selections("min_rating" to listOf("4.5"), "author" to listOf("oda")),
            )
        assertEquals(listOf("min_rating" to "4.5", "author" to "oda"), result.queryPairs)
        assertEquals(emptyList(), composed(listOf(rating), selections("min_rating" to listOf("high"))).queryPairs)
    }

    @Test
    fun hidden_filters_contribute_nothing_and_visibility_follows_logical_values() {
        // `demographic` is only visible when the `adult` toggle is ON — condition values are the
        // LOGICAL "true"/"false", independent of the toggle's wire encoding ("1"/"0").
        val adult =
            FilterDefinition(
                id = "adult",
                label = "Adult",
                type = "toggle",
                request = FilterRequestSpec(target = "query", param = "adult", trueValue = "1", falseValue = "0"),
            )
        val demographic =
            FilterDefinition(
                id = "demographic",
                label = "Demographic",
                type = "select",
                options = listOf(FilterOptionSpec("seinen")),
                default = "seinen",
                request = FilterRequestSpec(target = "query", param = "demo"),
                visibleWhen = listOf(FilterConditionSpec(filter = "adult", anyOf = listOf("true"))),
            )
        assertEquals(
            listOf("adult" to "0"),
            composed(listOf(adult, demographic)).queryPairs,
        )
        assertEquals(
            listOf("adult" to "1", "demo" to "seinen"),
            composed(listOf(adult, demographic), selections("adult" to listOf("true"))).queryPairs,
        )
    }

    @Test
    fun exclude_pairs_drop_values_selected_on_the_include_side() {
        val include = genres("csv")
        val exclude = genres("csv", id = "excluded_genres", param = "exclude", excludeOf = "genres")
        val result =
            composed(
                listOf(include, exclude),
                selections(
                    "genres" to listOf("action"),
                    "excluded_genres" to listOf("action", "drama"),
                ),
            )
        // include wins: "action" survives only on the include side.
        assertEquals(listOf("genre" to "action", "exclude" to "drama"), result.queryPairs)
    }

    @Test
    fun a_required_filter_resolving_empty_fails_closed() {
        val required = sort().copy(required = true) // no default, no selection
        val result = FilterRequestComposer.compose(listOf(required), "search", SourceFilterSelections())
        val failure = result as? SourceEngineResult.Failure ?: throw AssertionError("expected failure, got $result")
        assertEquals(SourceEngineError.Required("filter:sort"), failure.error)
    }

    @Test
    fun form_header_path_and_body_json_targets_route_to_their_sections() {
        val filters =
            listOf(
                sort(default = "latest").copy(request = FilterRequestSpec(target = "form", param = "vars[meta_key]")),
                FilterDefinition(
                    id = "lang",
                    label = "Language",
                    type = "select",
                    options = listOf(FilterOptionSpec("en")),
                    default = "en",
                    request = FilterRequestSpec(target = "header", param = "X-Lang"),
                ),
                FilterDefinition(
                    id = "section",
                    label = "Section",
                    type = "select",
                    options = listOf(FilterOptionSpec("man ga")),
                    default = "man ga",
                    request = FilterRequestSpec(target = "path", param = "section_seg"),
                ),
                genres("json-array").copy(
                    defaults = listOf("action", "drama"),
                    request = FilterRequestSpec(target = "body-json", param = "genres_json", encode = "json-array"),
                ),
            )
        val result = composed(filters)
        assertEquals(listOf("vars[meta_key]" to "latest"), result.formEntries)
        assertEquals(mapOf("X-Lang" to "en"), result.headerEntries)
        // path values are percent-encoded at composition (they land inside the URL template)
        assertEquals("man%20ga", result.templateVars["section_seg"])
        assertEquals("""["action","drama"]""", result.templateVars["genres_json"])
    }

    @Test
    fun body_json_scalar_is_json_escaped_and_empty_array_expands_deterministically() {
        val name =
            FilterDefinition(
                id = "name",
                label = "Name",
                type = "text",
                request = FilterRequestSpec(target = "body-json", param = "name_json"),
            )
        val quoted = composed(listOf(name), selections("name" to listOf("he said \"hi\"")))
        assertEquals("he said \\\"hi\\\"", quoted.templateVars["name_json"])
        // empty json-array hole expands to [] (a template hole cannot be omitted)
        val emptyArray =
            genres("json-array").copy(request = FilterRequestSpec(target = "body-json", param = "g", encode = "json-array"))
        assertEquals("[]", composed(listOf(emptyArray)).templateVars["g"])
    }

    @Test
    fun filters_not_applying_to_the_verb_are_ignored() {
        val searchOnly = sort(default = "latest")
        val result = FilterRequestComposer.compose(listOf(searchOnly), "home", SourceFilterSelections())
        assertEquals(ComposedFilters.EMPTY, (result as SourceEngineResult.Success).value)
    }

    @Test
    fun append_query_pairs_percent_encodes_names_and_values_with_url_join_awareness() {
        val appended =
            FilterRequestComposer.appendQueryPairs(
                "https://x.test/s?q=one%20piece",
                listOf("genre[]" to "action", "genre[]" to "sci fi"),
            )
        assertEquals("https://x.test/s?q=one%20piece&genre%5B%5D=action&genre%5B%5D=sci%20fi", appended)
        val fresh = FilterRequestComposer.appendQueryPairs("https://x.test/browse", listOf("orderby" to "latest"))
        assertEquals("https://x.test/browse?orderby=latest", fresh)
        assertEquals("https://x.test/plain", FilterRequestComposer.appendQueryPairs("https://x.test/plain", emptyList()))
    }

    @Test
    fun declaration_order_is_preserved_across_targets() {
        val filters =
            listOf(
                sort(default = "latest"),
                genres("repeat", defaults = listOf("action", "drama"), param = "genre[]"),
                FilterDefinition(
                    id = "status",
                    label = "Status",
                    type = "select",
                    options = listOf(FilterOptionSpec("ongoing")),
                    default = "ongoing",
                    request = FilterRequestSpec(target = "query", param = "status"),
                ),
            )
        val pairs = composed(filters).queryPairs
        assertEquals(
            listOf("orderby" to "latest", "genre[]" to "action", "genre[]" to "drama", "status" to "ongoing"),
            pairs,
        )
        assertTrue(pairs == pairs.sortedBy { p -> filters.indexOfFirst { it.request.param == p.first } })
    }
}
