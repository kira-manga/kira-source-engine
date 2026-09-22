package me.manga.kira.sources.testkit

import kotlinx.coroutines.test.runTest
import me.manga.kira.source.contracts.SourceChapter
import me.manga.kira.source.contracts.SourceConfigParser
import me.manga.kira.source.contracts.SourceEngineError
import me.manga.kira.source.contracts.SourceEngineResult
import me.manga.kira.source.contracts.SourceHttpMethod
import me.manga.kira.source.contracts.SourceMangaRef
import me.manga.kira.source.contracts.SourceRequest
import me.manga.kira.source.contracts.SourceResponse
import me.manga.kira.source.contracts.SourceTransport
import me.manga.kira.source.contracts.model.EndpointSpec
import me.manga.kira.source.contracts.model.FieldSpec
import me.manga.kira.source.contracts.model.PaginationSpec
import me.manga.kira.source.contracts.model.SourceConfig
import me.manga.kira.source.contracts.model.SourceConfigDocument
import me.manga.kira.source.engine.DefaultSourceConfigValidator
import me.manga.kira.source.engine.DefaultStrategyRegistry
import me.manga.kira.source.engine.GenericSourceEngine
import me.manga.kira.source.engine.SourceDeclarationCapabilities
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import kotlin.test.fail

class DeclarationCapabilitiesTest {
    private val capabilities = SourceDeclarationCapabilities()
    private val validator = DefaultSourceConfigValidator(DefaultStrategyRegistry())

    @Test
    fun reusable_corpus_has_exact_structured_findings_and_default_validator_hookup() {
        val fixtures = DeclarationCapabilityFixtures.cases
        assertEquals(fixtures.size, fixtures.map { it.id }.toSet().size, "Fixture IDs must be unique")
        for (fixture in fixtures) {
            val findings = capabilities.validate(fixture.source)
            assertEquals(
                fixture.expectedFindings.toSet(),
                findings.map { ExpectedDeclarationFinding(it.code, it.path) }.toSet(),
                fixture.id,
            )
            assertEquals(findings.size, findings.distinct().size, fixture.id)
            val result = validator.validate(document(fixture.source))
            assertEquals(fixture.expectedFindings.isEmpty(), result.isValid, "${fixture.id}: ${result.errors}")
            for (finding in fixture.expectedFindings) {
                assertTrue(result.errors.any { finding.path in it && "[${finding.code}]" in it }, fixture.id)
            }
        }
    }

    @Test
    fun approved_twelve_source_bundle_remains_valid_without_model_or_consumer_edits() {
        val bundle = SourceConfigParser.parse(APPROVED_BUNDLE_REVISION_6_JSON).valueOrFail()
        assertEquals(6L, bundle.revision)
        assertEquals(listOf("Azora", "Mangamello", "Mangamello Plus", "SwatManga", "Lekmanga", "Team X", "DilarV2", "3asq", "Demonicscans", "Mangabuddy", "Zazamanga", "Tapas"), bundle.sources.map { it.api })
        assertEquals(197, bundle.sources.sumOf { it.fields.size })
        bundle.sources.forEach { assertEquals(emptyList(), capabilities.validate(it), it.api) }
        assertEquals(emptyList(), validator.validate(bundle).errors)
    }

    @Test
    fun capability_failure_rejects_the_whole_document_and_existing_guards_stay_first() {
        val good = DeclarationCapabilityFixtures.seededRequestNames.copy(api = "good")
        val bad = DeclarationCapabilityFixtures.cases.first { it.id == "unknown_url_variable" }.source.copy(api = "bad")
        val result = validator.validate(SourceConfigDocument(1, sources = listOf(good, bad)))
        assertFalse(result.isValid)
        assertTrue(result.errors.single().startsWith("source 'bad': endpoints[home].url:"))
        val unsupportedSchema = validator.validate(SourceConfigDocument(99, sources = listOf(bad)))
        assertEquals(1, unsupportedSchema.errors.size)
        assertTrue(unsupportedSchema.errors.single().startsWith("schemaVersion"))
        val tooMany = validator.validate(SourceConfigDocument(1, sources = List(513) { bad }))
        assertEquals(1, tooMany.errors.size)
        assertTrue(tooMany.errors.single().startsWith("document contains"))
    }

