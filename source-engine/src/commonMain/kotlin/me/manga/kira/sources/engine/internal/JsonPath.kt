package me.manga.kira.sources.engine.internal

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * A deliberately small JSON path resolver — NOT a full JSONPath implementation. It supports the
 * subset the config model needs and nothing more, so behavior is predictable and fully testable:
 *
 *  - `$` or empty       → the root node
 *  - `a.b.c`            → nested object keys (leading `$.` optional)
 *  - `a[0]`             → array element by index
 *  - `a[*]`             → every element of an array (fan-out)
 *
 * Anything else (filters, recursive descent `..`, slices) is intentionally unsupported; the
 * validator should reject configs that need more than this until the engine grows real support.
 * Stage-0 leans on this only for golden-fixture JSON sources.
 */
internal object JsonPath {

    /**
     * Resolve [path] against [root], returning every matching node. A scalar path yields 0..1 nodes;
     * a `[*]` segment fans out. Used both for list roots and for scalar field extraction (take first).
     */
    fun resolve(root: JsonElement, path: String): List<JsonElement> {
        val clean = path.trim().removePrefix("$").removePrefix(".")
        if (clean.isEmpty()) return listOf(root)

        var current: List<JsonElement> = listOf(root)
        for (segment in clean.split('.')) {
            if (segment.isEmpty()) continue
            val (name, accessor) = parseSegment(segment)
            val next = ArrayList<JsonElement>()
            for (node in current) {
                val child = if (name.isEmpty()) node else (node as? JsonObject)?.get(name) ?: continue
                when (accessor) {
                    Accessor.None -> next.add(child)
                    Accessor.All -> (child as? JsonArray)?.let { next.addAll(it) }
                    is Accessor.Index -> (child as? JsonArray)?.getOrNull(accessor.index)?.let(next::add)
                }
            }
            current = next
            if (current.isEmpty()) break
        }
        return current
    }

    /** First node matching [path], as a string, or null. Arrays/objects yield null (not stringified). */
    fun string(root: JsonElement, path: String): String? =
        resolve(root, path).firstOrNull()?.asStringOrNull()

    /** Flatten the array at [path] into a list of strings (drops nulls/non-scalars). */
    fun stringList(root: JsonElement, path: String): List<String> =
        resolve(root, path).mapNotNull { it.asStringOrNull() }

    private fun parseSegment(segment: String): Pair<String, Accessor> {
        val bracket = segment.indexOf('[')
        if (bracket < 0) return segment to Accessor.None
        val name = segment.substring(0, bracket)
        val inside = segment.substring(bracket + 1, segment.indexOf(']', bracket).let { if (it < 0) segment.length else it })
        val accessor = when {
            inside == "*" -> Accessor.All
            else -> inside.toIntOrNull()?.let { Accessor.Index(it) } ?: Accessor.None
        }
        return name to accessor
    }

    private sealed interface Accessor {
        data object None : Accessor
        data object All : Accessor
        data class Index(val index: Int) : Accessor
    }
}

/** Scalar string view of a JSON node: strings/numbers/booleans → content, null/array/object → null. */
internal fun JsonElement.asStringOrNull(): String? = when (this) {
    is JsonNull -> null
    is JsonPrimitive -> content
    else -> null
}
