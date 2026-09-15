package me.manga.kira.sources.testkit

import me.manga.kira.source.contracts.model.EndpointSpec
import me.manga.kira.source.contracts.model.FieldSpec
import me.manga.kira.source.contracts.model.FilterConditionSpec
import me.manga.kira.source.contracts.model.FilterDefinition
import me.manga.kira.source.contracts.model.FilterRequestSpec
import me.manga.kira.source.contracts.model.FilterSpec
import me.manga.kira.source.contracts.model.PaginationSpec
import me.manga.kira.source.contracts.model.SourceConfig

/** Data-only expectation: consumers compare structured findings, never parse display messages. */
data class ExpectedDeclarationFinding(val code: String, val path: String)

data class DeclarationCapabilityFixture(
    val id: String,
    val source: SourceConfig,
    val expectedFindings: List<ExpectedDeclarationFinding> = emptyList(),
    /** Important limits of the assertion, rather than a claim of response-data validity. */
    val note: String = "",
)

/**
 * Synthetic, network-free declaration vectors shared by Engine and future App/Backend adapters.
 * Expected findings concern the capability checker; ordinary schema/filter/strategy checks still
 * apply. Positive declarations are not promises of nonempty responses or runtime filter visibility.
 */
object DeclarationCapabilityFixtures {
    // Independent fixture vocabulary: also asserted against actual GenericSourceEngine requests.
    val requestNames: List<String> = listOf(
        "baseUrl", "imageBase", "page", "pageOffset", "query", "queryEncoded", "queryJson",
        "itemUrl", "chapterUrl", "id",
    )

    val seededRequestNames: SourceConfig = source(
        endpoints = listOf("home", "featured", "search", "details", "chapters", "pages").associateWith { verb ->
            EndpointSpec(
                url = "{baseUrl}/$verb",
                method = "POST_FORM",
                format = "json",
                root = "items",
                formBody = requestNames.associateWith { "{$it}" },
            )
        },
    ).copy(pagination = PaginationSpec(start = 7))

    val conditionalPlaceholder: SourceConfig = source(
        endpoints = mapOf("search" to EndpointSpec("{baseUrl}/{category}", format = "json")),
        filters = listOf(
            FilterDefinition("advanced", "Advanced", "toggle", default = "false", request = FilterRequestSpec("query", "advanced")),
            FilterDefinition(
                "category", "Category", "text", default = "all", request = FilterRequestSpec("path", "category"),
                visibleWhen = listOf(FilterConditionSpec("advanced", listOf("true"))),
            ),
        ),
    )

