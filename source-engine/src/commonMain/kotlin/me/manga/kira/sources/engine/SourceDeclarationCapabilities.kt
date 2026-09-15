package me.manga.kira.source.engine

import me.manga.kira.source.contracts.StrategyRegistry
import me.manga.kira.source.contracts.model.EndpointSpec
import me.manga.kira.source.contracts.model.FieldSpec
import me.manga.kira.source.contracts.model.SourceConfig
import me.manga.kira.source.engine.internal.Extractor
import me.manga.kira.source.engine.internal.JsonPath
import me.manga.kira.source.engine.internal.Templates

/**
 * A declaration the generic executor cannot consume as authored. [path] is source-relative; map
 * entries with author-controlled names (form values and field vars) use their declaration index.
 * [code] is a stable machine-readable category. [message] never includes submitted values or parser
 * messages, so adapters need not parse or forward exception text to locate a finding.
 */
data class SourceDeclarationFinding(
    val code: String,
    val path: String,
    val message: String,
)

/**
 * The generic executor's declaration-capability boundary, reusable by consumer validation adapters.
 * Run the caller's schema, size/complexity, strategy, URL, and filter-definition checks as well; this
 * is not a replacement for them. [DefaultSourceConfigValidator] applies its guards before this check.
 * Non-generic descriptors are outside this check's execution contract.
 *
 * Only consumed locators/templates are inspected, using each endpoint's effective format and field
 * overrides. A valid declaration does NOT prove a response match or a nonempty value. In particular,
 * request built-ins can be seeded empty, and a declared conditional filter can be hidden at runtime
 * and then contribute no variable. This namespace check does not change that filter behavior.
 *
 * No requests, provider bodies, scripts, or new extraction capabilities are involved. JSON paths and
 * template tokens share the executor's parsers; CSS uses its actual Ksoup selector parser. Context
 * routing mirrors GenericSourceEngine's request/mapping helpers and is pinned by reusable fixtures.
 */
