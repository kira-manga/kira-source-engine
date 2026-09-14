package me.manga.kira.source.engine

import me.manga.kira.source.contracts.SourceConfigParser
import me.manga.kira.source.contracts.SourceEngineResult
import me.manga.kira.source.contracts.model.EndpointSpec
import me.manga.kira.source.contracts.model.FilterConditionSpec
import me.manga.kira.source.contracts.model.FilterDefinition
import me.manga.kira.source.contracts.model.FilterOptionSpec
import me.manga.kira.source.contracts.model.FilterRequestSpec
import me.manga.kira.source.contracts.model.SourceConfig
import me.manga.kira.source.contracts.model.SourceConfigDocument
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * Validation rules for the config-driven filter schema (CONFIG_DRIVEN_FILTERS_PLAN.md §4): every
 * malformed shape must be REJECTED with an error naming source → filter → field — never silently
 * ignored — while the full vocabulary of valid shapes passes.
 */
class DefaultSourceConfigValidatorFilterTest {
    private val validator = DefaultSourceConfigValidator(DefaultStrategyRegistry())

    private fun source(
        filters: List<FilterDefinition>,
        endpoints: Map<String, EndpointSpec> =
            mapOf(
                "home" to EndpointSpec(url = "{baseUrl}/h", listSelector = "div"),
                "search" to EndpointSpec(url = "{baseUrl}/s?q={queryEncoded}", listSelector = "div"),
            ),
        engine: String = "generic",
    ) = SourceConfig(
        api = "FilterSource",
        language = "en",
        baseUrl = "https://x.test",
        engine = engine,
        endpoints = endpoints,
        filters = filters,
    )

    private fun doc(source: SourceConfig) = SourceConfigDocument(schemaVersion = 1, sources = listOf(source))

    private fun select(
        id: String = "sort",
        options: List<FilterOptionSpec> = listOf(FilterOptionSpec("latest", "Latest"), FilterOptionSpec("views", "Views")),
        default: String = "",
        required: Boolean = false,
        request: FilterRequestSpec = FilterRequestSpec(target = "query", param = id),
        visibleWhen: List<FilterConditionSpec> = emptyList(),
    ) = FilterDefinition(
        id = id,
        label = id,
        type = "select",
        options = options,
        default = default,
        required = required,
        request = request,
        visibleWhen = visibleWhen,
    )

    private fun multiselect(
        id: String = "genres",
        options: List<FilterOptionSpec> = listOf(FilterOptionSpec("action"), FilterOptionSpec("drama")),
        defaults: List<String> = emptyList(),
        encode: String = "csv",
        excludeOf: String = "",
    ) = FilterDefinition(
        id = id,
        label = id,
        type = "multiselect",
        options = options,
        defaults = defaults,
        request = FilterRequestSpec(target = "query", param = id, encode = encode),
        excludeOf = excludeOf,
    )

    private fun errorsOf(source: SourceConfig): List<String> = validator.validate(doc(source)).errors

    private fun assertRejected(
        source: SourceConfig,
        vararg needles: String,
    ) {
        val errors = errorsOf(source)
        assertFalse(errors.isEmpty(), "expected validation errors, got none")
        for (needle in needles) {
            assertTrue(errors.any { it.contains(needle) }, "no error contains '$needle' in:\n${errors.joinToString("\n")}")
        }
    }

    // --- the green baseline: the full valid vocabulary in one source ---

