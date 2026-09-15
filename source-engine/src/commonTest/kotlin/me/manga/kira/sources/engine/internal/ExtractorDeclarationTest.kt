package me.manga.kira.source.engine.internal

import me.manga.kira.source.contracts.model.EndpointSpec
import me.manga.kira.source.contracts.model.FieldSpec
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class ExtractorDeclarationTest {
    @Test
    fun selectors_are_checked_by_the_actual_executor_parser() {
        for (selector in listOf("div:has(a[href]):not(.locked)", "h3:contains(الفصل)", "a[href^='/read/'] + span", "p:not(:has(i))", "img:nth-child(2)")) {
            assertTrue(Extractor.isSupportedSelector(selector), selector)
        }
        for (selector in listOf("div[", "", " ")) assertFalse(Extractor.isSupportedSelector(selector), selector)
    }

    @Test
    fun empty_scalar_selector_is_current_scope_but_raw_selector_is_not() {
        val scope = Extractor.rootScope("<p>text</p>", "https://example.test", EndpointSpec("unused", format = "html"))
        assertEquals("text", scope.field(FieldSpec()))
        assertFalse(Extractor.isSupportedSelector(""))
    }

    @Test
    fun coalesced_paths_retain_exact_existing_empty_candidate_behavior() {
        assertEquals(listOf("preferred", "pages"), Extractor.coalescedPaths(" ,preferred,, pages, "))
        assertEquals(listOf(""), Extractor.coalescedPaths(""))
        assertEquals(listOf(","), Extractor.coalescedPaths(","))
        assertEquals(listOf("  "), Extractor.coalescedPaths("  "))
    }
}
