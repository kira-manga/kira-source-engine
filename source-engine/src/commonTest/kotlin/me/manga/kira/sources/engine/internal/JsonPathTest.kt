package me.manga.kira.sources.engine.internal

import kotlinx.serialization.json.Json
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class JsonPathTest {
    private val json = Json
    private fun parse(s: String) = json.parseToJsonElement(s)

    @Test
    fun root_path_returns_whole_node() {
        val node = parse("\"hello\"")
        assertEquals("hello", JsonPath.string(node, ""))
        assertEquals("hello", JsonPath.string(node, "$"))
    }

    @Test
    fun nested_object_keys() {
        val node = parse("""{"a":{"b":{"c":"deep"}}}""")
        assertEquals("deep", JsonPath.string(node, "a.b.c"))
        assertEquals("deep", JsonPath.string(node, "$.a.b.c"))
        assertNull(JsonPath.string(node, "a.x.c"))
    }

    @Test
    fun array_index_and_wildcard() {
        val node = parse("""{"items":[{"t":"one"},{"t":"two"},{"t":"three"}]}""")
        assertEquals("two", JsonPath.string(node, "items[1].t"))
        assertEquals(listOf("one", "two", "three"), JsonPath.stringList(node, "items[*].t"))
    }

    @Test
    fun resolve_array_root_returns_array_node() {
        val node = parse("""{"data":{"list":[1,2,3]}}""")
        // resolve to the array node (used as a list root before fan-out)
        assertEquals(1, JsonPath.resolve(node, "data.list").size)
        // wildcard fans out the elements
        assertEquals(listOf("1", "2", "3"), JsonPath.stringList(node, "data.list[*]"))
    }

    @Test
    fun numbers_and_booleans_stringify() {
        val node = parse("""{"n":42,"b":true}""")
        assertEquals("42", JsonPath.string(node, "n"))
        assertEquals("true", JsonPath.string(node, "b"))
    }

    @Test
    fun missing_yields_null_not_crash() {
        val node = parse("""{"a":1}""")
        assertNull(JsonPath.string(node, "z.y.x"))
        assertEquals(emptyList(), JsonPath.stringList(node, "a[*]")) // a is not an array
    }
}