    val cases: List<DeclarationCapabilityFixture> = buildList {
        fun accept(id: String, source: SourceConfig, note: String = "") {
            add(DeclarationCapabilityFixture(id, source, note = note))
        }
        fun reject(id: String, source: SourceConfig, code: String, path: String) {
            add(DeclarationCapabilityFixture(id, source, listOf(ExpectedDeclarationFinding(code, path))))
        }

        accept("all_seeded_request_names_including_empty", seededRequestNames)
        accept("json_braces_and_query_encoded", source(endpoints = mapOf(
            "search" to EndpointSpec("{baseUrl}/search", method = "post-json", jsonBody = """{"query":"{queryEncoded}","nested":{"raw":"{queryJson}"}}"""),
        )))
        accept("only_sent_values_are_templated", source(endpoints = mapOf(
            "home" to EndpointSpec("{baseUrl}/home", formBody = mapOf("key" to "{unused}"), jsonBody = """{"unused":"{unused}"}"""),
            "search" to EndpointSpec("{baseUrl}/search", method = "post-form", formBody = mapOf("{literal_key}" to "{query}"), jsonBody = "{unused}"),
        )).copy(headers = mapOf("X-Fixture" to "{literal_header}")))

        for (method in listOf("post-form", "POST_FORM", "PostForm")) {
            accept("form_method_$method", source(endpoints = mapOf(
                "search" to EndpointSpec("{baseUrl}/search", method = method, formBody = mapOf("q" to "{query}")),
            )))
            reject("unknown_form_variable_$method", source(endpoints = mapOf(
                "search" to EndpointSpec("{baseUrl}/search", method = method, formBody = mapOf("q" to "{unknownRuntimeVar}")),
            )), "template.variable.unsupported", "endpoints[search].formBody[0].value")
        }
        for (method in listOf("post-json", "POST_JSON", "PostJson")) {
            accept("json_method_$method", source(endpoints = mapOf(
                "search" to EndpointSpec("{baseUrl}/search", method = method, jsonBody = """{"q":"{queryJson}"}"""),
            )))
            reject("unknown_json_variable_$method", source(endpoints = mapOf(
                "search" to EndpointSpec("{baseUrl}/search", method = method, jsonBody = """{"q":"{unknownRuntimeVar}"}"""),
            )), "template.variable.unsupported", "endpoints[search].jsonBody")
        }
        for (method in listOf("", "get", "GET")) {
            accept("get_method_$method", source(endpoints = mapOf("home" to EndpointSpec("{baseUrl}/home", method = method))))
        }
        reject("unknown_url_variable", source(endpoints = mapOf("home" to EndpointSpec("{baseUrl}/{unknownRuntimeVar}"))),
            "template.variable.unsupported", "endpoints[home].url")
        reject("request_names_are_case_sensitive", source(endpoints = mapOf("home" to EndpointSpec("{baseUrl}/{Page}"))),
            "template.variable.unsupported", "endpoints[home].url")

        val pathFilter = FilterDefinition("category", "Category", "text", default = "all", request = FilterRequestSpec("path", "category"))
        val bodyFilter = FilterDefinition("term", "Term", "text", request = FilterRequestSpec("body-json", "term"))
        accept("placeholder_filters_share_active_request_namespace", source(
            endpoints = mapOf("search" to EndpointSpec(
                "{baseUrl}/{category}?term={term}", method = "post-json", jsonBody = """{"category":"{category}","term":"{term}"}""",
            )), filters = listOf(pathFilter, bodyFilter),
        ))
        accept("path_filter_also_available_in_form_values", source(
            endpoints = mapOf("search" to EndpointSpec("{baseUrl}/{category}", method = "post-form", formBody = mapOf("category" to "{category}"))),
            filters = listOf(pathFilter),
        ))
        accept("hidden_placeholder_is_not_a_runtime_binding_guarantee", conditionalPlaceholder,
            "Accepted namespace only: default visibility hides category, so the existing composer contributes no category variable.")
        reject("query_filters_do_not_define_template_names", source(
            endpoints = mapOf("search" to EndpointSpec("{baseUrl}/{genre}")),
            filters = listOf(FilterDefinition("genre", "Genre", "text", request = FilterRequestSpec("query", "genre"))),
        ), "template.variable.unsupported", "endpoints[search].url")
        for (target in listOf("form", "header")) {
            reject("${target}_filters_do_not_define_template_names", source(
                endpoints = mapOf("search" to EndpointSpec("{baseUrl}/search", method = "post-form", formBody = mapOf("static" to "{genre}"))),
                filters = listOf(FilterDefinition("genre", "Genre", "text", request = FilterRequestSpec(target, "genre"))),
            ), "template.variable.unsupported", "endpoints[search].formBody[0].value")
        }
        reject("search_filter_not_available_on_home", source(
            endpoints = mapOf("home" to EndpointSpec("{baseUrl}/{category}"), "search" to EndpointSpec("{baseUrl}/{category}")),
            filters = listOf(pathFilter),
        ), "template.variable.unsupported", "endpoints[home].url")

        accept("field_own_vars_coalescing_pipes_and_base_shadow", source(fields = mapOf(
            "item.url" to FieldSpec(template = "{baseUrl}/book/{id}", vars = mapOf("baseUrl" to "host", "id" to "primary, alternate| trim |format-number|replace")),
        )))
        accept("field_empty_json_var_is_current_primitive", source(fields = mapOf(
            "item.title" to FieldSpec(template = "Title {value}", vars = mapOf("value" to "")),
        )))
        accept("dir_sentinel_can_be_known_empty_outside_pages", source(fields = mapOf(
            "item.title" to FieldSpec(template = "Title {dir}", vars = mapOf("dir" to "root:__dir")),
        )), "No chosen directory is supplied outside page.image; this sentinel is nevertheless defined as empty.")
        accept("page_root_and_dir_without_root_dirs", source(
            endpoints = mapOf("pages" to EndpointSpec("{chapterUrl}", root = "webp_pages,pages")),
            fields = mapOf("page.image" to FieldSpec(template = "{imageBase}/{key}/{dir}/{file}", vars = mapOf(
                "key" to "root:storage_key", "dir" to "root:__dir", "file" to "url, fallback",
            ))),
        ))
        accept("root_var_comma_is_a_literal_json_key_not_coalescing", source(
            endpoints = mapOf("pages" to EndpointSpec("{chapterUrl}", root = "pages", rootDirs = listOf("unpaired", "extra"))),
            fields = mapOf("page.image" to FieldSpec(template = "{key}", vars = mapOf("key" to "root:storage,key"))),
        ))
        reject("request_id_does_not_leak_into_field_template", source(fields = mapOf("item.url" to FieldSpec(template = "{baseUrl}/{id}"))),
            "template.variable.unsupported", "fields[item.url].template")
        reject("request_query_does_not_leak_into_field_template", source(fields = mapOf("item.title" to FieldSpec(template = "{queryJson}"))),
            "template.variable.unsupported", "fields[item.title].template")
        reject("direct_root_token_is_not_interpolation", source(fields = mapOf("item.url" to FieldSpec(template = "{baseUrl}/{root:id}"))),
            "template.root.unsupported", "fields[item.url].template")
        reject("root_unavailable_to_item_field", source(fields = mapOf("item.url" to FieldSpec(template = "{id}", vars = mapOf("id" to "root:id")))),
            "field.variable.root_context", "fields[item.url].vars[0]")
        for (expression in listOf("id|unknown", "id|trim()", "id|")) {
            reject("unsupported_var_pipe_$expression", source(fields = mapOf("item.url" to FieldSpec(template = "{id}", vars = mapOf("id" to expression)))),
                "field.variable.transform", "fields[item.url].vars[0]")
        }
        reject("invalid_field_variable_key", source(fields = mapOf("item.title" to FieldSpec(template = "fallback", vars = mapOf("not-a-token" to "title")))),
            "field.variable.name", "fields[item.title].vars[0]")
        reject("genre_lists_do_not_expand_templates", source(fields = mapOf("item.genres" to FieldSpec(template = "literal"))),
            "field.template.unsupported", "fields[item.genres].template")
        reject("genre_lists_do_not_resolve_vars", source(fields = mapOf("item.genres" to FieldSpec(vars = mapOf("genre" to "name")))),
            "field.vars.unsupported", "fields[item.genres].vars")
        reject("nested_lists_do_not_expand_templates", source(fields = mapOf("item.recentChapters" to FieldSpec(template = "literal"))),
            "field.template.unsupported", "fields[item.recentChapters].template")
        reject("chapter_dates_do_not_expand_templates", source(
            endpoints = mapOf("chapters" to EndpointSpec("{itemUrl}/chapters")), fields = mapOf("chapter.date" to FieldSpec(template = "literal")),
        ), "field.template.unsupported", "fields[chapter.date].template")

        for (root in listOf("", " ", "$", ".", "${'$'}.", "data", ".data", "${'$'}data", "${'$'}.data", "data.", "[*].data[*]", "[+01]", "[-0]")) {
            accept("json_root_alias_$root", source(endpoints = mapOf("home" to EndpointSpec("{baseUrl}/home", root = root))))
        }
        accept("literal_json_keys_not_ascii_identifiers", source(fields = mapOf(
            "item.title" to FieldSpec(path = "タイトル.اسم-المجلد", fallbackPath = "title]"),
            "item.genres" to FieldSpec(listPath = "genre,labels[*]"),
        )))
        accept("inline_json_chapters_coalesce", source(
            endpoints = mapOf("details" to EndpointSpec("{itemUrl}")), fields = mapOf("detail.chapters" to FieldSpec(listPath = "preferred, chapters")),
        ))
        accept("nested_json_list_comma_is_a_literal_key", source(fields = mapOf("item.recentChapters" to FieldSpec(listPath = "recent,chapters"))))
        for (root in listOf("items[?(@.active)]", "items[0:2]", "items[0,1]", "items['title']", "items[-1]", "items[2147483648]", "items[0", "items[0]suffix", "items[0][1]", "items..id", "${'$'}..items", "items[]")) {
            reject("unsupported_json_root_$root", source(endpoints = mapOf("home" to EndpointSpec("{baseUrl}/home", root = root))),
                "locator.json.unsupported", "endpoints[home].root")
        }
        reject("json_scalar_path", source(fields = mapOf("item.title" to FieldSpec(path = "title[0:]"))),
            "locator.json.unsupported", "fields[item.title].path")
        reject("json_fallback_path", source(fields = mapOf("item.title" to FieldSpec(path = "title", fallbackPath = "fallback[?(@.active)]"))),
            "locator.json.unsupported", "fields[item.title].fallbackPath")
        reject("json_list_path", source(fields = mapOf("item.genres" to FieldSpec(listPath = "genres[1:]"))),
            "locator.json.unsupported", "fields[item.genres].listPath")
        reject("json_var_locator", source(fields = mapOf("item.url" to FieldSpec(template = "{id}", vars = mapOf("id" to "items[0][1]")))),
            "locator.json.unsupported", "fields[item.url].vars[0]")
        reject("json_list_filter_locator", source(endpoints = mapOf("home" to EndpointSpec("{baseUrl}/home", listFilters = listOf(FilterSpec("items..id", "notNull"))))),
            "locator.json.unsupported", "endpoints[home].listFilters[0].path")

        val htmlHome = EndpointSpec("{baseUrl}/home", format = "html", listSelector = "div:has(a[href]):not(.locked)")
        accept("css_real_parser_not_a_regex_subset", source(endpoints = mapOf("home" to htmlHome), fields = mapOf(
            "item.title" to FieldSpec(selector = "h3:contains(الفصل)", fallbackSelectors = listOf("a[href^='/read/'] + span", "p:not(:has(i))")),
            "item.cover" to FieldSpec(selector = "img:nth-child(2)", attr = "abs:src"),
            "item.url" to FieldSpec(template = "{slug}", vars = mapOf("slug" to "a.primary, a.alternate|trim")),
        )))
        accept("html_empty_scalar_is_current_element", source(endpoints = mapOf("home" to htmlHome), fields = mapOf("item.title" to FieldSpec())))
        accept("format_inference_uses_list_selector", source(endpoints = mapOf("home" to htmlHome.copy(format = "")), fields = mapOf("item.title" to FieldSpec(selector = "h3"))))
        reject("malformed_css_endpoint", source(endpoints = mapOf("home" to htmlHome.copy(listSelector = "div["))),
            "locator.css.unsupported", "endpoints[home].listSelector")
        reject("malformed_css_field", source(endpoints = mapOf("home" to htmlHome), fields = mapOf("item.title" to FieldSpec(selector = "div["))),
            "locator.css.unsupported", "fields[item.title].selector")
        reject("empty_css_fallback_is_not_current_element", source(endpoints = mapOf("home" to htmlHome), fields = mapOf("item.title" to FieldSpec(fallbackSelectors = listOf("")))),
            "locator.css.unsupported", "fields[item.title].fallbackSelectors[0]")
        reject("empty_css_raw_var_is_not_current_element", source(endpoints = mapOf("home" to htmlHome), fields = mapOf("item.title" to FieldSpec(template = "{value}", vars = mapOf("value" to "")))),
            "locator.css.unsupported", "fields[item.title].vars[0]")

        accept("mixed_formats_respect_field_overrides", source(
            endpoints = mapOf("search" to htmlHome, "chapters" to EndpointSpec("{itemUrl}/chapters", root = "chapters")),
            fields = mapOf(
                "item.title" to FieldSpec(path = "title", selector = "ignored["),
                "search.item.title" to FieldSpec(selector = "h3", path = "ignored..path"),
                "chapter.url" to FieldSpec(path = "slug", selector = "a"),
            ),
        ))
        reject("shared_chapter_fields_use_recent_item_format", source(
            endpoints = mapOf("search" to htmlHome), fields = mapOf(
                "item.recentChapters" to FieldSpec(listPath = "recent", listSelector = "li"),
                "chapter.url" to FieldSpec(path = "slug", selector = "a["),
            ),
        ), "locator.css.unsupported", "fields[chapter.url].selector")

        val script = EndpointSpec("{itemUrl}", format = "script-json", root = "props.items")
        accept("script_json_list_scalar_details_and_pages", source(
            endpoints = mapOf("home" to script, "details" to script, "pages" to script.copy(url = "{chapterUrl}")),
            fields = mapOf("detail.title" to FieldSpec(path = "props.title"), "page.image" to FieldSpec(path = "url")),
        ))
        accept("script_json_single_chapter_request", source(endpoints = mapOf("chapters" to script)))
        reject("script_json_inline_chapters_unsupported", source(
            endpoints = mapOf("details" to script), fields = mapOf("detail.chapters" to FieldSpec(listPath = "props.chapters")),
        ), "field.script_json.inline_chapters", "fields[detail.chapters]")
        reject("script_json_uses_json_field_grammar", source(
            endpoints = mapOf("pages" to script), fields = mapOf("page.image" to FieldSpec(path = "images[0:]")),
        ), "locator.json.unsupported", "fields[page.image].path")
        reject("script_selector_is_checked_without_exception_echo", source(endpoints = mapOf("pages" to script.copy(scriptId = "bad["))),
            "locator.css.unsupported", "endpoints[pages].scriptId")

        val paged = EndpointSpec("{itemUrl}/chapters?wirePage={chapterPage}", root = "chapters", pageParam = "chapterPage", lastPageLocator = "next")
        accept("single_request_chapters", source(endpoints = mapOf("chapters" to paged.copy(pageParam = "", lastPageLocator = "", url = "{itemUrl}/chapters"))))
        for (locator in listOf("last_page", "has_next", "next")) {
            accept("numeric_boolean_or_next_signal_$locator", source(endpoints = mapOf("chapters" to paged.copy(lastPageLocator = locator))))
        }
        accept("counter_in_active_form_value", source(endpoints = mapOf("chapters" to paged.copy(
            url = "{itemUrl}/chapters", method = "post_form", formBody = mapOf("wirePage" to "{chapterPage}"),
        ))))
        accept("counter_in_active_json_body", source(endpoints = mapOf("chapters" to paged.copy(
            url = "{itemUrl}/chapters", method = "postjson", jsonBody = """{"wirePage":{chapterPage}}""",
        ))))
        accept("source_pagination_param_is_not_wire_construction", source(endpoints = mapOf("chapters" to paged)).copy(pagination = PaginationSpec(param = "unused", start = 9)))
        accept("html_numeric_termination_selector", source(endpoints = mapOf("chapters" to paged.copy(format = "html", listSelector = "li", lastPageLocator = "ul.pagination a"))))
        reject("counter_missing_termination", source(endpoints = mapOf("chapters" to paged.copy(lastPageLocator = ""))),
            "pagination.termination.required", "endpoints[chapters].lastPageLocator")
        reject("blank_termination_is_not_a_signal", source(endpoints = mapOf("chapters" to paged.copy(lastPageLocator = " "))),
            "pagination.termination.required", "endpoints[chapters].lastPageLocator")
        reject("termination_missing_counter", source(endpoints = mapOf("chapters" to paged.copy(pageParam = "", url = "{itemUrl}/chapters"))),
            "pagination.counter.required", "endpoints[chapters].pageParam")
        reject("counter_is_not_a_wire_parameter_name", source(endpoints = mapOf("chapters" to paged.copy(pageParam = "wirePage", url = "{itemUrl}/chapters?wirePage={page}"))),
            "pagination.counter.unconsumed", "endpoints[chapters].pageParam")
        reject("page_counter_does_not_update_page_offset", source(endpoints = mapOf("chapters" to paged.copy(pageParam = "page", url = "{itemUrl}/chapters?p={pageOffset}"))),
            "pagination.counter.unconsumed", "endpoints[chapters].pageParam")
        reject("counter_name_must_be_recognizable", source(endpoints = mapOf("chapters" to paged.copy(pageParam = "chapter-page", url = "{itemUrl}/chapters?p={chapter-page}"))),
            "pagination.counter.name", "endpoints[chapters].pageParam")
        for ((id, endpoint) in listOf(
            "inactive_form" to paged.copy(method = "get", formBody = mapOf("p" to "{chapterPage}")),
            "inactive_json" to paged.copy(method = "post-form", jsonBody = """{"p":{chapterPage}}"""),
            "static_form_key" to paged.copy(method = "post-form", formBody = mapOf("{chapterPage}" to "1")),
        )) {
            reject("counter_only_in_$id", source(endpoints = mapOf("chapters" to endpoint.copy(url = "{itemUrl}/chapters"))),
                "pagination.counter.unconsumed", "endpoints[chapters].pageParam")
        }
        reject("counter_only_in_header", source(endpoints = mapOf("chapters" to paged.copy(url = "{itemUrl}/chapters"))).copy(headers = mapOf("X-Page" to "{chapterPage}")),
            "pagination.counter.unconsumed", "endpoints[chapters].pageParam")
        reject("counter_only_in_field_template", source(
            endpoints = mapOf("chapters" to paged.copy(url = "{itemUrl}/chapters")),
            fields = mapOf("chapter.url" to FieldSpec(template = "{chapterPage}", vars = mapOf("chapterPage" to "id"))),
        ), "pagination.counter.unconsumed", "endpoints[chapters].pageParam")
        reject("script_json_pagination_is_not_implemented", source(endpoints = mapOf("chapters" to paged.copy(format = "script-json"))),
            "pagination.script_json.unsupported", "endpoints[chapters].lastPageLocator")
        reject("unsupported_termination_locator", source(endpoints = mapOf("chapters" to paged.copy(lastPageLocator = "meta..next"))),
            "locator.json.unsupported", "endpoints[chapters].lastPageLocator")
        add(DeclarationCapabilityFixture("automatic_pagination_only_on_chapters", source(endpoints = mapOf(
            "home" to paged.copy(url = "{baseUrl}/home?page={page}", pageParam = "page"),
        )), listOf(
            ExpectedDeclarationFinding("pagination.context.unsupported", "endpoints[home].pageParam"),
            ExpectedDeclarationFinding("pagination.context.unsupported", "endpoints[home].lastPageLocator"),
        )))
        reject("unknown_endpoint_does_not_gain_pagination", source(endpoints = mapOf("https://private.invalid/not-a-verb" to EndpointSpec("unused", pageParam = "page"))),
            "pagination.context.unsupported", "endpoints[1].pageParam")
        accept("non_generic_behavior_not_inspected", source(endpoints = mapOf("home" to EndpointSpec("{unknown}", root = "items..id"))).copy(engine = "legacy"))
    }

    private fun source(
        endpoints: Map<String, EndpointSpec> = emptyMap(),
        fields: Map<String, FieldSpec> = emptyMap(),
        filters: List<FilterDefinition> = emptyList(),
    ): SourceConfig = SourceConfig(
        api = "declaration-fixture",
        language = "en",
        engine = "generic",
        baseUrl = "https://example.test",
        imageBase = "https://images.example.test",
        usesCapturedHeaders = false,
        endpoints = mapOf("home" to EndpointSpec("{baseUrl}/home", format = "json", root = "items")) + endpoints,
        fields = fields,
        filters = filters,
    )
}
