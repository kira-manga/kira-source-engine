package me.manga.kira.source.engine

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertTrue
import kotlinx.coroutines.test.runTest
import me.manga.kira.source.contracts.*
import me.manga.kira.source.contracts.model.*

/** Grouped provider responses must not silently discard alternate release identities. */
class GenericSourceChapterEntriesTest {
    private val manga = SourceMangaRef("groups", "en", "Fixture", "https://groups.example/series/7", "")
    private val body = """{"title":"Fixture","groups":[
        {"number":"10.5","title":"Middle","lock":false,"releases":[{"id":"101","team":"A"},{"id":"102","team":"B"}]},
        {"number":"11","title":"Newest","lock":false,"releases":[{"id":"103","team":"C"}]},
        {"number":"12","title":"Unavailable","lock":true,"releases":[{}]},
        {"number":"13","title":"No release","lock":false,"releases":[]}
    ]}"""

    private fun source(inline: Boolean): SourceConfig {
        val endpoints = mapOf(
            "home" to EndpointSpec("{baseUrl}/home", format = "json"),
            "details" to EndpointSpec("{itemUrl}", format = "json"),
            "pages" to EndpointSpec("{chapterUrl}", format = "json"),
        ) + if (inline) emptyMap() else mapOf("chapters" to EndpointSpec("{itemUrl}/chapters", format = "json", root = "groups"))
        val fields = mapOf(
            "item.title" to FieldSpec(path = "title"), "item.url" to FieldSpec(path = "url"),
            "detail.title" to FieldSpec(path = "title"), "page.image" to FieldSpec(path = "url"),
            "chapter.entries" to FieldSpec(listPath = "releases"),
            "chapter.number" to FieldSpec(template = "{number}", vars = mapOf("number" to "root:number")),
            "chapter.order" to FieldSpec(template = "{number}", vars = mapOf("number" to "root:number")),
            "chapter.name" to FieldSpec(template = "{title} / {team}", vars = mapOf("title" to "root:title", "team" to "team")),
            "chapter.locked" to FieldSpec(template = "{locked}", vars = mapOf("locked" to "root:lock")),
            "chapter.url" to FieldSpec(template = "{baseUrl}/r/{id}", vars = mapOf("id" to "id")),
        ) + if (inline) mapOf("detail.chapters" to FieldSpec(listPath = "groups")) else emptyMap()
        return SourceConfig("groups", "en", baseUrl = "https://groups.example", engine = "generic",
            usesCapturedHeaders = false, endpoints = endpoints, fields = fields)
    }

    @Test
    fun inline_and_dedicated_groups_keep_all_releases_parent_context_and_stable_global_order() = runTest {
        for (inline in listOf(true, false)) {
            val config = source(inline)
            assertTrue(DefaultSourceConfigValidator(DefaultStrategyRegistry()).validate(SourceConfigDocument(1, sources = listOf(config))).isValid)
            val requests = mutableListOf<String>()
            val client = GenericSourceEngine(config, { requests += it.url; SourceResponse(200, body) }, { error("No captured credentials") })
            val details = assertIs<SourceEngineResult.Success<SourceDetails>>(client.details(manga)).value
            assertEquals(listOf("11", "10.5", "10.5"), details.chapters.map { it.number })
            assertEquals(listOf("Newest / C", "Middle / A", "Middle / B"), details.chapters.map { it.name })
            assertEquals(listOf("103", "101", "102").map { "https://groups.example/r/$it" }, details.chapters.map { it.url })
            assertEquals(if (inline) listOf(manga.url) else listOf(manga.url, "${manga.url}/chapters"), requests)
        }
    }

    @Test
    fun a_missing_alternate_identity_fails_instead_of_returning_a_partial_chapter_list() = runTest {
        for (inline in listOf(true, false)) {
            val broken = body.replace("\"id\":\"102\",", "")
            val client = GenericSourceEngine(source(inline), { SourceResponse(200, broken) }, { emptyMap() })
            assertEquals(SourceEngineResult.Failure(SourceEngineError.Required("var:chapter.url:id")), client.details(manga))
        }
    }

    @Test
    fun the_consumed_group_locator_is_checked_by_declaration_admission() {
        val config = source(false)
        val malformed = config.copy(fields = config.fields + ("chapter.entries" to FieldSpec(listPath = "releases[?(@.paid)]")))
        assertTrue(SourceDeclarationCapabilities().validate(malformed).any { it.path == "fields[chapter.entries].listPath" })
    }

    @Test
    fun an_empty_release_group_does_not_hide_a_required_later_page() = runTest {
        val original = source(false)
        val config = original.copy(endpoints = original.endpoints + ("chapters" to EndpointSpec(
            "{itemUrl}/chapters?page={page}", format = "json", root = "groups", pageParam = "page", lastPageLocator = "hasNext",
        )))
        val first = """{"groups":[{"number":"1","lock":false,"releases":[{"id":"101"}]}],"hasNext":true}"""
        val last = """{"groups":[{"number":"3","lock":false,"releases":[{"id":"103"}]}],"hasNext":false}"""
        val middlePages = listOf(
            """{"groups":[{"number":"2","lock":false,"releases":[]}],"hasNext":true}""",
            """{"groups":[{"number":"2","lock":true,"releases":[{}]}],"hasNext":true}""",
            """{"groups":[],"hasNext":true}""",
        )
        for ((index, middle) in middlePages.withIndex()) {
            val sent = mutableListOf<String>()
            val client = GenericSourceEngine(config, { request ->
                sent += request.url
                SourceResponse(200, when (request.url) {
                    manga.url -> body
                    "${manga.url}/chapters?page=1" -> first
                    "${manga.url}/chapters?page=2" -> middle
                    "${manga.url}/chapters?page=3" -> last
                    else -> error("Unexpected chapter page")
                })
            }, { emptyMap() })
            val result = assertIs<SourceEngineResult.Success<SourceDetails>>(client.details(manga)).value
            assertEquals(if (index == 2) listOf("1") else listOf("3", "1"), result.chapters.map { it.number })
            assertEquals(if (index == 2) 3 else 4, sent.size)
        }
    }
}
