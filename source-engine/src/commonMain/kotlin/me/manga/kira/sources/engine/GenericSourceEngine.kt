package me.manga.kira.sources.engine

import kotlinx.coroutines.CancellationException
import me.manga.kira.sources.contracts.SourceChapter
import me.manga.kira.sources.contracts.SourceChapterRef
import me.manga.kira.sources.contracts.SourceChallengeListener
import me.manga.kira.sources.contracts.SourceDetails
import me.manga.kira.sources.contracts.SourceEngine
import me.manga.kira.sources.contracts.SourceEngineError
import me.manga.kira.sources.contracts.SourceEngineResult
import me.manga.kira.sources.contracts.SourceFeaturedItem
import me.manga.kira.sources.contracts.SourceFilterSelections
import me.manga.kira.sources.contracts.SourceHeaderProvider
import me.manga.kira.sources.contracts.SourceBaseUrlProvider
import me.manga.kira.sources.contracts.SourceHttpMethod
import me.manga.kira.sources.contracts.SourceListItem
import me.manga.kira.sources.contracts.SourceMangaRef
import me.manga.kira.sources.contracts.SourcePage
import me.manga.kira.sources.contracts.SourceRequest
import me.manga.kira.sources.contracts.SourceResponse
import me.manga.kira.sources.contracts.SourceTransport
import me.manga.kira.sources.contracts.isValidSourceBaseUrl
import me.manga.kira.sources.contracts.model.EndpointSpec
import me.manga.kira.sources.contracts.model.FieldSpec
import me.manga.kira.sources.contracts.model.FilterSpec
import me.manga.kira.sources.contracts.model.SourceConfig
import me.manga.kira.sources.contracts.model.TransformSpec
import me.manga.kira.sources.engine.internal.ComposedFilters
import me.manga.kira.sources.engine.internal.DateStrategies
import me.manga.kira.sources.engine.internal.Extractor
import me.manga.kira.sources.engine.internal.FilterRequestComposer
import me.manga.kira.sources.engine.internal.ItemScope
import me.manga.kira.sources.engine.internal.JsonEscape
import me.manga.kira.sources.engine.internal.Templates
import me.manga.kira.sources.engine.internal.Transforms
import me.manga.kira.sources.engine.internal.UrlEncode

/**
 * The one generic source implementation. It satisfies [SourceEngine] for ANY source whose
 * behavior can be described by a [SourceConfig] — by interpreting that config (HTTP request templates
 * + named extraction/transform/date strategies) against an injected [SourceTransport], then mapping the
 * extracted strings onto neutral contract models. It contains no source-specific code and no HTTP library.
 *
 * The generic path is the only executable source path. A failure is returned to the caller; no
 * legacy adapter is inferred. Golden-fixture tests remain the engine's behavioural specification.
 *
 * Stage-0 relative-link resolution uses the source's base URL (for Ksoup `abs:*` and [absolutize]).
 * Resolving against [SourceResponse.finalUrl] (cross-host redirects / path-relative links) is a
 * Stage-1 refinement and will land with redirect golden fixtures; until then a generic source must
 * declare a `baseUrl` that its relative links resolve against.
 *
 * The effective base URL is taken live from the active catalog projection through
 * [SourceBaseUrlProvider], falling back to [SourceConfig.baseUrl] when none is stored.
 */
