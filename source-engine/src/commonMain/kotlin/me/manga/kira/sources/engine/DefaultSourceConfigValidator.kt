package me.manga.kira.sources.engine

import me.manga.kira.sources.contracts.SourceConfigValidator
import me.manga.kira.sources.contracts.StrategyRegistry
import me.manga.kira.sources.contracts.ValidationResult
import me.manga.kira.sources.contracts.model.FilterDefinition
import me.manga.kira.sources.contracts.model.IconSpec
import me.manga.kira.sources.contracts.model.SourceConfig
import me.manga.kira.sources.contracts.model.SourceConfigDocument

/**
 * Schema + referential validator. Runs after signature verification, before any source is trusted.
 * Two jobs: (1) the document is structurally sane (supported schema, non-blank keys, URL-shaped
 * base), and (2) every strategy/transform/date/pagination name a `generic` source references is one
 * this build ships (via [StrategyRegistry]). Archived descriptors remain parseable for migration
 * tooling, but the shipping catalog manager independently rejects every non-generic entry.
 *
 * Errors are collected (not fail-fast) and keyed by api so a whole batch can be diagnosed at once.
 */
class DefaultSourceConfigValidator(
    private val strategies: StrategyRegistry,
) : SourceConfigValidator {
    override fun validate(document: SourceConfigDocument): ValidationResult {
        val errors = mutableListOf<String>()

        if (document.schemaVersion != SUPPORTED_SCHEMA_VERSION) {
            errors += "schemaVersion ${document.schemaVersion} is unsupported (this build expects $SUPPORTED_SCHEMA_VERSION)"
            return ValidationResult.failed(errors) // shape may differ entirely; don't probe further
        }

        if (!validateComplexity(document, errors)) {
            return ValidationResult.failed(errors)
        }

        val seenApis = mutableSetOf<String>()
        for (source in document.sources) {
            validateSource(source, seenApis, errors)
        }

        return if (errors.isEmpty()) ValidationResult.OK else ValidationResult.failed(errors)
    }

    /** Reject pathological collection fan-out before detailed validation traverses the object. */
    private fun validateComplexity(
        document: SourceConfigDocument,
        errors: MutableList<String>,
    ): Boolean {
        if (document.sources.size > MAX_SOURCES) {
            errors += "document contains ${document.sources.size} sources; maximum is $MAX_SOURCES"
            return false
        }

        for (source in document.sources) {
            val tag = "source '${source.api}':"
            var complexity =
                source.headers.size.toLong() + source.endpoints.size + source.fields.size +
                    source.blacklistGenres.size + source.previousHosts.size +
                    source.previousImageHosts.size + source.trustedHosts.size + source.filters.size
            var valid = true

            fun bounded(
                name: String,
                size: Int,
                maximum: Int = MAX_COLLECTION_ENTRIES,
            ) {
                if (size > maximum) {
                    errors += "$tag $name contains $size entries; maximum is $maximum"
                    valid = false
                }
            }

            bounded("headers", source.headers.size)
            bounded("endpoints", source.endpoints.size, 16)
            bounded("fields", source.fields.size)
            bounded("blacklistGenres", source.blacklistGenres.size)
            bounded("previousHosts", source.previousHosts.size)
            bounded("previousImageHosts", source.previousImageHosts.size)
            bounded("trustedHosts", source.trustedHosts.size)
            bounded("filters", source.filters.size, MAX_FILTERS)

            source.endpoints.values.take(MAX_COLLECTION_ENTRIES).forEach { endpoint ->
                bounded("endpoints[].rootDirs", endpoint.rootDirs.size)
                bounded("endpoints[].formBody", endpoint.formBody.size)
                bounded("endpoints[].listFilters", endpoint.listFilters.size)
                complexity += endpoint.rootDirs.size + endpoint.formBody.size + endpoint.listFilters.size
            }
            source.fields.values.take(MAX_COLLECTION_ENTRIES).forEach { field ->
                bounded("fields[].fallbackSelectors", field.fallbackSelectors.size)
                bounded("fields[].lazyAttrChain", field.lazyAttrChain.size)
                bounded("fields[].vars", field.vars.size)
                bounded("fields[].transform", field.transform.size)
                complexity += field.fallbackSelectors.size + field.lazyAttrChain.size + field.vars.size + field.transform.size
                field.transform.take(MAX_COLLECTION_ENTRIES).forEach { transform ->
                    bounded("fields[].transform[].args", transform.args.size)
                    bounded("fields[].transform[].list", transform.list.size)
                    complexity += transform.args.size + transform.list.size
                }
            }
            source.filters.take(MAX_FILTERS).forEach { filter ->
                bounded("filters[].options", filter.options.size)
                bounded("filters[].defaults", filter.defaults.size)
                bounded("filters[].visibleWhen", filter.visibleWhen.size)
                bounded("filters[].appliesTo", filter.appliesTo.size, 16)
                complexity += filter.options.size + filter.defaults.size + filter.visibleWhen.size + filter.appliesTo.size
                filter.visibleWhen.take(MAX_COLLECTION_ENTRIES).forEach { condition ->
                    bounded("filters[].visibleWhen[].anyOf", condition.anyOf.size)
                    complexity += condition.anyOf.size
                }
            }
            if (complexity > MAX_SOURCE_COMPLEXITY) {
                errors += "$tag complexity is $complexity entries; maximum is $MAX_SOURCE_COMPLEXITY"
                valid = false
            }
            if (!valid) return false
        }
        return true
    }

    private fun validateSource(
        source: SourceConfig,
        seenApis: MutableSet<String>,
        errors: MutableList<String>,
    ) {
        val tag = "source '${source.api}':"
        if (source.api.isBlank()) errors += "a source has a blank api"
        if (!seenApis.add(source.api)) errors += "$tag duplicate api"
        if (source.language.isBlank()) errors += "$tag blank language"
        if (!source.baseUrl.startsWith("http")) errors += "$tag baseUrl must be an absolute http(s) URL"

        val isGeneric = source.engine == "generic"
        val isLegacy = source.engine == "legacy" || source.engine.startsWith("kotlin:")
        if (!isGeneric && !isLegacy) {
            errors += "$tag unknown engine '${source.engine}' (expected 'generic', 'legacy', or 'kotlin:<id>')"
        }

        // Validate archived compatibility descriptors before the generic-only early return. The
        // shipping catalog manager rejects these engine values before activation.
        validateLifecycleMetadata(source, tag, errors)

        // Non-generic behavior is intentionally outside this config validator and never executable
        // through the shipping source registry.
        if (!isGeneric) {
            if (source.filters.isNotEmpty()) {
                errors += "$tag filters: declared on a non-generic engine — filters are a generic-engine capability"
            }
            return
        }

        if (!strategies.hasPagination(source.pagination.type)) {
            errors += "$tag unknown pagination strategy '${source.pagination.type}'"
        }
        if (source.endpoints["home"] == null && source.endpoints["featured"] == null) {
            errors += "$tag generic source must define at least a 'home' or 'featured' endpoint"
        }
        validateEndpoints(source, tag, errors)
        validateFields(source, tag, errors)
        validateSearchFilters(source, tag, errors)
    }

    private fun validateLifecycleMetadata(
        source: SourceConfig,
        tag: String,
        errors: MutableList<String>,
    ) {
        if (source.siteState !in SUPPORTED_SITE_STATES) {
            errors += "$tag unknown siteState '${source.siteState}' (expected one of $SUPPORTED_SITE_STATES)"
        }
        if (source.lifecycle !in SUPPORTED_LIFECYCLES) {
            errors += "$tag unknown lifecycle '${source.lifecycle}' (expected one of $SUPPORTED_LIFECYCLES)"
        }
        validateHostList(tag, "previousHosts", source.previousHosts, errors)
        validateHostList(tag, "previousImageHosts", source.previousImageHosts, errors)
        validateHostList(tag, "trustedHosts", source.trustedHosts, errors)
        source.icon?.let { validateIcon(it, tag, errors) }
    }

    // Icons are render-only, but a malformed descriptor must die at validation (fail-closed), not
    // silently never resolve: a key outside the registry's `[a-z0-9_]` vocabulary or a non-https
    // URL would look authored-and-working while always falling through to the fallback avatar.
    private fun validateIcon(
        icon: IconSpec,
        tag: String,
        errors: MutableList<String>,
    ) {
        if (icon.resourceKey.isNotEmpty() && !icon.resourceKey.matches(ICON_RESOURCE_KEY)) {
            errors += "$tag icon resourceKey '${icon.resourceKey}' must match [a-z0-9_]{1,64}"
        }
        if (icon.remoteUrl.isNotEmpty() && !icon.remoteUrl.startsWith("https://")) {
            errors += "$tag icon remoteUrl must be an absolute https URL (no cleartext icons)"
        }
        if (icon.resourceKey.isEmpty() && icon.remoteUrl.isEmpty()) {
            errors += "$tag icon block is present but empty — set resourceKey and/or remoteUrl, or omit it"
        }
    }

    private fun validateEndpoints(
        source: SourceConfig,
        tag: String,
        errors: MutableList<String>,
    ) {
        for ((verb, spec) in source.endpoints) {
            if (spec.url.isBlank()) errors += "$tag endpoint '$verb' has a blank url"
            // The raw {query} var is the UNENCODED search term — legitimate only in formBody
            // values (the executor form-encodes those). In a URL it must be {queryEncoded} and in
            // a JSON body {queryJson}; the raw form would break (or corrupt the body) on the first
            // search containing a space, '&', quote, or backslash. Rejecting at validation keeps
            // the engine's fail-closed posture: a typo'd config is rejected instead of issuing
            // malformed requests. ("{query}" cannot false-match "{queryEncoded}"/
            // "{queryJson}" — the closing brace pins the exact var name.)
            if (spec.url.contains("{query}")) {
                errors += "$tag endpoint '$verb' url uses raw {query} — use {queryEncoded}"
            }
            if (spec.jsonBody.contains("{query}")) {
                errors += "$tag endpoint '$verb' jsonBody uses raw {query} — use {queryJson}"
            }
            if (spec.method.isNotEmpty() && spec.method.lowercase() !in SUPPORTED_METHODS) {
                errors += "$tag endpoint '$verb' has unknown method '${spec.method}'"
            }
            if (spec.format.isNotEmpty() && spec.format !in SUPPORTED_FORMATS) {
                errors += "$tag endpoint '$verb' has unknown format '${spec.format}'"
            }
            for (filter in spec.listFilters) {
                if (filter.op !in SUPPORTED_FILTER_OPS) {
                    errors += "$tag endpoint '$verb' filter references unknown op '${filter.op}'"
                }
                if (filter.mode !in SUPPORTED_FILTER_MODES) {
                    errors += "$tag endpoint '$verb' filter has unknown mode '${filter.mode}'"
                }
            }
        }
    }

    private fun validateFields(
        source: SourceConfig,
        tag: String,
        errors: MutableList<String>,
    ) {
        for ((key, spec) in source.fields) {
            for (transform in spec.transform) {
                if (!strategies.hasTransform(transform.fn)) {
                    errors += "$tag field '$key' references unknown transform '${transform.fn}'"
                }
            }
            if (spec.dateStrategy.isNotEmpty() && !strategies.hasDateStrategy(spec.dateStrategy)) {
                errors += "$tag field '$key' references unknown date strategy '${spec.dateStrategy}'"
            }
            if (spec.imageStrategy.isNotEmpty() && !strategies.hasImageStrategy(spec.imageStrategy)) {
                errors += "$tag field '$key' references unknown image strategy '${spec.imageStrategy}'"
            }
        }
    }

    // ---------------------------------------------------------------------------------------------
    // Config-driven search filters (docs/sources/CONFIG_DRIVEN_FILTERS_PLAN.md §4). Error paths
    // follow "source '<api>': filters: filter '<id>': <field>: <message>" so a malformed filter is
    // pinpointed, never silently ignored — the document is rejected all-or-nothing like everything
    // else. Whitelists mirror FilterRequestComposer's string handling exactly (typo ⇒ rejection at
    // validation, not silent degradation at request time).
    // ---------------------------------------------------------------------------------------------
    private fun validateSearchFilters(
        source: SourceConfig,
        tag: String,
        errors: MutableList<String>,
    ) {
        if (source.filters.isEmpty()) return

        val byId = mutableMapOf<String, FilterDefinition>()
        for (filter in source.filters) {
            val ftag = "$tag filters: filter '${filter.id}':"
            if (filter.id.isBlank()) {
                errors += "$tag filters: a filter has a blank id"
            } else {
                if (!filter.id.matches(FILTER_ID)) errors += "$ftag id: must match [a-z0-9_]{1,64}"
                if (byId.put(filter.id, filter) != null) errors += "$ftag id: duplicate filter id"
            }
            if (filter.label.isBlank()) errors += "$ftag label: must not be blank"
            if (filter.type !in SUPPORTED_FILTER_TYPES) {
                errors += "$ftag type: unknown '${filter.type}' (expected one of $SUPPORTED_FILTER_TYPES)"
            }
            validateStandardFilterId(filter, ftag, errors)
            validateFilterOptions(filter, ftag, errors)
            validateFilterDefaults(filter, ftag, errors)
            validateFilterRequest(source, filter, ftag, errors)
        }
        // Cross-filter rules need the complete id map first.
        for (filter in source.filters) {
            val ftag = "$tag filters: filter '${filter.id}':"
            validateFilterConditions(filter, byId, ftag, errors)
            validateFilterExcludeOf(filter, byId, ftag, errors)
        }
        detectVisibilityCycles(source.filters, tag, errors)
    }

    // Standard ids are conventions, not special code paths — but a `sort` rendered as a free-text
    // box (or genres as a toggle) is always an authoring mistake, so the types are pinned.
    private fun validateStandardFilterId(
        filter: FilterDefinition,
        ftag: String,
        errors: MutableList<String>,
    ) {
        when (filter.id) {
            "sort" ->
                if (filter.type != "select") {
                    errors += "$ftag type: standard id 'sort' must be select, not '${filter.type}'"
                }
            "genres", "status", "language", "type" ->
                if (filter.type !in setOf("select", "multiselect")) {
                    errors += "$ftag type: standard id '${filter.id}' must be select or multiselect, not '${filter.type}'"
                }
        }
    }

    private fun validateFilterOptions(
        filter: FilterDefinition,
        ftag: String,
        errors: MutableList<String>,
    ) {
        val hasOptions = filter.type == "select" || filter.type == "multiselect"
        if (hasOptions && filter.options.isEmpty()) {
            errors += "$ftag options: ${filter.type} requires at least one option"
        }
        if (!hasOptions && filter.options.isNotEmpty()) {
            errors += "$ftag options: not allowed on type '${filter.type}'"
        }
        val seenValues = mutableSetOf<String>()
        for (option in filter.options) {
            if (option.value.isBlank()) errors += "$ftag options: an option has a blank value"
            if (option.value.isNotBlank() && !seenValues.add(option.value)) {
                errors += "$ftag options: duplicate option value '${option.value}' (selection would be ambiguous)"
            }
        }
    }

    private fun validateFilterDefaults(
        filter: FilterDefinition,
        ftag: String,
        errors: MutableList<String>,
    ) {
        val optionValues = filter.options.map { it.value }.toSet()
        if (filter.type == "multiselect") {
            if (filter.default.isNotBlank()) {
                errors += "$ftag default: multiselect uses 'defaults', not 'default'"
            }
            for (value in filter.defaults) {
                if (value !in optionValues) errors += "$ftag defaults: '$value' is not a declared option value"
            }
        } else {
            if (filter.defaults.isNotEmpty()) {
                errors += "$ftag defaults: only multiselect uses 'defaults' (use 'default')"
            }
            when (filter.type) {
                "select" ->
                    if (filter.default.isNotBlank() && filter.default !in optionValues) {
                        errors += "$ftag default: '${filter.default}' is not a declared option value"
                    }
                "toggle" ->
                    if (filter.default !in setOf("", "true", "false")) {
                        errors += "$ftag default: toggle default must be 'true' or 'false', not '${filter.default}'"
                    }
                "number" ->
                    if (filter.default.isNotBlank() && filter.default.toDoubleOrNull()?.isFinite() != true) {
                        errors += "$ftag default: '${filter.default}' is not numeric"
                    }
            }
        }
        if (filter.required) {
            val hasUsableDefault =
                if (filter.type == "multiselect") filter.defaults.isNotEmpty() else filter.default.isNotBlank()
            if (!hasUsableDefault) {
                errors += "$ftag required: a required filter needs a usable default (the runtime must always satisfy it)"
            }
        }
    }

    private fun validateFilterRequest(
        source: SourceConfig,
        filter: FilterDefinition,
        ftag: String,
        errors: MutableList<String>,
    ) {
        val request = filter.request
        if (request.target !in SUPPORTED_FILTER_TARGETS) {
            errors += "$ftag request.target: unknown '${request.target}' (expected one of $SUPPORTED_FILTER_TARGETS)"
        }
        if (request.param.isBlank()) errors += "$ftag request.param: must not be blank"
        if (request.encode !in SUPPORTED_FILTER_ENCODINGS) {
            errors += "$ftag request.encode: unknown '${request.encode}' (expected one of $SUPPORTED_FILTER_ENCODINGS)"
        }

        // Encoding ↔ target compatibility.
        if (request.encode == "repeat" && request.target !in setOf("query", "form")) {
            errors += "$ftag request.encode: 'repeat' is only valid for query/form targets"
        }
        if (request.encode == "json-array" && request.target != "body-json") {
            errors += "$ftag request.encode: 'json-array' is only valid for the body-json target"
        }
        if (request.target == "body-json" && request.encode !in setOf("single", "json-array")) {
            errors += "$ftag request.encode: body-json supports 'single' or 'json-array', not '${request.encode}'"
        }
        // Encoding ↔ control-type compatibility: only a multiselect can produce multiple values.
        if (request.encode in setOf("csv", "repeat", "json-array") && filter.type != "multiselect") {
            errors += "$ftag request.encode: '${request.encode}' requires type multiselect, not '${filter.type}'"
        }

        // Placeholder targets fill template holes — the hole can never be omitted, so the value
        // must be guaranteed (path) and the placeholder must actually exist in the template.
        val isPlaceholderTarget = request.target == "path" || request.target == "body-json"
        if (isPlaceholderTarget) {
            if (!request.param.matches(PLACEHOLDER_NAME)) {
                errors += "$ftag request.param: placeholder name must match [a-zA-Z0-9_]+"
            }
            if (request.param in RESERVED_TEMPLATE_VARS) {
                errors += "$ftag request.param: '${request.param}' shadows a reserved engine template var"
            }
        }
        if (request.target == "path") {
            val guaranteed =
                if (filter.type == "multiselect") filter.defaults.isNotEmpty() else filter.default.isNotBlank()
            if (!guaranteed) {
                errors += "$ftag request.target: a path-target filter must declare a non-empty default " +
                    "(a URL placeholder cannot be omitted)"
            }
        }

        // Per-endpoint checks for every verb the filter applies to.
        if (filter.appliesTo.isEmpty()) errors += "$ftag appliesTo: must not be empty"
        for (verb in filter.appliesTo) {
            val etag = "$ftag appliesTo '$verb':"
            if (verb !in SUPPORTED_FILTER_VERBS) {
                errors += "$etag unsupported verb (v1 supports $SUPPORTED_FILTER_VERBS)"
                continue
            }
            val endpoint = source.endpoints[verb]
            if (endpoint == null) {
                errors += "$etag filter is mapped to an endpoint that does not exist"
                continue
            }
            val method = endpoint.method.lowercase()
            when (request.target) {
                "form" -> {
                    if (method !in POST_FORM_METHODS) {
                        errors += "$etag form target requires a post-form endpoint (method is '${endpoint.method}')"
                    }
                    if (request.param in endpoint.formBody.keys) {
                        errors += "$etag request.param '${request.param}' collides with a static formBody key"
                    }
                }
                "body-json" -> {
                    if (method !in POST_JSON_METHODS) {
                        errors += "$etag body-json target requires a post-json endpoint (method is '${endpoint.method}')"
                    }
                    if (!endpoint.jsonBody.contains("{${request.param}}")) {
                        errors += "$etag jsonBody template does not contain the placeholder {${request.param}}"
                    }
                }
                "path" ->
                    if (!endpoint.url.contains("{${request.param}}")) {
                        errors += "$etag url template does not contain the placeholder {${request.param}}"
                    }
                "query" ->
                    if (endpoint.url.contains("?${request.param}=") || endpoint.url.contains("&${request.param}=")) {
                        errors += "$etag request.param '${request.param}' is already hardcoded in the url template"
                    }
            }
        }
    }

    private fun validateFilterConditions(
        filter: FilterDefinition,
        byId: Map<String, FilterDefinition>,
        ftag: String,
        errors: MutableList<String>,
    ) {
        for (condition in filter.visibleWhen) {
            val referenced = byId[condition.filter]
            if (referenced == null) {
                errors += "$ftag visibleWhen: references unknown filter '${condition.filter}'"
                continue
            }
            if (condition.filter == filter.id) {
                errors += "$ftag visibleWhen: a filter cannot depend on itself"
            }
            if (condition.anyOf.isEmpty()) {
                errors += "$ftag visibleWhen: anyOf must not be empty"
            }
            // Enumerable referenced filters pin the value vocabulary; text/number can't be checked.
            val possible =
                when (referenced.type) {
                    "select", "multiselect" -> referenced.options.map { it.value }.toSet()
                    "toggle" -> setOf("true", "false")
                    else -> null
                }
            if (possible != null) {
                for (value in condition.anyOf) {
                    if (value !in possible) {
                        errors += "$ftag visibleWhen: anyOf value '$value' is not a possible value of " +
                            "filter '${condition.filter}'"
                    }
                }
            }
        }
    }

    private fun validateFilterExcludeOf(
        filter: FilterDefinition,
        byId: Map<String, FilterDefinition>,
        ftag: String,
        errors: MutableList<String>,
    ) {
        if (filter.excludeOf.isBlank()) return
        if (filter.type != "multiselect") {
            errors += "$ftag excludeOf: only a multiselect can be an exclusion counterpart"
        }
        if (filter.excludeOf == filter.id) {
            errors += "$ftag excludeOf: a filter cannot exclude against itself"
            return
        }
        val included = byId[filter.excludeOf]
        if (included == null) {
            errors += "$ftag excludeOf: references unknown filter '${filter.excludeOf}'"
            return
        }
        if (included.type != "multiselect") {
            errors += "$ftag excludeOf: referenced filter '${filter.excludeOf}' must be a multiselect"
        }
        if (included.excludeOf.isNotBlank()) {
            errors += "$ftag excludeOf: chained exclusion (referenced filter '${filter.excludeOf}' " +
                "has excludeOf itself)"
        }
        val overlap = filter.defaults.toSet() intersect included.defaults.toSet()
        if (overlap.isNotEmpty()) {
            errors += "$ftag excludeOf: defaults overlap with filter '${filter.excludeOf}' ($overlap) — " +
                "a value cannot default to included AND excluded"
        }
    }

    // The visibleWhen graph must be acyclic. Kahn's algorithm keeps traversal iterative so a
    // maliciously deep signed document cannot overflow the stack.
    private fun detectVisibilityCycles(
        filters: List<FilterDefinition>,
        tag: String,
        errors: MutableList<String>,
    ) {
        val byId = filters.associateBy { it.id }
        val edges = LinkedHashMap<String, Set<String>>(byId.size)
        val indegree = byId.keys.associateWith { 0 }.toMutableMap()
        byId.forEach { (id, filter) ->
            val dependencies =
                filter.visibleWhen
                    .asSequence()
                    .map { it.filter }
                    .filter { it in byId }
                    .toCollection(linkedSetOf())
            edges[id] = dependencies
            dependencies.forEach { dependency -> indegree[dependency] = indegree.getValue(dependency) + 1 }
        }
        val ready = ArrayDeque(indegree.filterValues { it == 0 }.keys)
        var visited = 0
        while (ready.isNotEmpty()) {
            val id = ready.removeFirst()
            visited++
            edges[id].orEmpty().forEach { dependency ->
                val remaining = indegree.getValue(dependency) - 1
                indegree[dependency] = remaining
                if (remaining == 0) ready.addLast(dependency)
            }
        }
        if (visited != byId.size) {
            errors += "$tag filters: visibleWhen: dependency cycle detected"
        }
    }

    // The three host lists hold BARE hosts ("azoramoon.co"): matching against a stored URL's host
    // is exact, so a scheme/path/blank entry would silently never match — reject it at validation
    // (fail-closed) instead of letting a malformed entry neuter migration or trust.
    private fun validateHostList(
        tag: String,
        field: String,
        hosts: List<String>,
        errors: MutableList<String>,
    ) {
        for (host in hosts) {
            if (!isBareHost(host)) {
                errors += "$tag $field entry '$host' must be a bare host (no scheme, path, or spaces)"
            }
        }
    }

    private fun isBareHost(host: String): Boolean {
        if (host.isBlank() || host.contains("://")) return false
        // No port suffix either (2026-07 audit): every consumer (urlHost / ConfigHostTrust) compares
        // portless hosts, so a "host:8080" entry would validate yet silently never match — the exact
        // authoring mistake this rule exists to reject.
        return !host.contains('/') && !host.contains(':') && host.none { it.isWhitespace() }
    }

    companion object {
        /** The only schema major this build understands. Bumped only on incompatible model changes. */
        const val SUPPORTED_SCHEMA_VERSION = 1
        internal const val MAX_SOURCES = 512
        internal const val MAX_COLLECTION_ENTRIES = 256
        internal const val MAX_FILTERS = 128
        internal const val MAX_SOURCE_COMPLEXITY = 20_000L

        // Lifecycle metadata whitelists (SourceRegistry retirement §2). SUPPORTED_SITE_STATES
        // mirrors the persisted SourceState enum (:data:local) — the catalog sync maps the string
        // to the enum, and an unknown value must die here, not silently no-op there.
        private val SUPPORTED_SITE_STATES = setOf("WORKING", "UNDER_MAINTENANCE", "STOPPED", "ADULT_18_PLUS")
        private val SUPPORTED_LIFECYCLES = setOf("active", "disabled", "removed")
        private val ICON_RESOURCE_KEY = Regex("[a-z0-9_]{1,64}")

        // Whitelists mirror the engine's own string handling so a typo in a cached/remote document
        // is rejected at validation rather than silently degrading at runtime (unknown op drops every
        // item, unknown method falls back to GET, unknown format falls into the listSelector heuristic).
        private val SUPPORTED_METHODS =
            setOf(
                "get",
                "post-form",
                "post_form",
                "postform",
                "post-json",
                "post_json",
                "postjson",
            )
        private val SUPPORTED_FORMATS = setOf("json", "html", "script-json")
        private val SUPPORTED_FILTER_OPS = setOf("equals", "notEquals", "contains", "notNull", "isNull")
        private val SUPPORTED_FILTER_MODES = setOf("include", "exclude")

        // Config-driven search filters (CONFIG_DRIVEN_FILTERS_PLAN.md §4). `range`/`date` and
        // non-search appliesTo verbs are reserved vocabulary: rejected here until an engine/UI
        // consumer exists, so a config can never silently claim an unimplemented capability.
        private val SUPPORTED_FILTER_TYPES = setOf("select", "multiselect", "toggle", "text", "number")
        private val SUPPORTED_FILTER_TARGETS = setOf("query", "path", "form", "header", "body-json")
        private val SUPPORTED_FILTER_ENCODINGS = setOf("single", "csv", "repeat", "json-array")
        private val SUPPORTED_FILTER_VERBS = setOf("search")
        private val POST_FORM_METHODS = setOf("post-form", "post_form", "postform")
        private val POST_JSON_METHODS = setOf("post-json", "post_json", "postjson")
        private val FILTER_ID = Regex("[a-z0-9_]{1,64}")
        private val PLACEHOLDER_NAME = Regex("[a-zA-Z0-9_]+")

        /** Vars `GenericSourceClient.vars()` always seeds — a filter placeholder must not shadow them. */
        private val RESERVED_TEMPLATE_VARS =
            setOf(
                "baseUrl",
                "imageBase",
                "page",
                "pageOffset",
                "query",
                "queryEncoded",
                "queryJson",
                "itemUrl",
                "chapterUrl",
                "id",
            )
    }
}
