package me.manga.kira.source.engine.internal

import kotlinx.serialization.json.Json
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

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

    @Test
    fun existing_root_and_prefix_aliases_are_preserved() {
        val primitive = parse("\"root\"")
        for (path in listOf("", " ", "$", ".", "${'$'}.")) {
            assertTrue(JsonPath.isSupported(path), path)
            assertEquals("root", JsonPath.string(primitive, path), path)
        }
        val node = parse("""{"a":"value"}""")
        for (path in listOf("a", ".a", "${'$'}a", "${'$'}.a", "a.", "  ${'$'}.a.  ")) {
            assertTrue(JsonPath.isSupported(path), path)
            assertEquals("value", JsonPath.string(node, path), path)
        }
    }

    @Test
    fun root_arrays_nested_wildcards_and_index_aliases_are_preserved() {
        val node = parse("""[{"data":["one"]},{"data":["two"]}]""")
        assertTrue(JsonPath.isSupported("[*].data[*]"))
        assertEquals(listOf("one", "two"), JsonPath.stringList(node, "[*].data[*]"))
        for (path in listOf("[1].data[0]", "[+1].data[-0]", "[01].data[00]", "[+01].data[+0]")) {
            assertTrue(JsonPath.isSupported(path), path)
            assertEquals("two", JsonPath.string(node, path), path)
        }
    }

    @Test
    fun keys_are_literal_not_ascii_identifiers_or_expressions() {
        val node = parse("""{"書名":{"اسم-المجلد":"yes"},"a,b":"comma","stray]":"bracket","*":"literal"}""")
        for ((path, value) in mapOf("書名.اسم-المجلد" to "yes", "a,b" to "comma", "stray]" to "bracket", "*" to "literal")) {
            assertTrue(JsonPath.isSupported(path), path)
            assertEquals(value, JsonPath.string(node, path), path)
        }
    }

    @Test
    fun unsupported_syntax_is_not_partially_resolved() {
        val node = parse("""{"items":[{"active":true,"id":"one"},{"id":"two"}]}""")
        for (path in listOf(
            "items[?(@.active)]", "items[0:2]", "items[0,1]", "items['id']", "items[-1]",
            "items[2147483648]", "items[0", "items[0]suffix", "items[0][1]", "items..id",
            "${'$'}..items", "items[]", "items[*]ignored", "items[0]]", "items[",
        )) {
            assertFalse(JsonPath.isSupported(path), path)
            assertEquals(emptyList(), JsonPath.resolve(node, path), path)
        }
    }
}
