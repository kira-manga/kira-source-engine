package me.manga.kira.source.engine

import kotlinx.coroutines.test.runTest
import me.manga.kira.source.contracts.SourceEngineResult
import me.manga.kira.source.contracts.SourceFilterSelections
import me.manga.kira.source.contracts.SourceTransport
import me.manga.kira.source.contracts.SourceRequest
import me.manga.kira.source.contracts.SourceResponse
import me.manga.kira.source.contracts.model.EndpointSpec
import me.manga.kira.source.contracts.model.FieldSpec
import me.manga.kira.source.contracts.model.FilterDefinition
import me.manga.kira.source.contracts.model.FilterOptionSpec
import me.manga.kira.source.contracts.model.FilterRequestSpec
import me.manga.kira.source.contracts.model.SourceConfig
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * End-to-end request assertions for config-driven filters: a [GenericSourceEngine] built from a
 * filter-carrying stanza turns `search(query, page, selections)` into the EXACT expected outgoing
 * [SourceRequest] — URL, form body, JSON body, headers — with zero source-specific code
 * (CONFIG_DRIVEN_FILTERS_PLAN.md §8, test items 2–9).
 */
class GenericSourceClientFilterRequestTest {
    private class RecordingHttp(
        private val body: String = """{"items":[{"title":"T","url":"https://f.test/m/1"}]}""",
    ) : SourceTransport {
        val requests = mutableListOf<SourceRequest>()

        override suspend fun execute(request: SourceRequest): SourceResponse {
            requests += request
            return SourceResponse(status = 200, body = body)
        }
    }

    private fun config(
        filters: List<FilterDefinition>,
        search: EndpointSpec = EndpointSpec(url = "{baseUrl}/search?q={queryEncoded}", root = "items"),
    ) = SourceConfig(
        api = "FilterPilot",
        language = "en",
        baseUrl = "https://f.test",
        engine = "generic",
        endpoints = mapOf("home" to EndpointSpec(url = "{baseUrl}/h", root = "items"), "search" to search),
        fields =
            mapOf(
                "item.title" to FieldSpec(path = "title"),
                "item.url" to FieldSpec(path = "url"),
            ),
        filters = filters,
    )

    private fun client(
        filters: List<FilterDefinition>,
        http: RecordingHttp,
        search: EndpointSpec = EndpointSpec(url = "{baseUrl}/search?q={queryEncoded}", root = "items"),
    ) = GenericSourceEngine(config(filters, search), http, FakeHeaderStore())

    private fun sort(default: String = "") =
        FilterDefinition(
            id = "sort",
            label = "Sort",
            type = "select",
            options = listOf(FilterOptionSpec("latest", "Latest"), FilterOptionSpec("views", "Views")),
            default = default,
            request = FilterRequestSpec(target = "query", param = "orderby"),
        )

    private fun genres(
        encode: String,
        param: String,
        target: String = "query",
    ) = FilterDefinition(
        id = "genres",
        label = "Genres",
        type = "multiselect",
        options = listOf(FilterOptionSpec("action"), FilterOptionSpec("drama")),
        request = FilterRequestSpec(target = target, param = param, encode = encode),
    )

    @Test
    fun selections_produce_the_exact_expected_query_request() =
        runTest {
            val http = RecordingHttp()
            val result =
                client(listOf(sort(), genres(encode = "repeat", param = "genre[]")), http).search(
                    query = "one piece",
                    page = 1,
                    filters = SourceFilterSelections(mapOf("sort" to listOf("views"), "genres" to listOf("action", "drama"))),
                )
            assertTrue(result is SourceEngineResult.Success)
            assertEquals(
                "https://f.test/search?q=one%20piece&orderby=views&genre%5B%5D=action&genre%5B%5D=drama",
                http.requests.single().url,
            )
        }

    @Test
    fun csv_encoding_joins_values_into_one_parameter() =
        runTest {
            val http = RecordingHttp()
            client(listOf(genres(encode = "csv", param = "genre")), http).search(
                query = "q",
                page = 1,
                filters = SourceFilterSelections(mapOf("genres" to listOf("action", "drama"))),
            )
            assertEquals("https://f.test/search?q=q&genre=action%2Cdrama", http.requests.single().url)
        }

    @Test
    fun defaults_apply_on_a_plain_search_and_a_source_without_filters_is_byte_identical_to_before() =
        runTest {
            val withDefaults = RecordingHttp()
            client(listOf(sort(default = "latest")), withDefaults).search("q", 1)
            assertEquals("https://f.test/search?q=q&orderby=latest", withDefaults.requests.single().url)

            val noFilters = RecordingHttp()
            client(emptyList(), noFilters).search("one piece", 1)
            assertEquals("https://f.test/search?q=one%20piece", noFilters.requests.single().url)
        }