    @Test
    fun a_full_valid_filter_vocabulary_validates() {
        val filters =
            listOf(
                select(id = "sort", default = "latest"),
                multiselect(id = "genres"),
                multiselect(id = "excluded_genres", excludeOf = "genres"),
                FilterDefinition(
                    id = "status",
                    label = "Status",
                    type = "select",
                    options = listOf(FilterOptionSpec("ongoing"), FilterOptionSpec("completed")),
                    request = FilterRequestSpec(target = "query", param = "status"),
                ),
                FilterDefinition(
                    id = "adult",
                    label = "Adult",
                    type = "toggle",
                    default = "false",
                    request = FilterRequestSpec(target = "query", param = "adult", trueValue = "1", falseValue = "0"),
                ),
                FilterDefinition(
                    id = "author",
                    label = "Author",
                    type = "text",
                    request = FilterRequestSpec(target = "query", param = "author"),
                ),
                FilterDefinition(
                    id = "min_rating",
                    label = "Min rating",
                    type = "number",
                    request = FilterRequestSpec(target = "query", param = "min_rating"),
                    visibleWhen = listOf(FilterConditionSpec(filter = "adult", anyOf = listOf("false"))),
                ),
            )
        assertEquals(emptyList(), errorsOf(source(filters)))
    }

    @Test
    fun a_filters_stanza_parses_from_json() {
        val parsed =
            SourceConfigParser.parse(
                """
                {"schemaVersion":1,"sources":[{
                  "api":"S","language":"en","baseUrl":"https://x.test","engine":"generic",
                  "endpoints":{
                    "home":{"url":"{baseUrl}/h","listSelector":"div"},
                    "search":{"url":"{baseUrl}/s?q={queryEncoded}","listSelector":"div"}},
                  "filters":[
                    {"id":"sort","label":"Sort","type":"select","default":"latest",
                     "options":[{"value":"latest","label":"Latest"},{"value":"views"}],
                     "request":{"target":"query","param":"orderby"}},
                    {"id":"genres","label":"Genres","type":"multiselect",
                     "options":[{"value":"action"},{"value":"drama"}],
                     "request":{"target":"query","param":"genre[]","encode":"repeat"}}
                  ]}]}
                """.trimIndent(),
            )
        val document = (parsed as SourceEngineResult.Success).value
        assertEquals(emptyList(), validator.validate(document).errors)
        val filters = document.sources.single().filters
        assertEquals(listOf("sort", "genres"), filters.map { it.id })
        assertEquals("Latest", filters[0].options[0].label)
        assertEquals("repeat", filters[1].request.encode)
    }

    // --- identity + structure ---

    @Test
    fun duplicate_filter_ids_are_rejected() {
        assertRejected(source(listOf(select(id = "sort"), select(id = "sort"))), "filter 'sort'", "duplicate filter id")
    }

    @Test
    fun blank_id_bad_charset_and_blank_label_are_rejected() {
        assertRejected(source(listOf(select(id = ""))), "blank id")
        assertRejected(source(listOf(select(id = "Sort-Order"))), "must match [a-z0-9_]{1,64}")
        assertRejected(
            source(listOf(select(id = "sort").copy(label = " "))),
            "filter 'sort'",
            "label: must not be blank",
        )
    }

    @Test
    fun unknown_and_reserved_control_types_are_rejected() {
        assertRejected(source(listOf(select().copy(type = "slider"))), "type: unknown 'slider'")
        // `range`/`date` are reserved vocabulary — rejected until the engine/UI implement them.
        assertRejected(source(listOf(select().copy(type = "range", options = emptyList()))), "type: unknown 'range'")
        assertRejected(source(listOf(select().copy(type = "date", options = emptyList()))), "type: unknown 'date'")
    }

    @Test
    fun option_rules_duplicate_values_blank_values_and_type_mismatches_are_rejected() {
        assertRejected(
            source(listOf(select(options = listOf(FilterOptionSpec("a"), FilterOptionSpec("a"))))),
            "duplicate option value (selection would be ambiguous)",
        )
        assertRejected(source(listOf(select(options = listOf(FilterOptionSpec(" "))))), "blank value")
        assertRejected(source(listOf(select(options = emptyList()))), "requires at least one option")
        assertRejected(
            source(
                listOf(
                    FilterDefinition(
                        id = "adult",
                        label = "Adult",
                        type = "toggle",
                        options = listOf(FilterOptionSpec("yes")),
                        request = FilterRequestSpec(target = "query", param = "adult"),
                    ),
                ),
            ),
            "options: not allowed on type 'toggle'",
        )
    }