    @Test
    fun findings_do_not_echo_urls_bodies_map_keys_or_parser_messages() {
        val marker = "SYNTHETIC_PRIVATE_MARKER"
        val source = DeclarationCapabilityFixtures.seededRequestNames.copy(
            endpoints = mapOf("home" to EndpointSpec(
                url = "https://private.invalid/$marker?value={unknown}", method = "post-form", format = "html",
                formBody = mapOf("https://private.invalid/$marker" to "body-$marker-{unknown}"), listSelector = "div[$marker",
            ), "search" to EndpointSpec(
                url = "https://example.test/search", method = "post-json", jsonBody = """{"private":"$marker","unknown":"{unknown}"}""",
            )),
            fields = mapOf("item.title" to FieldSpec(template = "fallback", vars = mapOf("https://private.invalid/$marker" to "h3|$marker"))),
        )
        val findings = capabilities.validate(source)
        assertTrue(findings.size >= 6)
        for (finding in findings) {
            assertFalse(marker in finding.toString())
            assertFalse("https://" in finding.toString())
            assertFalse("body-" in finding.toString())
        }
        assertTrue(findings.any { it.path == "endpoints[home].formBody[0].value" })
        assertTrue(findings.any { it.path == "fields[item.title].vars[0]" })
        assertTrue(validator.validate(document(source)).errors.none { marker in it })
    }

    @Test
    fun request_vocabulary_matches_execution_including_known_empty_values() = runTest {
        val source = DeclarationCapabilityFixtures.seededRequestNames
        val transport = FixtureSourceTransport(source.endpoints.keys.associate { verb ->
            FixtureRequestKey(SourceHttpMethod.POST_FORM, "https://example.test/$verb") to SourceResponse(200, """{"items":[]}""")
        })
        val engine = GenericSourceEngine(source, transport, StaticSourceHeaderProvider())
        val manga = manga(source)
        val chapter = SourceChapter("1", "One", "https://example.test/chapter/1", null)
        engine.home(3).valueOrFail()
        engine.featured(2).valueOrFail()
        engine.search("x \"y\"", 4).valueOrFail()
        engine.details(manga).valueOrFail() // Includes one separate chapters request.
        engine.pages(manga, chapter).valueOrFail()
        assertEquals(6, transport.requests.size)
        val expectedPages = listOf("3", "2", "4", "7", "7", "7")
        transport.requests.forEachIndexed { index, request ->
            val isSearch = index == 2
            val hasItem = index >= 3
            assertEquals(mapOf(
                "baseUrl" to "https://example.test", "imageBase" to "https://images.example.test",
                "page" to expectedPages[index], "pageOffset" to (expectedPages[index].toInt() - 1).toString(),
                "query" to if (isSearch) "x \"y\"" else "",
                "queryEncoded" to if (isSearch) "x%20%22y%22" else "",
                "queryJson" to if (isSearch) "x \\\"y\\\"" else "",
                "itemUrl" to if (hasItem) manga.url else "",
                "chapterUrl" to if (index == 5) chapter.url else "",
                "id" to if (hasItem) "42" else "",
            ), request.formBody?.toMap())
        }
    }

    @Test
    fun chapter_counters_use_sent_surfaces_and_next_strings_are_not_followed() = runTest {
        val nextUrl = "https://do-not-follow.invalid/opaque-cursor"
        for (method in listOf("get", "post-form", "POST_FORM", "PostForm", "post-json", "POST_JSON", "PostJson")) {
            for (signal in listOf("numeric", "boolean", "next")) {
                val form = method.lowercase() in setOf("post-form", "post_form", "postform")
                val json = method.lowercase() in setOf("post-json", "post_json", "postjson")
                val chaptersUrl = "https://example.test/chapters"
                val source = DeclarationCapabilityFixtures.seededRequestNames.copy(
                    pagination = PaginationSpec(param = "unused", start = 9),
                    endpoints = mapOf(
                        "home" to EndpointSpec("{baseUrl}/home", root = "items"),
                        "details" to EndpointSpec("{baseUrl}/details"),
                        "chapters" to EndpointSpec(
                            url = chaptersUrl + if (!form && !json) "?wire={chapterPage}" else "", method = method,
                            root = "chapters", pageParam = "chapterPage", lastPageLocator = "termination",
                            formBody = if (form) mapOf("wire" to "{chapterPage}") else emptyMap(),
                            jsonBody = if (json) """{"wire":{chapterPage}}""" else "",
                        ),
                    ),
                    fields = mapOf("chapter.url" to FieldSpec(path = "url")),
                )
                assertEquals(emptyList(), validator.validate(document(source)).errors)
                val requests = mutableListOf<SourceRequest>()
                val transport = SourceTransport { request ->
                    requests += request
                    if (requests.size == 1) {
                        SourceResponse(200, "{}")
                    } else {
                        val page = requests.size - 1
                        val termination = when (signal) {
                            "numeric" -> "2"
                            "boolean" -> if (page == 1) "true" else "false"
                            else -> if (page == 1) "\"$nextUrl\"" else "null"
                        }
                        SourceResponse(200, """{"chapters":[{"url":"/chapter/$page"}],"termination":$termination}""")
                    }
                }
                val result = GenericSourceEngine(source, transport, StaticSourceHeaderProvider()).details(manga(source)).valueOrFail()
                assertEquals(listOf("https://example.test/chapter/1", "https://example.test/chapter/2"), result.chapters.map { it.url }, "$method/$signal")
                assertEquals(3, requests.size, "$method/$signal")
                requests.drop(1).forEachIndexed { index, request ->
                    val page = index + 1
                    assertEquals(chaptersUrl + if (!form && !json) "?wire=$page" else "", request.url)
                    assertEquals(if (form) listOf("wire" to page.toString()) else null, request.formBody)
                    assertEquals(if (json) """{"wire":$page}""" else null, request.jsonBody)
                }
                assertTrue(requests.none { it.url == nextUrl })
            }
        }
    }

