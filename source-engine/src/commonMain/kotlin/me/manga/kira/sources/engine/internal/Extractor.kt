package me.manga.kira.sources.engine.internal

import com.fleeksoft.ksoup.Ksoup
import com.fleeksoft.ksoup.nodes.Document
import com.fleeksoft.ksoup.nodes.Element
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import me.manga.kira.sources.contracts.model.EndpointSpec
import me.manga.kira.sources.contracts.model.FieldSpec

/**
 * A single matched item (a JSON node or an HTML element) plus the rules for pulling string fields out
 * of it. Hides the JSON-vs-HTML distinction so [me.manga.kira.sources.engine.GenericSourceClient]
 * maps to domain models with one code path.
 */
internal interface ItemScope {
    /** The primary value for [spec] (with fallbacks/lazy-attr chain applied), or null if absent. */
    fun field(spec: FieldSpec): String?

    /** Every value matched by [spec]'s list locator (e.g. a genre chip list). */
    fun fieldList(spec: FieldSpec): List<String>

    /** Resolve a single raw value by locator (JSON path or CSS selector) — used for template vars. */
    fun raw(locator: String): String?

    /** Sub-scopes for a nested list within this item (e.g. a home item's `recentChapters`). */
    fun children(spec: FieldSpec): List<ItemScope>
}

/** A JSON node scope. Field locators are [FieldSpec.path]; list locators are [FieldSpec.listPath]. */
internal class JsonItemScope(private val node: kotlinx.serialization.json.JsonElement) : ItemScope {
    override fun field(spec: FieldSpec): String? {
        val primary = JsonPath.string(node, spec.path)
        if (!primary.isNullOrEmpty()) return primary
        if (spec.fallbackPath.isNotEmpty()) {
            JsonPath.string(node, spec.fallbackPath)?.takeIf { it.isNotEmpty() }?.let { return it }
        }
        return primary
    }

    override fun fieldList(spec: FieldSpec): List<String> =
        JsonPath.stringList(node, spec.listPath.ifEmpty { spec.path })

    override fun raw(locator: String): String? = JsonPath.string(node, locator)

    override fun children(spec: FieldSpec): List<ItemScope> =
        JsonPath.resolve(node, spec.listPath.ifEmpty { spec.path })
            .flatMap { if (it is JsonArray) it.toList() else listOf(it) }
            .map { JsonItemScope(it) }
}

/** An HTML element scope. Field locators are [FieldSpec.selector]; list locators are [FieldSpec.listSelector]. */
internal class HtmlItemScope(private val element: Element) : ItemScope {
    override fun field(spec: FieldSpec): String? {
        val target = if (spec.selector.isEmpty()) element else element.selectFirst(spec.selector)
        val primary = attrOf(target, spec.attr)
        if (!primary.isNullOrEmpty()) return primary
        for (sel in spec.fallbackSelectors) {
            val el = element.selectFirst(sel) ?: continue
            attrOf(el, spec.attr)?.takeIf { it.isNotEmpty() }?.let { return it }
        }
        for (attr in spec.lazyAttrChain) {
            attrOf(target, attr)?.takeIf { it.isNotEmpty() }?.let { return it }
        }
        return primary
    }

    override fun fieldList(spec: FieldSpec): List<String> {
        val selector = spec.listSelector.ifEmpty { spec.selector }
        if (selector.isEmpty()) return emptyList()
        return element.select(selector).mapNotNull { attrOf(it, spec.attr) }.filter { it.isNotEmpty() }
    }

    override fun raw(locator: String): String? = element.selectFirst(locator)?.text()

    override fun children(spec: FieldSpec): List<ItemScope> {
        val selector = spec.listSelector.ifEmpty { spec.selector }
        if (selector.isEmpty()) return emptyList()
        return element.select(selector).map { HtmlItemScope(it) }
    }

    private fun attrOf(el: Element?, attr: String): String? = when (attr) {
        "", "text" -> el?.text()
        "html" -> el?.html()
        "ownText" -> el?.ownText()
        else -> el?.attr(attr) // Ksoup resolves "abs:src"/"abs:href" against the parse base URL
    }
}