    // --- defaults ---

    @Test
    fun invalid_defaults_are_rejected() {
        assertRejected(source(listOf(select(default = "nope"))), "default: value is not a declared option value")
        assertRejected(source(listOf(multiselect(defaults = listOf("nope")))), "defaults: value is not a declared option value")
        assertRejected(source(listOf(multiselect().copy(default = "action"))), "multiselect uses 'defaults'")
        assertRejected(source(listOf(select().copy(defaults = listOf("latest")))), "only multiselect uses 'defaults'")
        assertRejected(
            source(
                listOf(
                    FilterDefinition(
                        id = "adult",
                        label = "Adult",
                        type = "toggle",
                        default = "maybe",
                        request = FilterRequestSpec(target = "query", param = "adult"),
                    ),
                ),
            ),
            "toggle default must be 'true' or 'false'",
        )
        assertRejected(
            source(
                listOf(
                    FilterDefinition(
                        id = "min_rating",
                        label = "Min rating",
                        type = "number",
                        default = "high",
                        request = FilterRequestSpec(target = "query", param = "min_rating"),
                    ),
                ),
            ),
            "default: value is not numeric",
        )
        for (nonFinite in listOf("NaN", "Infinity", "-Infinity")) {
            assertRejected(
                source(
                    listOf(
                        FilterDefinition(
                            id = "min_rating",
                            label = "Min rating",
                            type = "number",
                            default = nonFinite,
                            request = FilterRequestSpec(target = "query", param = "min_rating"),
                        ),
                    ),
                ),
                "not numeric",
            )
        }
    }

    @Test
    fun required_filter_without_usable_default_is_rejected() {
        assertRejected(source(listOf(select(required = true))), "required filter needs a usable default")
        assertEquals(emptyList(), errorsOf(source(listOf(select(required = true, default = "latest")))))
    }

    // --- request mapping ---

    @Test
    fun unknown_target_and_encoding_are_rejected() {
        assertRejected(
            source(listOf(select(request = FilterRequestSpec(target = "cookie", param = "sort")))),
            "request.target: unknown 'cookie'",
        )
        assertRejected(
            source(listOf(select(request = FilterRequestSpec(target = "query", param = "sort", encode = "pipe")))),
            "request.encode: unknown 'pipe'",
        )
        assertRejected(source(listOf(select(request = FilterRequestSpec(target = "query", param = " ")))), "param: must not be blank")
    }

    @Test
    fun header_filter_names_must_be_exact_ascii_http_tokens() {
        val valid = select(id = "header", request = FilterRequestSpec(target = "header", param = "X-Lang"))
        assertEquals(emptyList(), errorsOf(source(listOf(valid))))
        val field = "source 'FilterSource': filters: filter 'header': request.param:"
        val invalidNames =
            listOf(
                "", " ", " X-Lang", "X-Lang ", "X Lang", "X\tLang", "X\r\nLang",
                "X\u0000Lang", "X\u007fLang", "X-Lang\u00e9", "X:Lang", "genre[]",
            )
        for (name in invalidNames) {
            val expected =
                buildList {
                    if (name.isBlank()) add("$field must not be blank")
                    add("$field header name must be a non-empty ASCII HTTP token without whitespace")
                }
            assertEquals(expected, errorsOf(source(listOf(valid.copy(request = valid.request.copy(param = name))))))
        }
    }