    @Test
    fun empty_optional_selections_are_omitted() =
        runTest {
            val http = RecordingHttp()
            client(listOf(sort(), genres(encode = "csv", param = "genre")), http).search("q", 1)
            assertEquals("https://f.test/search?q=q", http.requests.single().url)
        }

    @Test
    fun form_target_filters_append_after_the_static_form_body_in_declaration_order() =
        runTest {
            val http = RecordingHttp()
            val search =
                EndpointSpec(
                    url = "{baseUrl}/wp-admin/admin-ajax.php",
                    method = "post-form",
                    root = "items",
                    formBody = mapOf("action" to "madara_load_more", "vars[s]" to "{query}"),
                )
            val filters =
                listOf(
                    sort(default = "latest").copy(request = FilterRequestSpec(target = "form", param = "vars[meta_key]")),
                    genres(encode = "csv", param = "vars[wp-manga-genre]", target = "form"),
                )
            client(filters, http, search).search(
                query = "solo",
                page = 1,
                filters = SourceFilterSelections(mapOf("genres" to listOf("action"))),
            )
            assertEquals(
                listOf(
                    "action" to "madara_load_more",
                    "vars[s]" to "solo",
                    "vars[meta_key]" to "latest",
                    "vars[wp-manga-genre]" to "action",
                ),
                http.requests.single().formBody,
            )
        }

    @Test
    fun repeat_encoding_on_a_form_target_emits_repeated_entries() =
        runTest {
            val http = RecordingHttp()
            val search =
                EndpointSpec(url = "{baseUrl}/ajax", method = "post-form", root = "items")
            client(listOf(genres(encode = "repeat", param = "genre[]", target = "form")), http, search).search(
                query = "q",
                page = 1,
                filters = SourceFilterSelections(mapOf("genres" to listOf("action", "drama"))),
            )
            assertEquals(
                listOf("genre[]" to "action", "genre[]" to "drama"),
                http.requests.single().formBody,
            )
        }

    @Test
    fun body_json_filters_fill_the_json_body_template_as_arrays_and_escaped_scalars() =
        runTest {
            val http = RecordingHttp()
            val search =
                EndpointSpec(
                    url = "{baseUrl}/api/search",
                    method = "post-json",
                    root = "items",
                    jsonBody = """{"query":"{queryJson}","genres":{genres_json},"author":"{author_json}"}""",
                )
            val filters =
                listOf(
                    genres(encode = "json-array", param = "genres_json", target = "body-json"),
                    FilterDefinition(
                        id = "author",
                        label = "Author",
                        type = "text",
                        request = FilterRequestSpec(target = "body-json", param = "author_json"),
                    ),
                )
            client(filters, http, search).search(
                query = "one",
                page = 1,
                filters =
                    SourceFilterSelections(
                        mapOf("genres" to listOf("action", "drama"), "author" to listOf("o\"da")),
                    ),
            )
            assertEquals(
                """{"query":"one","genres":["action","drama"],"author":"o\"da"}""",
                http.requests.single().jsonBody,
            )
        }

    @Test
    fun header_target_filters_merge_over_the_request_headers() =
        runTest {
            val http = RecordingHttp()
            val filters =
                listOf(
                    FilterDefinition(
                        id = "language",
                        label = "Language",
                        type = "select",
                        options = listOf(FilterOptionSpec("en"), FilterOptionSpec("ar")),
                        default = "en",
                        request = FilterRequestSpec(target = "header", param = "X-Content-Lang"),
                    ),
                )
            client(filters, http).search("q", 1, SourceFilterSelections(mapOf("language" to listOf("ar"))))
            assertEquals("ar", http.requests.single().headers["X-Content-Lang"])
        }

    @Test
    fun a_required_filter_with_no_value_fails_closed_without_issuing_a_request() =
        runTest {
            val http = RecordingHttp()
            val required = sort().copy(required = true)
            val result = client(listOf(required), http).search("q", 1)
            assertTrue(result is SourceEngineResult.Failure)
            assertEquals(emptyList(), http.requests)
        }

    @Test
    fun home_requests_are_untouched_by_search_only_filters() =
        runTest {
            val http = RecordingHttp()
            client(listOf(sort(default = "latest")), http).home(1)
            assertEquals("https://f.test/h", http.requests.single().url)
        }
}