/** Turns raw response bodies into [ItemScope]s for the generic client. */
internal object Extractor {
    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
    }

    fun isHtml(endpoint: EndpointSpec): Boolean = when (endpoint.format) {
        "html" -> true
        // script-json is JSON-after-extraction: the response is HTML, but the data lives in a <script>
        // JSON island (e.g. Next.js __NEXT_DATA__), so it is parsed as JSON, not scraped via CSS.
        "json", "script-json" -> false
        else -> endpoint.listSelector.isNotEmpty()
    }

    /** Item scopes for a list endpoint (home/popular/search) or a page-image list. */
    fun listScopes(body: String, baseUrl: String, endpoint: EndpointSpec): List<ItemScope> =
        parse(body, baseUrl, endpoint).listScopes()

    /** The whole response as one scope, for extracting scalar detail fields. */
    fun rootScope(body: String, baseUrl: String, endpoint: EndpointSpec): ItemScope =
        parse(body, baseUrl, endpoint).rootScope()

    /**
     * Parse [body] ONCE into the right backing (HTML document or JSON element) and return a reusable
     * [ParsedBody]. The hot paths (a chapter's page list + its root scope, or a chapters page's list +
     * its pagination locator) read several scopes off the same response — sharing one parse avoids
     * re-running Ksoup/Json over large bodies. [listScopes]/[rootScope]/[locatorValues] above route
     * through this so single-shot callers stay one-liners.
     */
    fun parse(body: String, baseUrl: String, endpoint: EndpointSpec): ParsedBody = ParsedBody(body, baseUrl, endpoint)

    /**
     * The JSON text inside a `<script id="…">` island (default `__NEXT_DATA__`) — how client-rendered
     * (Next.js/RSC) sites expose their data when the visible DOM has none. Throws when the island is
     * absent/blank: a missing island is a STRUCTURAL site change (redesign/rename), not ordinary selector
     * rot, so it must surface as a failure rather than degrade to an empty `{}` that would be a
     * wrong-but-success result.
     */
    private fun scriptJson(body: String, scriptId: String): String {
        val id = scriptId.ifEmpty { "__NEXT_DATA__" }
        val el = Ksoup.parse(body).selectFirst("script#$id")
        return el?.data()?.takeIf { it.isNotBlank() }
            ?: el?.html()?.takeIf { it.isNotBlank() }
            ?: throw IllegalStateException("script island #$id not found")
    }

    /**
     * All scalar values a [locator] matches on a response — used for chapter-list pagination: an HTML
     * pagination widget's page-number texts, or a JSON `has_next`/`total` flag. HTML → matched elements'
     * text; JSON → the path's value(s) as strings.
     */
    fun locatorValues(body: String, baseUrl: String, endpoint: EndpointSpec, locator: String): List<String> =
        parse(body, baseUrl, endpoint).locatorValues(locator)

    /** A sub-list within a response (e.g. the chapter list embedded in a details page). */
    fun subScopes(body: String, baseUrl: String, html: Boolean, locator: FieldSpec): List<ItemScope> =
        if (html) htmlScopes(Ksoup.parse(body, baseUrl), locator.listSelector.ifEmpty { locator.selector })
        else jsonScopes(json.parseToJsonElement(body), locator.listPath.ifEmpty { locator.path })

    private fun htmlScopes(doc: Element, selector: String): List<ItemScope> {
        if (selector.isBlank()) return emptyList()
        return doc.select(selector).map { HtmlItemScope(it) }
    }

    private fun jsonScopes(doc: JsonElement, rootPath: String): List<ItemScope> {
        // The root may be a COMMA-separated list of candidate paths; the first whose array is non-empty
        // wins (coalesce) — e.g. DilarV2 page images live in `webp_pages` when present, else `pages`.
        val candidates = rootPath.split(',').map { it.trim() }.filter { it.isNotEmpty() }.ifEmpty { listOf(rootPath) }
        for (candidate in candidates) {
            val scopes = JsonPath.resolve(doc, candidate)
                .flatMap { if (it is JsonArray) it.toList() else listOf(it) }
                .map { JsonItemScope(it) }
            if (scopes.isNotEmpty()) return scopes
        }
        return emptyList()
    }

    /**
     * One response body parsed a single time. Whichever backing the [endpoint] format implies (an HTML
     * [Document], a JSON island for `script-json`, or the body-as-JSON) is built once in [init] and then
     * reused by [listScopes]/[rootScope]/[locatorValues] — so a hot path that needs more than one of them
     * (page list + root, or chapter list + pagination locator) never re-parses the body.
     */
    class ParsedBody(body: String, baseUrl: String, private val endpoint: EndpointSpec) {
        private val html: Boolean = isHtml(endpoint)
        private val htmlDoc: Document? = if (html) Ksoup.parse(body, baseUrl) else null
        // For script-json the JSON scopes/root come from the <script> island; for plain json from the body.
        private val jsonDoc: JsonElement? = when {
            html -> null
            endpoint.format == "script-json" -> json.parseToJsonElement(scriptJson(body, endpoint.scriptId))
            else -> json.parseToJsonElement(body)
        }
        // locatorValues on a non-html endpoint historically read the RAW body as JSON. For plain `json`
        // that is the same node as [jsonDoc]; only `script-json` differs (its [jsonDoc] is the island, not
        // the body), so parse the raw body lazily and only when actually needed there.
        private val rawJsonDoc: Lazy<JsonElement> =
            if (endpoint.format == "script-json") lazy { json.parseToJsonElement(body) } else lazy { jsonDoc!! }

        fun listScopes(): List<ItemScope> =
            if (html) htmlScopes(htmlDoc!!, endpoint.listSelector) else jsonScopes(jsonDoc!!, endpoint.root)

        fun rootScope(): ItemScope =
            if (html) HtmlItemScope(htmlDoc!!) else JsonItemScope(jsonDoc!!)

        fun locatorValues(locator: String): List<String> {
            if (locator.isBlank()) return emptyList()
            return if (html) {
                htmlDoc!!.select(locator).map { it.text() }.filter { it.isNotBlank() }
            } else {
                val doc = rawJsonDoc.value
                JsonPath.stringList(doc, locator).ifEmpty {
                    JsonPath.string(doc, locator)?.let { listOf(it) } ?: emptyList()
                }
            }
        }
    }
}