    @Test
    fun unsafe_header_names_are_rejected_independently_of_control_values_or_visibility() {
        val request = FilterRequestSpec(target = "header", param = "X-Lang")
        val text = FilterDefinition(id = "header", label = "Header", type = "text", request = request)
        val optionOnly = select(id = "header", request = request)
        val csv =
            multiselect(id = "header", defaults = listOf("action")).copy(
                required = true,
                request = request.copy(encode = "csv", delimiter = ";"),
            )
        val toggle =
            text.copy(
                type = "toggle",
                default = "false",
                request = request.copy(trueValue = "on", falseValue = "off", omitIfEmpty = false),
            )
        val number = text.copy(type = "number")
        // With the controller's default, this otherwise-valid filter is hidden.
        val controller = select(id = "controller", default = "latest")
        val hidden = text.copy(visibleWhen = listOf(FilterConditionSpec(filter = "controller", anyOf = listOf("views"))))
        val forbidden = "forbidden header names are not allowed for filters"
        val sensitive = "sensitive header names are not supported for filters"
        val cases =
            listOf(
                Triple("cOoKiE", optionOnly, forbidden),
                Triple("sEt-CoOkIe", toggle, forbidden),
                Triple("pRoXy-AuThOrIzAtIoN", number, forbidden),
                Triple("aUtHoRiZaTiOn", text, sensitive),
                Triple("X-aPi-KeY", optionOnly, sensitive),
                Triple("aPi-KeY", csv, sensitive),
                Triple("X-aUtH-tOkEn", toggle, sensitive),
                Triple("X-ReFrEsH-ToKeN-Mode", number, sensitive),
                Triple("X-Client-SeCrEt", hidden, sensitive),
                Triple("X-User-PaSsWoRd", text, sensitive),
                Triple("Authorization", text.copy(default = "Bearer null"), sensitive),
            )
        for ((name, safe, finding) in cases) {
            // No invalid default, encoding or cross-reference may make this rejection vacuous.
            assertEquals(emptyList(), errorsOf(source(listOf(controller, safe))))
            val unsafe = safe.copy(request = safe.request.copy(param = name))
            assertEquals(
                listOf("source 'FilterSource': filters: filter 'header': request.param: $finding"),
                errorsOf(source(listOf(controller, unsafe))),
            )
        }
    }

    @Test
    fun safe_header_tokens_and_static_public_placeholders_remain_accepted() {
        val names = listOf("X-Lang", "X-Content-Lang", "aZ09", "!#\$%&'*+-.^_`|~", "X-Cookie-Count", "X-Authorization-Mode")
        for (name in names) {
            val filter = select(id = "header", default = "latest", request = FilterRequestSpec(target = "header", param = name))
            assertEquals(
                emptyList(),
                errorsOf(source(listOf(filter)).copy(headers = mapOf("Authorization" to "Bearer null"))),
            )
        }
    }