    @Test
    fun field_root_vars_coalescing_and_empty_dir_keep_existing_semantics() = runTest {
        val base = DeclarationCapabilityFixtures.cases.first { it.id == "page_root_and_dir_without_root_dirs" }.source
        val body = """{"storage_key":"SK","storage,key":"LITERAL","webp_pages":[],"pages":[{"fallback":"a.jpg"},{"url":"b.jpg"}]}"""
        for (dirs in listOf(emptyList(), listOf("webp", "raw"))) {
            val source = base.copy(endpoints = base.endpoints + ("pages" to base.endpoints.getValue("pages").copy(rootDirs = dirs)))
            assertEquals(emptyList(), validator.validate(document(source)).errors)
            val pages = GenericSourceEngine(source, SourceTransport { SourceResponse(200, body) }, StaticSourceHeaderProvider())
                .pages(manga(source), SourceChapter("1", "One", "https://example.test/chapter/1", null)).valueOrFail()
            val dir = dirs.lastOrNull().orEmpty()
            assertEquals(listOf("https://images.example.test/SK/$dir/a.jpg", "https://images.example.test/SK/$dir/b.jpg"), pages.map { it.url })
        }
        val literalKey = DeclarationCapabilityFixtures.cases.first { it.id == "root_var_comma_is_a_literal_json_key_not_coalescing" }.source
        val pages = GenericSourceEngine(literalKey, SourceTransport { SourceResponse(200, body) }, StaticSourceHeaderProvider())
            .pages(manga(literalKey), SourceChapter("1", "One", "https://example.test/chapter/1", null)).valueOrFail()
        assertEquals(listOf("https://images.example.test/LITERAL", "https://images.example.test/LITERAL"), pages.map { it.url })
    }

    @Test
    fun conditional_filter_acceptance_is_explicitly_not_a_dynamic_binding_guarantee() = runTest {
        val source = DeclarationCapabilityFixtures.conditionalPlaceholder
        assertEquals(emptyList(), capabilities.validate(source))
        val requests = mutableListOf<SourceRequest>()
        val transport = SourceTransport { request ->
            requests += request
            SourceResponse(200, "[]")
        }
        val result = GenericSourceEngine(source, transport, StaticSourceHeaderProvider()).search("", 1)
        // Still pins an OPEN declaration-binding limitation: a hidden filter contributes no var.
        // The runtime's destination guard separately refuses the unresolved literal URI before
        // this fake transport can see it; that refusal is not dynamic-binding validation.
        assertEquals(SourceEngineResult.Failure(SourceEngineError.InvalidResponse), result)
        assertEquals(emptyList(), requests)
    }

    private fun document(source: SourceConfig) = SourceConfigDocument(1, sources = listOf(source))

    private fun manga(source: SourceConfig) = SourceMangaRef(source.api, source.language, "Book", "https://example.test/book/42/", "")

    private fun <T> SourceEngineResult<T>.valueOrFail(): T = when (this) {
        is SourceEngineResult.Success -> value
        is SourceEngineResult.Failure -> fail("Expected fixture success, got $error")
    }
}