class GenericSourceEngine(
    private val config: SourceConfig,
    private val http: SourceTransport,
    private val headerStore: SourceHeaderProvider,
    private val cloudflare: SourceChallengeListener? = null,
    private val baseUrlProvider: SourceBaseUrlProvider? = null,
) : SourceEngine {

    override val api: String = config.api

    // The live base URL for this source, resolved (once per verb, before any extraction) from
    // [baseUrlProvider]. A blank/absent override keeps the signed config value.
    private var effectiveBaseUrl: String = config.baseUrl

    /**
     * Refresh [effectiveBaseUrl] from the live [SourceBaseUrlProvider] before a verb's request/extraction
     * runs. A null provider or a blank stored value keeps the frozen config baseUrl. Resolves to the same
     * string for every verb of a source, so concurrent verbs writing it is benign.
     */
    private suspend fun resolveEffectiveBaseUrl() {
        // The Room projection is user-editable and can contain stale values from older releases.
        // Only a real HTTP(S) origin may override the already-validated immutable descriptor.
        val live =
            baseUrlProvider
                ?.baseUrlFor(config.api)
                ?.takeIf(::isValidSourceBaseUrl)
        effectiveBaseUrl = live ?: config.baseUrl
    }

    override suspend fun home(page: Int): SourceEngineResult<List<SourceListItem>> =
        fetchList("home", page, query = "", map = ::homeFeedItemFrom)

    override suspend fun featured(page: Int): SourceEngineResult<List<SourceFeaturedItem>> =
        fetchList("featured", page, query = "", map = ::featuredFrom)

    override suspend fun search(query: String, page: Int, filters: SourceFilterSelections): SourceEngineResult<List<SourceListItem>> =
        fetchList("search", page, query = query, selections = filters, map = ::homeFeedItemFrom)

    override suspend fun details(manga: SourceMangaRef): SourceEngineResult<SourceDetails> {
        val endpoint = config.endpoints["details"]
            ?: return SourceEngineResult.Failure(SourceEngineError.Required("endpoint:details"))
        resolveEffectiveBaseUrl()
        val base = runRequest(endpoint, vars(itemUrl = manga.url)) { resp -> detailsFrom(manga, endpoint, resp.body) }
        // Some sources serve manga metadata and chapters through two distinct endpoints.
        // scalars and the chapter list from two DISTINCT endpoints. When a `chapters` endpoint is declared,
        // fetch it as a SECOND request and replace the (inline) chapter list with the chapters parsed from
        // THAT body — using the chapters endpoint's own root/listSelector (so JSON `root` and HTML
        // `listSelector`/POST_FORM all work). If the second request fails, the whole details request
        // fails instead of returning a misleading chapter-less success.
        val chaptersEndpoint = config.endpoints["chapters"]
        if (base !is SourceEngineResult.Success || chaptersEndpoint == null) return base
        return when (val chapters = chaptersPaginated(chaptersEndpoint, vars(itemUrl = manga.url))) {
            is SourceEngineResult.Success -> SourceEngineResult.Success(base.value.copy(chapters = chapters.value))
            is SourceEngineResult.Failure -> chapters
        }
    }

    override suspend fun pages(
        manga: SourceMangaRef,
        chapter: SourceChapter,
    ): SourceEngineResult<List<SourcePage>> {
        val endpoint = config.endpoints["pages"]
            ?: return SourceEngineResult.Failure(SourceEngineError.Required("endpoint:pages"))
        resolveEffectiveBaseUrl()
        val pageHeaders = requestHeaders()
        // Reuse the already-read headers for the HTTP request too — pages() opens a chapter on the hot
        // path, and a second requestHeaders() would re-read persistent storage.
        return runRequest(endpoint, vars(itemUrl = manga.url, chapterUrl = chapter.url), precomputedHeaders = pageHeaders) { resp ->
                // Parse the (large) page body once and read both the page list and the response root off it.
                val parsed = Extractor.parse(resp.body, effectiveBaseUrl, endpoint)
                val scopes = parsed.listScopes()
                // The response root scope + chosen-list dir let a page.image template reference a sibling of
                // the page array (e.g. DilarV2's `{root:storage_key}`) and the `{root:__dir}` token bound to
                // which coalesced list-root (webp_pages vs pages) was used.
                val rootScope = parsed.rootScope()
                val chosenDir = chosenRootDir(rootScope, endpoint)
                // If the source exposes a per-page order field, sort numerically by it (generic, reusable):
                // some APIs return page images out of array order (mirrors legacy `images.sortedBy { order }`).
                val ordered = if (config.fields["page.order"] != null) {
                    scopes.sortedBy { text(it, "page.order").toDoubleOrNull() ?: 0.0 }
                } else {
                    scopes
                }
                ordered.mapNotNull { scope ->
                    image(scope, "page.image", rootScope, chosenDir)?.let { SourcePage(url = it, headers = pageHeaders) }
                }
        }
    }

    // --- request plumbing -------------------------------------------------------------------------

    private suspend fun <T> fetchList(
        verb: String,
        page: Int,
        query: String,
        selections: SourceFilterSelections = SourceFilterSelections(),
        map: (ItemScope, String) -> T,
    ): SourceEngineResult<List<T>> {
        val endpoint = config.endpoints[verb]
            ?: return SourceEngineResult.Failure(SourceEngineError.Required("endpoint:$verb"))
        resolveEffectiveBaseUrl()
        // Declarative filter → request mapping (CONFIG_DRIVEN_FILTERS_PLAN.md §3). Defaults apply
        // even to an empty selection; a source with no declared filters composes to EMPTY.
        val composed = when (val c = FilterRequestComposer.compose(config.filters, verb, selections)) {
            is SourceEngineResult.Success -> c.value
            is SourceEngineResult.Failure -> return c
        }
        return runRequest(endpoint, vars(page = page, query = query) + composed.templateVars, composed) { resp ->
            Extractor.listScopes(resp.body, effectiveBaseUrl, endpoint)
                .filter { passesFilters(it, endpoint.listFilters) }
                .filterNot { isBlacklistedByGenre(it, verb) }
                .map { map(it, verb) }
        }
    }

    /**
     * Keep/drop a list item by [SourceConfig] endpoint [FilterSpec]s — all must pass. Mirrors legacy
     * content filters that the JSONPath subset can't express inline (e.g. DilarV2 search dropping
     * `series_type.name == "Novel"` / deleted entries from the "Manga" group). `include` keeps the item
     * when the predicate matches; `exclude` drops it when it matches.
     */
    private fun passesFilters(scope: ItemScope, filters: List<FilterSpec>): Boolean {
        for (f in filters) {
            val v = scope.raw(f.path)
            val matched = when (f.op) {
                "equals" -> v == f.value
                "notEquals" -> v != f.value
                "contains" -> v?.contains(f.value, ignoreCase = true) == true
                "notNull" -> !v.isNullOrEmpty()
                "isNull" -> v.isNullOrEmpty()
                else -> false
            }
            val keep = if (f.mode == "include") matched else !matched
            if (!keep) return false
        }
        return true
    }

    /**
     * Drop a list item whose genres intersect [SourceConfig.blacklistGenres] — case-insensitive
     * substring match, mirroring the legacy `hasBlacklistedGenres` (`genre.lowercase().contains(bl)`).
     * Sources that declare a blacklist (e.g. Zazamanga's adult-genre list) thus filter their home/search/
     * featured feeds for parity. A source with no blacklist, or one whose items carry no `item.genres`,
     * never filters.
     */
    private fun isBlacklistedByGenre(scope: ItemScope, verb: String): Boolean {
        if (config.blacklistGenres.isEmpty()) return false
        // Resolve genres through the per-verb override (e.g. `search.item.genres`) just like the mapping
        // path does — otherwise a source declaring genres only for a specific verb would silently skip
        // blacklist filtering on that verb. No genres declared for this verb → nothing to filter.
        val key = verbKey(verb, "item.genres")
        if (config.fields[key] == null) return false
        val genres = textList(scope, key)
        return genres.any { g -> config.blacklistGenres.any { bl -> g.contains(bl, ignoreCase = true) } }
    }

    private suspend fun <T> runRequest(
        endpoint: EndpointSpec,
        vars: Map<String, String>,
        composed: ComposedFilters = ComposedFilters.EMPTY,
        precomputedHeaders: Map<String, String>? = null,
        extract: (SourceResponse) -> T,
    ): SourceEngineResult<T> {
        return try {
            // Filter query pairs append AFTER template expansion (percent-encoded, ?/& aware);
            // filter headers override same-name computed headers; filter form entries append after
            // the static formBody in declaration order. All deterministic — see FilterRequestComposer.
            val url = FilterRequestComposer.appendQueryPairs(Templates.expand(endpoint.url, vars), composed.queryPairs)
            val headers = (precomputedHeaders ?: requestHeaders()) + composed.headerEntries
            val method = methodOf(endpoint.method)
            val form = if (method == SourceHttpMethod.POST_FORM) {
                endpoint.formBody.map { (key, value) -> key to Templates.expand(value, vars) } + composed.formEntries
            } else {
                null
            }
            // POST_JSON: expand the configured body template (e.g. {"query":"{queryJson}","includes":["Manga"]})
            // and send it as the request body. Use {queryJson} (JSON-escaped) inside a jsonBody string, not
            // {query} (raw) — a quote/backslash in the search term would otherwise corrupt the body. Mirrors
            // legacy `api.postJson(url, body)`.
            val jsonBody = if (method == SourceHttpMethod.POST_JSON && endpoint.jsonBody.isNotEmpty()) {
                Templates.expand(endpoint.jsonBody, vars)
            } else {
                null
            }
            val response = http.execute(
                SourceRequest(url = url, method = method, headers = headers, formBody = form, jsonBody = jsonBody),
            )
            when {
                isCloudflareChallenge(response) -> {
                    cloudflare?.onChallenge(config.api, url)
                    SourceEngineResult.Failure(SourceEngineError.Http(403))
                }
                response.status !in 200..299 -> SourceEngineResult.Failure(SourceEngineError.Http(response.status))
                else -> SourceEngineResult.Success(extract(response))
            }
        } catch (c: CancellationException) {
            throw c
        } catch (e: UnresolvedTemplateVarException) {
            // A required URL-template value was empty; reject the broken-but-plausible request.
            SourceEngineResult.Failure(SourceEngineError.Required("var:${e.field}:${e.varName}"))
        } catch (t: Throwable) {
            SourceEngineResult.Failure(classifyTransportError(t))
        }
    }

    /**
     * Bucket a non-cancellation transport/parse [Throwable] without exposing implementation details
     * or messages across the shared contract.
     */
    private fun classifyTransportError(t: Throwable): SourceEngineError {
        val raw = (t.message ?: "").lowercase()
        val causeName = t.cause?.let { it::class.simpleName.orEmpty() }.orEmpty()
        return when {
            raw.contains("timeout") || raw.contains("timed out") ->
                SourceEngineError.Timeout
            raw.contains("unable to resolve") || raw.contains("unknown host") ||
                raw.contains("network is unreachable") || raw.contains("connection refused") ->
                SourceEngineError.NoConnectivity
            causeName.contains("Serialization") || causeName.contains("Json") ||
                t::class.simpleName.orEmpty().let { it.contains("Serialization") || it.contains("Json") } ->
                SourceEngineError.InvalidResponse
            else ->
                SourceEngineError.Unexpected(t::class.simpleName ?: "unknown")
        }
    }

    /**
     * Static config headers, plus the per-api captured headers ONLY when [SourceConfig.usesCapturedHeaders]
     * is set. A header-free source (e.g. Azora) skips the header-store read entirely — no needless I/O
     * or log noise on every fetch.
     */
    private suspend fun requestHeaders(): Map<String, String> =
        if (config.usesCapturedHeaders) config.headers + headerStore.headersFor(config.api) else config.headers

    private fun methodOf(method: String): SourceHttpMethod = when (method.lowercase()) {
        "post-form", "post_form", "postform" -> SourceHttpMethod.POST_FORM
        "post-json", "post_json", "postjson" -> SourceHttpMethod.POST_JSON
        else -> SourceHttpMethod.GET
    }

    private fun isCloudflareChallenge(response: SourceResponse): Boolean {
        if (response.status != 403 && response.status != 503) return false
        val body = response.body
        return body.contains("Just a moment", ignoreCase = true) ||
            body.contains("cf-chl", ignoreCase = true) ||
            body.contains("Cloudflare", ignoreCase = true)
    }

    // --- mapping ----------------------------------------------------------------------------------

    /**
     * Per-verb field override: a field may be overridden for a specific list verb via a `<verb>.<base>`
     * key (e.g. `featured.item.title`, `search.item.url`). Absent → the shared `item.*`. This lets one
     * source map a verb whose response shape differs from the others — Demonicscans' featured carousel, or
     * Tapas where home/featured are JSON (story-api) but search is HTML.
     */
    private fun verbKey(verb: String, base: String): String =
        if (config.fields.containsKey("$verb.$base")) "$verb.$base" else base

    /** A home/search row, including the recent-chapter chips (preserves the rich Home data). */
    private fun homeFeedItemFrom(scope: ItemScope, verb: String): SourceListItem = SourceListItem(
        api = config.api,
        language = config.language,
        title = text(scope, verbKey(verb, "item.title")),
        url = link(scope, verbKey(verb, "item.url")),
        coverUrl = image(scope, verbKey(verb, "item.cover")).orEmpty(),
        rating = ratingInt(text(scope, verbKey(verb, "item.rating"))),
        genres = textList(scope, verbKey(verb, "item.genres")),
        recentChapters = recentChaptersFrom(scope, verb),
    )

    private val lockedValues = setOf("true", "1", "yes")

    /**
     * A chapter is hidden when the source declares a `chapter.locked` field that resolves truthy
     * (Azora: `isLocked=true`). Generic + reusable for any site with paid/locked chapters — sources
     * without the field never filter. Locked chapters return empty images, so hiding them avoids a
     * dead-end blank reader.
     */
    private fun isLockedChapter(scope: ItemScope): Boolean {
        if (config.fields["chapter.locked"] == null) return false
        return text(scope, "chapter.locked").trim().lowercase() in lockedValues
    }

    /** Per-item recent-chapter chips: each sub-scope under `item.recentChapters` mapped via chapter.* fields. */
    private fun recentChaptersFrom(scope: ItemScope, verb: String): List<SourceChapterRef> {
        val spec = config.fields[verbKey(verb, "item.recentChapters")] ?: return emptyList()
        return scope.children(spec).filterNot { isLockedChapter(it) }.map { ch ->
            SourceChapterRef(
                number = text(ch, "chapter.number"),
                url = link(ch, "chapter.url"),
            )
        }.filter { isNavigable(it.url) }
    }

    /** A featured-carousel row (cover + title only); honors `featured.item.*` overrides via [verbKey]. */
    private fun featuredFrom(scope: ItemScope, verb: String): SourceFeaturedItem = SourceFeaturedItem(
        api = config.api,
        language = config.language,
        title = text(scope, verbKey(verb, "item.title")),
        url = link(scope, verbKey(verb, "item.url")),
        coverUrl = image(scope, verbKey(verb, "item.cover")).orEmpty(),
    )

    private fun detailsFrom(manga: SourceMangaRef, endpoint: EndpointSpec, body: String): SourceDetails {
        val root = Extractor.rootScope(body, effectiveBaseUrl, endpoint)
        val html = Extractor.isHtml(endpoint)
        val chapterLocator = config.fields["detail.chapters"]
        val chapters = if (chapterLocator != null) {
            Extractor.subScopes(body, effectiveBaseUrl, html, chapterLocator)
                .filterNot { isLockedChapter(it) }
                .map { chapterFrom(it) }
                .filter { isNavigable(it.url) }
        } else {
            emptyList()
        }
        return SourceDetails(
            api = config.api,
            language = config.language,
            title = text(root, "detail.title").ifEmpty { manga.title },
            url = manga.url,
            coverUrl = image(root, "detail.cover") ?: manga.coverUrl,
            description = text(root, "detail.description"),
            author = text(root, "detail.author"),
            rating = text(root, "detail.rating"),
            status = text(root, "detail.status"),
            genres = textList(root, "detail.genres"),
            chapters = chapters,
        )
    }

    /**
     * SourceChapter list parsed from a DEDICATED `chapters` response (the two-request "separated details"
     * pattern). The list is located via the chapters endpoint's own `root`/`listSelector`; each element
     * is mapped through the same `chapter.*` fields + locked-filter as the inline path.
     */
    private fun chaptersFrom(parsed: Extractor.ParsedBody): List<SourceChapter> =
        parsed.listScopes()
            .filterNot { isLockedChapter(it) }
            .map { chapterFrom(it) }
            .filter { isNavigable(it.url) }

    /**
     * A chapter is kept only if it has a real destination. Locked/paywalled chapters on some sites use a
     * placeholder `href="#"` (Team X) or no link — those resolve to a URL ending in `#` (or blank) and are
     * dropped, mirroring legacy `url != "#"` filtering. Real chapter URLs never end in `#`, so this never
     * drops a genuine chapter.
     */
    private fun isNavigable(url: String): Boolean = url.isNotBlank() && !url.endsWith("#")

    /**
     * Fetch the `chapters` endpoint, paginating when [EndpointSpec.pageParam] is set: loop `pageParam=1,2,…`
     * and concatenate, deciding "is there more?" from [EndpointSpec.lastPageLocator] (a numeric pagination
     * widget → loop while page < max; or a `true`/`false` has-next flag). Mirrors the legacy multi-page
     * chapter loops (Team X HTML pagination, Tapas JSON `has_next`). With no `pageParam` it is a single
     * fetch (the original separated-details behavior). A page-1 failure fails the whole call (→ details
     * fails → fallback); a later-page failure stops with what was gathered.
     */
    private suspend fun chaptersPaginated(endpoint: EndpointSpec, baseVars: Map<String, String>): SourceEngineResult<List<SourceChapter>> {
        if (endpoint.pageParam.isEmpty()) {
            return runRequest(endpoint, baseVars) { resp -> chaptersFrom(Extractor.parse(resp.body, effectiveBaseUrl, endpoint)) }
        }
        val all = mutableListOf<SourceChapter>()
        var page = 1
        var maxPage = Int.MAX_VALUE
        val safetyCap = 200
        while (page <= minOf(maxPage, safetyCap)) {
            val pageVars = baseVars + (endpoint.pageParam to page.toString())
            val res = runRequest(endpoint, pageVars) { resp ->
                // Parse each chapters page once; the chapter list and the pagination locator both read it.
                val parsed = Extractor.parse(resp.body, effectiveBaseUrl, endpoint)
                chaptersFrom(parsed) to paginationState(parsed, endpoint, page)
            }
            when (res) {
                is SourceEngineResult.Failure -> return if (page == 1) res else SourceEngineResult.Success(all)
                is SourceEngineResult.Success -> {
                    val (chs, state) = res.value
                    if (chs.isEmpty() && page > 1) break
                    all += chs
                    state.maxPage?.let { maxPage = it }
                    if (!state.hasNext) break
                    page++
                }
            }
        }
        return SourceEngineResult.Success(all)
    }

    private data class PageState(val hasNext: Boolean, val maxPage: Int?)

    /** Decide whether to keep paging from the [EndpointSpec.lastPageLocator] values on the current page. */
    private fun paginationState(parsed: Extractor.ParsedBody, endpoint: EndpointSpec, currentPage: Int): PageState {
        val loc = endpoint.lastPageLocator
        if (loc.isEmpty()) return PageState(hasNext = false, maxPage = null)
        val values = parsed.locatorValues(loc)
        val first = values.firstOrNull()?.trim().orEmpty()
        when (first.lowercase()) {
            "true" -> return PageState(hasNext = true, maxPage = null)
            "false", "", "null" -> return PageState(hasNext = false, maxPage = null)
        }
        val maxNum = values.mapNotNull { it.trim().toIntOrNull() }.maxOrNull()
        if (maxNum != null) return PageState(hasNext = currentPage < maxNum, maxPage = maxNum)
        // A non-empty, non-boolean, non-numeric locator value means "there is a next page" — e.g. a
        // DRF-style `next` cursor/URL that is a string while more pages remain and null/absent once
        // exhausted (SwatManga chapters). The empty-page break + safetyCap in chaptersPaginated bound it.
        return PageState(hasNext = true, maxPage = null)
    }

    private fun chapterFrom(scope: ItemScope): SourceChapter = SourceChapter(
        number = text(scope, "chapter.number"),
        name = text(scope, "chapter.name"),
        url = link(scope, "chapter.url"),
        date = config.fields["chapter.date"]?.let { spec ->
            scope.field(spec)?.let { DateStrategies.parse(Transforms.apply(it, spec.transform), spec.dateStrategy) }
        },
    )

    // --- field helpers ----------------------------------------------------------------------------

    /** Extract + transform a scalar text field; "" if absent. */
    private fun text(scope: ItemScope, key: String): String {
        val spec = config.fields[key] ?: return ""
        return resolveField(scope, key, spec)
    }

    /**
     * Resolve a field to its final string. Primary-or-template: if the primary locator
     * ([FieldSpec.path]/[FieldSpec.selector]) yields a non-empty value, use it; otherwise, if a
     * [FieldSpec.template] is set, expand it with `{baseUrl}`/`{imageBase}` plus [FieldSpec.vars]
     * resolved from the scope. Transforms always apply last. This expresses both "build a URL from an
     * id" (no primary, always template) and "title, else 'SourceChapter N'" (primary with template fallback).
     *
     * [strict] makes the template fail-closed: a required var that cannot be resolved (an empty
     * response locator) throws [UnresolvedTemplateVarException] instead of expanding to "" and yielding
     * a broken-but-plausible URL. URL fields ([link]/[image]) resolve strict; lenient text fields (a
     * `"SourceChapter {num}"` display fallback) tolerate an empty var.
     */
    private fun resolveField(
        scope: ItemScope,
        key: String,
        spec: FieldSpec,
        rootScope: ItemScope? = null,
        chosenDir: String = "",
        strict: Boolean = false,
    ): String {
        val primary = scope.field(spec)?.takeIf { it.isNotEmpty() }
        val base = when {
            primary != null -> primary
            spec.template.isNotEmpty() -> Templates.expand(spec.template, fieldVars(scope, key, spec, rootScope, chosenDir, strict))
            else -> ""
        }
        return Transforms.apply(base, spec.transform)
    }

    private fun fieldVars(
        scope: ItemScope,
        key: String,
        spec: FieldSpec,
        rootScope: ItemScope? = null,
        chosenDir: String = "",
        strict: Boolean = false,
    ): Map<String, String> = buildMap {
        put("baseUrl", effectiveBaseUrl.trimEnd('/'))
        put("imageBase", config.imageBase.trimEnd('/'))
        // A var expr is "locator" or "locator|fn1|fn2": resolve the locator from the scope, then apply
        // the named (argless) transforms. This lets a template var be normalized — e.g. "number|format-number"
        // reproduces legacy `formatChapterNumber` when building a "SourceChapter <n>" fallback name.
        spec.vars.forEach { (name, expr) ->
            val parts = expr.split('|')
            val loc0 = parts[0].trim()
            // `root:__dir` is an internal coalesce sentinel that is legitimately empty when the endpoint
            // declares no rootDirs — never a "required var", so it never trips the strict check below.
            val isDirToken = loc0 == "root:__dir"
            val resolved = when {
                isDirToken -> chosenDir
                // `root:<path>` → resolve against the RESPONSE root (a sibling of the per-page array),
                // e.g. DilarV2's page image needs the root-level `storage_key`.
                loc0.startsWith("root:") -> rootScope?.raw(loc0.removePrefix("root:").trim()).orEmpty()
                // Otherwise resolve against the per-item scope. The locator may list COMMA-separated
                // fallback locators (first non-empty wins) — lets one var span sources whose field name
                // drifts per verb (e.g. SwatManga: home `serie_id`, featured `serie.id`, search `id`).
                else -> parts[0].split(',')
                    .firstNotNullOfOrNull { loc -> scope.raw(loc.trim())?.takeIf { it.isNotEmpty() } }
                    .orEmpty()
            }
            // Fail-closed: a required template var that resolved to nothing would expand to "" and build a
            // broken URL (e.g. ".../postId="). Throw so runRequest fails closed.
            if (strict && !isDirToken && resolved.isEmpty()) throw UnresolvedTemplateVarException(key, name)
            val value = if (parts.size > 1) {
                Transforms.apply(resolved, parts.drop(1).map { TransformSpec(fn = it.trim()) })
            } else {
                resolved
            }
            put(name, value)
        }
    }

    /**
     * A required template var resolved to nothing. Thrown from [fieldVars] when a URL field's template
     * references a var whose response locator is empty; caught in [runRequest] and mapped to
     * [SourceEngineError.Required] so the verb fails rather than emitting a plausible-but-wrong
     * URL. Carries no `cause` — it is a deliberate fail-closed signal, not a transport failure.
     */
    private class UnresolvedTemplateVarException(val field: String, val varName: String) :
        Exception("unresolved template var '$varName' for field '$field'")

    /**
     * For a coalesced list-root (`endpoint.root = "webp_pages,pages"`), the dir token paired with whichever
     * candidate is non-empty in the response root — surfaced to page templates as `{root:__dir}`. Empty
     * when the endpoint declares no [EndpointSpec.rootDirs].
     */
    private fun chosenRootDir(rootScope: ItemScope, endpoint: EndpointSpec): String {
        if (endpoint.rootDirs.isEmpty()) return ""
        val paths = endpoint.root.split(',').map { it.trim() }
        paths.forEachIndexed { i, p ->
            if (p.isNotEmpty() && rootScope.children(FieldSpec(listPath = p)).isNotEmpty()) {
                return endpoint.rootDirs.getOrElse(i) { "" }
            }
        }
        return endpoint.rootDirs.lastOrNull().orEmpty()
    }

    private fun textList(scope: ItemScope, key: String): List<String> {
        val spec = config.fields[key] ?: return emptyList()
        return scope.fieldList(spec).map { Transforms.apply(it, spec.transform) }.filter { it.isNotBlank() }
    }

    /** A link field, absolutised against the live base URL. Resolves strict (a broken URL must fail-close). */
    private fun link(scope: ItemScope, key: String): String {
        val spec = config.fields[key] ?: return ""
        return absolutize(resolveField(scope, key, spec, strict = true), effectiveBaseUrl)
    }

    /** An image field, absolutised against [SourceConfig.imageBase] (falling back to the live base); null if absent. */
    private fun image(scope: ItemScope, key: String, rootScope: ItemScope? = null, chosenDir: String = ""): String? {
        val spec = config.fields[key] ?: return null
        val value = resolveField(scope, key, spec, rootScope, chosenDir, strict = true).takeIf { it.isNotEmpty() } ?: return null
        return absolutize(value, config.imageBase.ifEmpty { effectiveBaseUrl })
    }

    private fun ratingInt(value: String): Int? =
        value.toIntOrNull() ?: value.toDoubleOrNull()?.toInt()

    private fun absolutize(value: String, base: String): String = when {
        value.isEmpty() -> value
        value.startsWith("http://") || value.startsWith("https://") -> value
        value.startsWith("//") -> "https:$value"
        value.startsWith("/") -> base.trimEnd('/') + value
        else -> "${base.trimEnd('/')}/$value"
    }

    private fun vars(
        page: Int = config.pagination.start,
        query: String = "",
        itemUrl: String = "",
        chapterUrl: String = "",
    ): Map<String, String> = mapOf(
        "baseUrl" to effectiveBaseUrl.trimEnd('/'),
        "imageBase" to config.imageBase.trimEnd('/'),
        "page" to page.toString(),
        // Zero-based page index for APIs that count pages from 0 (e.g. Tapas: API page = UI page - 1).
        "pageOffset" to (page - 1).coerceAtLeast(0).toString(),
        "query" to query,
        "queryEncoded" to UrlEncode.encode(query),
        "queryJson" to JsonEscape.escape(query),
        "itemUrl" to itemUrl,
        "chapterUrl" to chapterUrl,
        "id" to itemUrl.trimEnd('/').substringAfterLast('/'),
    )
}