    @Test
    fun rejected_header_filters_do_not_echo_synthetic_values_anywhere_in_the_validation_result() {
        val duplicate = "SYNTHETIC_DUPLICATE_OPTION_21"
        val selectDefault = "SYNTHETIC_SELECT_DEFAULT_21"
        val multiselectDefault = "SYNTHETIC_MULTISELECT_DEFAULT_21"
        val toggleDefault = "SYNTHETIC_TOGGLE_DEFAULT_21"
        val numberDefault = "SYNTHETIC_NUMBER_DEFAULT_21"
        val conditionValue = "SYNTHETIC_CONDITION_VALUE_21"
        val trueValue = "SYNTHETIC_TOGGLE_TRUE_21"
        val falseValue = "SYNTHETIC_TOGGLE_FALSE_21"
        val delimiter = "SYNTHETIC_CSV_DELIMITER_21"
        val overlap = "SYNTHETIC_OVERLAPPING_DEFAULT_21"
        val headerName = "X-Secret-SYNTHETIC_HEADER_NAME_21"
        val request = FilterRequestSpec(target = "header", param = "Authorization")
        val filters =
            listOf(
                select(
                    id = "selected",
                    options = listOf(FilterOptionSpec(duplicate), FilterOptionSpec(duplicate)),
                    default = selectDefault,
                    request = request,
                ),
                multiselect(
                    id = "included",
                    options = listOf(FilterOptionSpec("action"), FilterOptionSpec(overlap)),
                    defaults = listOf(multiselectDefault, overlap),
                ).copy(request = request.copy(param = "X-Api-Key", encode = "csv", delimiter = delimiter)),
                FilterDefinition(
                    id = "switch",
                    label = "Switch",
                    type = "toggle",
                    default = toggleDefault,
                    request = request.copy(param = "Api-Key", trueValue = trueValue, falseValue = falseValue),
                ),
                FilterDefinition(
                    id = "quantity",
                    label = "Quantity",
                    type = "number",
                    default = numberDefault,
                    request = request.copy(param = headerName),
                ),
                FilterDefinition(
                    id = "dependent",
                    label = "Dependent",
                    type = "text",
                    request = request.copy(param = "X-Lang"),
                    visibleWhen = listOf(FilterConditionSpec(filter = "selected", anyOf = listOf(conditionValue))),
                ),
                multiselect(
                    id = "excluded",
                    options = listOf(FilterOptionSpec(overlap)),
                    defaults = listOf(overlap),
                    excludeOf = "included",
                ).copy(request = request.copy(param = "X-Excluded", encode = "csv")),
            )
        val result = validator.validate(doc(source(filters)))
        assertFalse(result.isValid)
        val sensitive = "request.param: sensitive header names are not supported for filters"
        assertEquals(
            listOf(
                "filter 'selected': options: duplicate option value (selection would be ambiguous)",
                "filter 'selected': default: value is not a declared option value",
                "filter 'selected': $sensitive",
                "filter 'included': defaults: value is not a declared option value",
                "filter 'included': $sensitive",
                "filter 'switch': default: toggle default must be 'true' or 'false'",
                "filter 'switch': $sensitive",
                "filter 'quantity': default: value is not numeric",
                "filter 'quantity': $sensitive",
                "filter 'dependent': visibleWhen: anyOf value is not a possible value of filter 'selected'",
                "filter 'excluded': excludeOf: defaults overlap with filter 'included' — " +
                    "a value cannot default to included AND excluded",
            ).map { "source 'FilterSource': filters: $it" },
            result.errors,
        )
        // Check the complete result, not just the new name-policy diagnostic. Structural IDs are
        // deliberately valid and contain no sentinel: this is not arbitrary-field redaction.
        val diagnosticText = result.toString()
        val sentinels =
            listOf(
                duplicate, selectDefault, multiselectDefault, toggleDefault, numberDefault,
                conditionValue, trueValue, falseValue, delimiter, overlap, headerName,
            )
        for (sentinel in sentinels) {
            assertFalse(diagnosticText.contains(sentinel), "validation result must not echo synthetic filter data")
        }
    }

    @Test
    fun incompatible_encoding_target_and_type_combinations_are_rejected() {
        assertRejected(
            source(
                listOf(
                    multiselect().copy(request = FilterRequestSpec(target = "header", param = "x-g", encode = "repeat")),
                ),
            ),
            "'repeat' is only valid for query/form",
        )
        assertRejected(
            source(
                listOf(
                    multiselect().copy(request = FilterRequestSpec(target = "query", param = "g", encode = "json-array")),
                ),
            ),
            "'json-array' is only valid for the body-json target",
        )
        // Only a multiselect can produce multiple values.
        assertRejected(
            source(listOf(select(request = FilterRequestSpec(target = "query", param = "sort", encode = "csv")))),
            "'csv' requires type multiselect",
        )
    }

    @Test
    fun body_and_form_targets_require_the_matching_endpoint_method() {
        val getEndpoints =
            mapOf(
                "home" to EndpointSpec(url = "{baseUrl}/h", listSelector = "div"),
                "search" to EndpointSpec(url = "{baseUrl}/s?q={queryEncoded}", listSelector = "div"),
            )
        assertRejected(
            source(
                listOf(select(request = FilterRequestSpec(target = "form", param = "vars[sort]"))),
                endpoints = getEndpoints,
            ),
            "form target requires a post-form endpoint",
        )
        assertRejected(
            source(
                listOf(select(request = FilterRequestSpec(target = "body-json", param = "sort"))),
                endpoints = getEndpoints,
            ),
            "body-json target requires a post-json endpoint",
        )
    }

