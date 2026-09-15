package me.manga.kira.source.engine.internal

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class TemplatesTest {
    @Test
    fun references_use_the_exact_single_pass_expander_tokens() {
        val template = """{"id":"{id}","array":[{"literal":true}],"root":"{root:id}","again":"{id}","unknown":"{other}"}"""
        assertEquals(setOf("id", "other"), Templates.references(template))
        assertEquals(
            """{"id":"{other}","array":[{"literal":true}],"root":"{root:id}","again":"{other}","unknown":"done"}""",
            Templates.expand(template, mapOf("id" to "{other}", "other" to "done")),
        )
        assertEquals("{unknown}", Templates.expand("{unknown}", emptyMap()))
    }

    @Test
    fun request_and_field_seed_names_are_not_the_same_namespace() {
        assertEquals(setOf("baseUrl", "imageBase"), Templates.FIELD_VARIABLES)
        assertEquals(setOf("baseUrl", "imageBase", "page", "pageOffset", "query", "queryEncoded", "queryJson", "itemUrl", "chapterUrl", "id"), Templates.REQUEST_VARIABLES)
    }

    @Test
    fun variable_names_follow_the_expander_not_identifier_or_expression_rules() {
        for (name in listOf("id", "chapterPage", "0", "page_1")) assertTrue(Templates.isVariableName(name), name)
        for (name in listOf("", " id", "page-name", "page.offset", "root:id", "{page}", "page+1")) {
            assertFalse(Templates.isVariableName(name), name)
        }
    }
}