class SourceDeclarationCapabilities(
    private val strategies: StrategyRegistry = DefaultStrategyRegistry(),
) {
    fun validate(source: SourceConfig): List<SourceDeclarationFinding> =
        if (source.engine != "generic") emptyList() else Inspection(source, strategies).validate()

    private class Inspection(
        private val source: SourceConfig,
        private val strategies: StrategyRegistry,
    ) {
        private val findings = linkedSetOf<SourceDeclarationFinding>()

        fun validate(): List<SourceDeclarationFinding> {
            source.endpoints.entries.forEachIndexed { index, (verb, endpoint) ->
                if (verb !in ENDPOINT_VERBS) pagination(verb, endpoint, "endpoints[$index]", emptyList())
            }
            // These names, unlike arbitrary map keys/values, are safe to include in diagnostic paths.
            for (verb in ENDPOINT_VERBS) {
                val endpoint = source.endpoints[verb] ?: continue
                val path = "endpoints[$verb]"
                val templates = requestTemplates(endpoint, path)
                val variables = Templates.REQUEST_VARIABLES.toMutableSet()
                if (verb in LIST_VERBS) {
                    source.filters.filter { verb in it.appliesTo }.forEach { filter ->
                        if (filter.request.target in PLACEHOLDER_TARGETS) variables += filter.request.param
                    }
                }
                if (verb == "chapters" && Templates.isVariableName(endpoint.pageParam)) {
                    variables += endpoint.pageParam
                }
                templates.forEach { (fieldPath, value) -> template(value, variables, fieldPath) }
                pagination(verb, endpoint, path, templates.map { it.second })
                endpointLocators(verb, endpoint, path)
                fields(verb, endpoint)
            }
            return findings.toList()
        }

        private fun requestTemplates(endpoint: EndpointSpec, path: String): List<Pair<String, String>> = buildList {
            add("$path.url" to endpoint.url)
            // Match methodOf/runRequest: keys, headers, and inactive bodies are not interpolated.
            when (endpoint.method.lowercase()) {
                "post-form", "post_form", "postform" -> endpoint.formBody.values.forEachIndexed { index, value ->
                    add("$path.formBody[$index].value" to value)
                }
                "post-json", "post_json", "postjson" -> {
                    if (endpoint.jsonBody.isNotEmpty()) add("$path.jsonBody" to endpoint.jsonBody)
                }
                else -> Unit
            }
        }

        private fun template(value: String, variables: Set<String>, path: String) {
            if (Templates.references(value).any { it !in variables }) {
                finding("template.variable.unsupported", path, "Template references a variable not supplied in this context.")
            }
            // This tempting spelling appears in old model comments but is NOT an interpolation token.
            // Do not reject arbitrary braces: JSON objects and ordinary literal text must survive.
            if (DIRECT_ROOT_TOKEN.containsMatchIn(value)) {
                finding("template.root.unsupported", path, "Direct root tokens are not interpolation syntax; response-root locators belong in page.image vars.")
            }
        }

        private fun pagination(verb: String, endpoint: EndpointSpec, path: String, templates: List<String>) {
            val counter = endpoint.pageParam
            val locator = endpoint.lastPageLocator
            if (verb != "chapters") {
                if (counter.isNotEmpty()) {
                    finding("pagination.context.unsupported", "$path.pageParam", "Automatic pagination is supported only on the chapters endpoint.")
                }
                if (locator.isNotEmpty()) {
                    finding("pagination.context.unsupported", "$path.lastPageLocator", "Automatic pagination is supported only on the chapters endpoint.")
                }
                return
            }
            if (counter.isEmpty()) {
                if (locator.isNotEmpty()) {
                    finding("pagination.counter.required", "$path.pageParam", "A chapter termination locator requires a counter variable.")
                }
            } else {
                if (!Templates.isVariableName(counter)) {
                    finding("pagination.counter.name", "$path.pageParam", "The chapter counter must be a template variable name.")
                } else if (templates.none { counter in Templates.references(it) }) {
                    finding("pagination.counter.unconsumed", "$path.pageParam", "The chapter counter must occur in the URL, an active form value, or the active JSON body.")
                }
                if (locator.isBlank()) {
                    finding("pagination.termination.required", "$path.lastPageLocator", "A chapter counter requires a nonblank termination locator.")
                }
                if (endpoint.format == "script-json") {
                    finding("pagination.script_json.unsupported", "$path.lastPageLocator", "Chapter pagination cannot read a termination locator from a script-json island.")
                }
            }
            if (locator.isNotBlank()) rawLocator(locator, Extractor.isHtml(endpoint), "$path.lastPageLocator")
            // Locator values may be numeric, boolean, or nonempty next strings. The latter are only
            // has-next signals: the executor increments the named counter and never follows a cursor.
        }

        private fun endpointLocators(verb: String, endpoint: EndpointSpec, path: String) {
            val html = Extractor.isHtml(endpoint)
            if (endpoint.format == "script-json") {
                css("script#${endpoint.scriptId.ifEmpty { "__NEXT_DATA__" }}", "$path.scriptId")
            }
            // Details uses the whole response; its root/listSelector is not a detail-field scope.
            if (verb != "details") {
                if (html) css(endpoint.listSelector, "$path.listSelector", allowBlank = true)
                else jsonCoalesce(endpoint.root, "$path.root")
            }
            if (verb in LIST_VERBS) {
                endpoint.listFilters.forEachIndexed { index, filter ->
                    rawLocator(filter.path, html, "$path.listFilters[$index].path")
                }
            }
            if (verb == "details" && endpoint.format == "script-json" && "detail.chapters" in source.fields) {
                finding("field.script_json.inline_chapters", "fields[detail.chapters]", "Inline chapters cannot be extracted from a script-json details island.")
            }
        }

        private fun fields(verb: String, endpoint: EndpointSpec) {
            val html = Extractor.isHtml(endpoint)
            when (verb) {
                "home", "featured", "search" -> {
                    fun key(base: String): String = if ("$verb.$base" in source.fields) "$verb.$base" else base
                    for (base in listOf("item.title", "item.url", "item.cover")) field(key(base), html)
                    if (verb != "featured") {
                        field(key("item.rating"), html)
                        field(key("item.genres"), html, FieldMode.LIST)
                        val recent = key("item.recentChapters")
                        if (recent in source.fields) {
                            field(recent, html, FieldMode.LIST)
                            field("chapter.number", html)
                            field("chapter.url", html)
                            field("chapter.locked", html)
                        }
                    } else if (source.blacklistGenres.isNotEmpty()) {
                        field(key("item.genres"), html, FieldMode.LIST)
                    }
                }
                "details" -> {
                    for (name in listOf("title", "cover", "description", "author", "rating", "status")) {
                        field("detail.$name", html)
                    }
                    field("detail.genres", html, FieldMode.LIST)
                    if ("detail.chapters" in source.fields) {
                        field("detail.chapters", html, FieldMode.INLINE_LIST)
                        chapterFields(html)
                    }
                }
                "chapters" -> chapterFields(html)
                "pages" -> {
                    field("page.order", html)
                    field("page.image", html, rootAvailable = true)
                }
            }
        }

        private fun chapterFields(html: Boolean) {
            for (name in listOf("number", "name", "url", "locked")) field("chapter.$name", html)
            field("chapter.date", html, FieldMode.DATE)
        }

        private fun field(key: String, html: Boolean, mode: FieldMode = FieldMode.SCALAR, rootAvailable: Boolean = false) {
            val spec = source.fields[key] ?: return
            val path = "fields[$key]"
            when (mode) {
                FieldMode.SCALAR, FieldMode.DATE -> {
                    if (html) {
                        css(spec.selector, "$path.selector", allowEmpty = true)
                        spec.fallbackSelectors.forEachIndexed { index, selector -> css(selector, "$path.fallbackSelectors[$index]") }
                    } else {
                        json(spec.path, "$path.path")
                        if (spec.fallbackPath.isNotEmpty()) json(spec.fallbackPath, "$path.fallbackPath")
                    }
                }
                FieldMode.LIST, FieldMode.INLINE_LIST -> {
                    if (html) {
                        val property = if (spec.listSelector.isNotEmpty()) "listSelector" else "selector"
                        css(spec.listSelector.ifEmpty { spec.selector }, "$path.$property", allowEmpty = true, allowBlank = mode == FieldMode.INLINE_LIST)
                    } else {
                        val property = if (spec.listPath.isNotEmpty()) "listPath" else "path"
                        val locator = spec.listPath.ifEmpty { spec.path }
                        if (mode == FieldMode.INLINE_LIST) jsonCoalesce(locator, "$path.$property")
                        else json(locator, "$path.$property")
                    }
                }
            }
            if (mode != FieldMode.SCALAR) {
                if (spec.template.isNotEmpty()) {
                    finding("field.template.unsupported", "$path.template", "This field consumer does not expand templates.")
                }
                if (spec.vars.isNotEmpty()) {
                    finding("field.vars.unsupported", "$path.vars", "This field consumer does not resolve template variables.")
                }
                return
            }
            if (spec.template.isEmpty()) return // Vars are resolved only when the scalar template runs.
            template(spec.template, Templates.FIELD_VARIABLES + spec.vars.keys, "$path.template")
            fieldVariables(spec, html, path, rootAvailable)
        }

        private fun fieldVariables(spec: FieldSpec, html: Boolean, path: String, rootAvailable: Boolean) {
            spec.vars.entries.forEachIndexed { index, (name, expression) ->
                val varPath = "$path.vars[$index]"
                if (!Templates.isVariableName(name)) {
                    finding("field.variable.name", varPath, "A field variable key must be a template variable name.")
                }
                // Same argumentless pipe and item-only comma splitting as fieldVars. Root locators
                // deliberately do not take the comma-coalescing branch (CSS grouping still works).
                val parts = expression.split('|')
                val locator = parts.first().trim()
                parts.drop(1).forEach { transformName ->
                    if (!strategies.hasTransform(transformName.trim())) {
                        finding("field.variable.transform", varPath, "A variable pipe step must name a compiled transform without pipe arguments.")
                    }
                }
                if (locator.startsWith("root:")) {
                    if (locator == "root:__dir") {
                        // This sentinel is defined even when no directory is supplied (including
                        // scalar contexts other than page.image): the executor binds it to empty.
                    } else if (!rootAvailable) {
                        finding("field.variable.root_context", varPath, "Only page.image receives the response-root locator context.")
                    } else {
                        rawLocator(locator.removePrefix("root:").trim(), html, varPath)
                    }
                    // root:__dir may be empty without rootDirs; do not invent a cardinality rule.
                } else {
                    parts.first().split(',').forEach { rawLocator(it.trim(), html, varPath) }
                }
            }
        }

        private fun rawLocator(value: String, html: Boolean, path: String) {
            if (html) css(value, path) else json(value, path)
        }

        private fun jsonCoalesce(value: String, path: String) {
            Extractor.coalescedPaths(value).forEach { json(it, path) }
        }

        private fun json(value: String, path: String) {
            if (!JsonPath.isSupported(value)) {
                finding("locator.json.unsupported", path, "JSON locator must use literal dot keys with at most one nonnegative index or wildcard accessor per segment.")
            }
        }

        private fun css(value: String, path: String, allowEmpty: Boolean = false, allowBlank: Boolean = false) {
            if ((allowEmpty && value.isEmpty()) || (allowBlank && value.isBlank())) return
            if (!Extractor.isSupportedSelector(value)) {
                finding("locator.css.unsupported", path, "Selector is not accepted by the executor's CSS parser.")
            }
        }

        private fun finding(code: String, path: String, message: String) {
            findings += SourceDeclarationFinding(code, path, message)
        }
    }

    private enum class FieldMode { SCALAR, DATE, LIST, INLINE_LIST }

    private companion object {
        val ENDPOINT_VERBS = listOf("home", "featured", "search", "details", "chapters", "pages")
        val LIST_VERBS = setOf("home", "featured", "search")
        val PLACEHOLDER_TARGETS = setOf("path", "body-json")
        val DIRECT_ROOT_TOKEN = Regex("""\{root:[^{}]*\}""")
    }
}