    @Test
    fun filters_mapped_to_missing_or_unsupported_endpoints_are_rejected() {
        assertRejected(
            source(
                listOf(select()),
                endpoints = mapOf("home" to EndpointSpec(url = "{baseUrl}/h", listSelector = "div")),
            ),
            "appliesTo 'search'",
            "endpoint that does not exist",
        )
        assertRejected(source(listOf(select().copy(appliesTo = listOf("home")))), "unsupported verb")
        assertRejected(source(listOf(select().copy(appliesTo = emptyList()))), "appliesTo: must not be empty")
    }

    @Test
    fun placeholder_target_rules_are_enforced() {
        // path param must exist in the url template…
        assertRejected(
            source(listOf(select(default = "latest", request = FilterRequestSpec(target = "path", param = "sort_seg")))),
            "url template does not contain the placeholder {sort_seg}",
        )
        // …must not shadow a reserved engine var…
        assertRejected(
            source(listOf(select(default = "latest", request = FilterRequestSpec(target = "path", param = "page")))),
            "shadows a reserved engine template var",
        )
        // …and needs a guaranteed value (a hole cannot be omitted).
        assertRejected(
            source(
                listOf(select(request = FilterRequestSpec(target = "path", param = "sort_seg"))),
                endpoints =
                    mapOf(
                        "home" to EndpointSpec(url = "{baseUrl}/h", listSelector = "div"),
                        "search" to EndpointSpec(url = "{baseUrl}/{sort_seg}?q={queryEncoded}", listSelector = "div"),
                    ),
            ),
            "must declare a non-empty default",
        )
        // body-json placeholder must exist in the jsonBody template.
        assertRejected(
            source(
                listOf(
                    multiselect().copy(
                        request = FilterRequestSpec(target = "body-json", param = "genres", encode = "json-array"),
                    ),
                ),
                endpoints =
                    mapOf(
                        "home" to EndpointSpec(url = "{baseUrl}/h", listSelector = "div"),
                        "search" to
                            EndpointSpec(
                                url = "{baseUrl}/api/search",
                                method = "post-json",
                                jsonBody = """{"q":"{queryJson}"}""",
                                root = "results",
                            ),
                    ),
            ),
            "jsonBody template does not contain the placeholder {genres}",
        )
    }

    @Test
    fun param_collisions_with_static_endpoint_parts_are_rejected() {
        // form param vs static formBody key
        assertRejected(
            source(
                listOf(select(request = FilterRequestSpec(target = "form", param = "action"))),
                endpoints =
                    mapOf(
                        "home" to EndpointSpec(url = "{baseUrl}/h", listSelector = "div"),
                        "search" to
                            EndpointSpec(
                                url = "{baseUrl}/admin-ajax.php",
                                method = "post-form",
                                formBody = mapOf("action" to "load_more"),
                                listSelector = "div",
                            ),
                    ),
            ),
            "collides with a static formBody key",
        )
        // query param already hardcoded in the url template
        assertRejected(
            source(
                listOf(select(request = FilterRequestSpec(target = "query", param = "q"))),
            ),
            "already hardcoded in the url template",
        )
    }

    // --- cross-filter references ---

    @Test
    fun unknown_dependency_references_are_rejected() {
        assertRejected(
            source(listOf(select(visibleWhen = listOf(FilterConditionSpec(filter = "ghost", anyOf = listOf("x")))))),
            "references unknown filter 'ghost'",
        )
    }

    @Test
    fun self_reference_empty_anyOf_and_impossible_values_are_rejected() {
        assertRejected(
            source(
                listOf(
                    select(id = "sort", visibleWhen = listOf(FilterConditionSpec(filter = "sort", anyOf = listOf("latest")))),
                ),
            ),
            "cannot depend on itself",
        )
        assertRejected(
            source(
                listOf(
                    select(id = "sort"),
                    multiselect(id = "genres").copy(visibleWhen = listOf(FilterConditionSpec(filter = "sort", anyOf = emptyList()))),
                ),
            ),
            "anyOf must not be empty",
        )
        assertRejected(
            source(
                listOf(
                    select(id = "sort"),
                    multiselect(id = "genres").copy(
                        visibleWhen = listOf(FilterConditionSpec(filter = "sort", anyOf = listOf("nonsense"))),
                    ),
                ),
            ),
            "anyOf value is not a possible value",
        )
    }

    @Test
    fun dependency_cycles_are_rejected() {
        val a =
            select(id = "aaa").copy(visibleWhen = listOf(FilterConditionSpec(filter = "bbb", anyOf = listOf("latest"))))
        val b =
            select(id = "bbb", request = FilterRequestSpec(target = "query", param = "bbb"))
                .copy(visibleWhen = listOf(FilterConditionSpec(filter = "aaa", anyOf = listOf("latest"))))
        assertRejected(source(listOf(a, b)), "dependency cycle detected")
    }

    @Test
    fun oversized_deep_visibility_chains_are_rejected_without_recursive_traversal() {
        val filters =
            List(DefaultSourceConfigValidator.MAX_FILTERS + 1) { index ->
                select(
                    id = "f$index",
                    request = FilterRequestSpec(target = "query", param = "f$index"),
                    visibleWhen =
                        if (index == 0) {
                            emptyList()
                        } else {
                            listOf(FilterConditionSpec(filter = "f${index - 1}", anyOf = listOf("latest")))
                        },
                )
            }

        assertRejected(source(filters), "maximum is ${DefaultSourceConfigValidator.MAX_FILTERS}")
    }

    @Test
    fun invalid_include_exclude_combinations_are_rejected() {
        // non-multiselect exclusion side
        assertRejected(
            source(listOf(multiselect(id = "genres"), select(id = "sort").copy(excludeOf = "genres"))),
            "only a multiselect can be an exclusion counterpart",
        )
        // unknown include side
        assertRejected(source(listOf(multiselect(id = "excluded", excludeOf = "ghost"))), "references unknown filter 'ghost'")
        // self-exclusion
        assertRejected(source(listOf(multiselect(id = "genres", excludeOf = "genres"))), "cannot exclude against itself")
        // chained exclusion
        assertRejected(
            source(
                listOf(
                    multiselect(id = "a1"),
                    multiselect(id = "b1", excludeOf = "a1"),
                    multiselect(id = "c1", excludeOf = "b1"),
                ),
            ),
            "chained exclusion",
        )
        // overlapping defaults: the same value cannot default to included AND excluded
        assertRejected(
            source(
                listOf(
                    multiselect(id = "genres", defaults = listOf("action")),
                    multiselect(id = "excluded", defaults = listOf("action"), excludeOf = "genres"),
                ),
            ),
            "defaults overlap",
        )
    }

    // --- conventions + engine gating ---

    @Test
    fun standard_ids_with_incompatible_types_are_rejected() {
        assertRejected(
            source(
                listOf(
                    FilterDefinition(
                        id = "sort",
                        label = "Sort",
                        type = "text",
                        request = FilterRequestSpec(target = "query", param = "orderby"),
                    ),
                ),
            ),
            "standard id 'sort' must be select",
        )
        assertRejected(
            source(
                listOf(
                    FilterDefinition(
                        id = "genres",
                        label = "Genres",
                        type = "toggle",
                        request = FilterRequestSpec(target = "query", param = "genre"),
                    ),
                ),
            ),
            "standard id 'genres' must be select or multiselect",
        )
    }

    @Test
    fun filters_on_a_non_generic_engine_are_rejected() {
        assertRejected(
            source(listOf(select(default = "latest")), engine = "legacy"),
            "filters are a generic-engine capability",
        )
    }

    @Test
    fun error_messages_carry_the_source_filter_path() {
        val errors = errorsOf(source(listOf(select(default = "nope"))))
        assertTrue(
            errors.any { it.startsWith("source 'FilterSource': filters: filter 'sort': default:") },
            "expected a source→filters→id→field path, got:\n${errors.joinToString("\n")}",
        )
    }
}
