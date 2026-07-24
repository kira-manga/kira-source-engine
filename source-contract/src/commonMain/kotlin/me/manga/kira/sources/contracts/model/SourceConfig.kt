package me.manga.kira.sources.contracts.model

import kotlinx.serialization.Serializable

/**
 * Data-only, signed description of one or more sources. This is the wire format of the generic
 * sources subsystem: the bundled asset, the on-disk cache, and (later) the remote update all
 * (de)serialize to [SourceConfigDocument]. It contains NO executable code — only references, by
 * name, to generic strategies that the engine already ships (see `StrategyRegistry`). That is what
 * keeps "client-driven but dynamic" honest: configs can change behavior only within the envelope of
 * strategies the app was built with.
 *
 * Field shapes are intentionally permissive (maps of named specs) so the same model serves REST and
 * HTML sources without a per-family schema. Validation (`SourceConfigValidator`) rejects documents
 * that reference unknown strategies or omit required endpoints/fields.
 */
@Serializable
data class SourceConfigDocument(
    /** Bumped when the document shape changes incompatibly; the validator refuses unknown majors. */
    val schemaVersion: Int,
    /** ISO-8601 timestamp the document was produced (provenance only; not trusted for ordering). */
    val generatedAt: String? = null,
    /** Monotonic content revision — higher wins when merging cached vs. bundled vs. remote. */
    val revision: Long = 0,
    val sources: List<SourceConfig> = emptyList(),
)

/**
 * One source. [api] is its immutable identity across manifests, local data, and backups.
 *
 * The shipping registry executes only `"generic"`. Compatibility parsing may recognize older
 * engine values, but a v2 manifest containing one is rejected and no adapter is created.
 */
@Serializable
data class SourceConfig(
    val api: String,
    val language: String,
    val displayName: String = api,
    val baseUrl: String,
    val imageBase: String = "",
    /**
     * First-seed enablement (retirement §2): the catalog sync seeds a NEW `sources` row with this
     * value (false — the default — means user/onboarding opt-in, the classic posture); the user's
     * toggle owns the field afterwards. The sync never writes `isEnabled` for an existing row
     * except the [lifecycle] kill switches. Historical note: the field predates the retirement
     * with default `true` and zero readers; the default flipped to `false` when the sync started
     * honoring it, keeping every already-shipped document's seeding behavior identical.
     */
    val enabled: Boolean = false,
    /** Display order hint; signed v2 manifests replace it with their contiguous entry order. */
    val priority: Int = 0,
    /**
     * Execution engine. The `"legacy"` default exists only to parse archived descriptors; bundled
     * and signed v2 catalogs reject every value except `"generic"`.
     */
    val engine: String = "legacy",
    /** Declared minimum app version. Reserved for a Stage-1 version gate; NOT enforced by the engine yet. */
    val minAppVersion: String? = null,
    /** Static headers merged under any per-source captured headers (referer/UA/etc.). */
    val headers: Map<String, String> = emptyMap(),
    /**
     * Whether the engine reads the per-api captured-header store (cookies/UA/Cloudflare clearance)
     * for each request. Default true. Set false for a source whose API serves directly with no auth
     * (e.g. Azora) — avoids a needless header-store read (and its log noise) on every fetch.
     */
    val usesCapturedHeaders: Boolean = true,
    val pagination: PaginationSpec = PaginationSpec(),
    /** Keyed by verb: `home`,`featured`,`search`,`details`,`chapters`,`pages`. */
    val endpoints: Map<String, EndpointSpec> = emptyMap(),
    /** Keyed by dotted field path, e.g. `item.title`, `chapter.url`, `page.image`. */
    val fields: Map<String, FieldSpec> = emptyMap(),
    /**
     * Genres to suppress, for parity with legacy sources that drop blacklisted items. Applied by the
     * engine on every list verb (`GenericSourceClient.isBlacklistedByGenre`, covered by a golden
     * test); bundled pilots such as Zazamanga and Tapas rely on it.
     */
    val blacklistGenres: List<String> = emptyList(),
    /**
     * Source-lifecycle metadata. Defaults preserve parsing compatibility with archived documents;
     * the signed manifest is authoritative for remote lifecycle and ordering.
     *
     * This field — operational status (R5): projected into the `sources` row's siteState by the
     * catalog sync (drives the Home-tab maintenance/stopped states). One of `"WORKING"`,
     * `"UNDER_MAINTENANCE"`, `"STOPPED"`, `"ADULT_18_PLUS"` — mirrors the persisted `SourceState`
     * enum; the validator rejects anything else. Default `"WORKING"`: config is the authority,
     * absence means healthy.
     */
    val siteState: String = "WORKING",
    /**
     * Lifecycle (R6) — replaces the retired endpoint's `delate` flag. `"active"` → normal;
     * `"disabled"` → the row is force-disabled every sync (kept on disk for saved-entry reads);
     * `"removed"` → the `sources` row is deleted (saved-library rows untouched — endpoint
     * `shouldDelete` parity) and is never re-seeded. Default `"active"`.
     */
    val lifecycle: String = "active",
    /**
     * Bare hosts (no scheme/path, e.g. `"azoramoon.co"`) this source previously lived on (R3/R7).
     * The catalog sync rewrites any stored manga/chapter/history/notification URL whose host is in
     * this list to [baseUrl] (selective + idempotent), and these hosts join the deep-link trust
     * set. Append-only history; list only hosts with real past moves — empty costs nothing.
     */
    val previousHosts: List<String> = emptyList(),
    /** [previousHosts] sibling for the IMAGE host family — stored cover URLs migrate to [imageBase] (R4). */
    val previousImageHosts: List<String> = emptyList(),
    /**
     * Extra bare hosts trusted for this api in the push deep-link gate beyond
     * baseUrl/imageBase/[previousHosts] (e.g. an image CDN on an unrelated domain) (R7).
     * Trust-only — never drives migration.
     */
    val trustedHosts: List<String> = emptyList(),
    /**
     * Optional icon descriptor (MangaSource decoupling, 2026-07). Render-only metadata — icon
     * failures never affect discovery, enabling, or routing. Omitted → the UI renders a
     * deterministic initials avatar derived from [displayName]/[api].
     */
    val icon: IconSpec? = null,
    /**
     * Ordered advanced-filter declarations (config-driven filters, 2026-07 —
     * docs/sources/CONFIG_DRIVEN_FILTERS_PLAN.md). Declaration order IS both the UI order and the
     * request-composition order. Absent → the source has no advanced filters (plain search only).
     * Additive-optional: schemaVersion stays 1 and pre-filter parsers ignore the key.
     */
    val filters: List<FilterDefinition> = emptyList(),
)

/**
 * How a source's brand icon is resolved, in strict order: a packaged drawable looked up by
 * [resourceKey] in the app's icon registry wins; otherwise [remoteUrl] is loaded through the
 * app's existing image stack; otherwise the deterministic fallback avatar renders. JSON carries
 * only the stable string key, never a generated Kotlin resource identifier.
 */
@Serializable
data class IconSpec(
    /** Key into the packaged-drawable registry (`[a-z0-9_]{1,64}`), or empty for none. */
    val resourceKey: String = "",
    /** Absolute HTTPS icon URL, or empty for none. Rejected at validation unless https. */
    val remoteUrl: String = "",
)

/**
 * How successive pages are addressed. Only [start] is consumed by the engine today — paging is
 * template-driven through `{page}`/`{pageOffset}` placeholders in endpoint URLs (and
 * `EndpointSpec.pageParam` for chapter paging), not by appending [param]. [type] is validated
 * against the `page-number` whitelist; [param] is currently unread, reserved for the engine to
 * grow into.
 */
@Serializable
data class PaginationSpec(
    val type: String = "page-number",
    val param: String = "page",
    val start: Int = 1,
)

/**
 * One request template + how to find the result list in the response. [url] is a template with
 * `{baseUrl}`, `{imageBase}`, `{page}`, `{query}`, `{itemUrl}`, `{chapterUrl}`, `{id}` placeholders.
 * For JSON responses [root] is the JSONPath to the array; for HTML [listSelector] is the CSS
 * selector matching each item element.
 */
@Serializable
data class EndpointSpec(
    val url: String,
    val method: String = "get",
    /** `"json"` | `"html"` | `"script-json"` | `""` (infer: html iff [listSelector] is set, else json). */
    val format: String = "",
    /** For `format = "script-json"`: the `<script id="…">` whose JSON body is parsed (default `__NEXT_DATA__`). */
    val scriptId: String = "",
    /**
     * JSONPath to the result array. May be COMMA-separated candidates (`"webp_pages,pages"`) — the engine
     * picks the first whose array is non-empty (coalesce). [rootDirs], if set, pairs a token with each
     * candidate, surfaced to field templates as the `{root:__dir}` var (e.g. `hq_webp` vs `hq`).
     */
    val root: String = "",
    /** Per-candidate tokens paired positionally with the comma-split [root]; exposed as `{root:__dir}`. */
    val rootDirs: List<String> = emptyList(),
    val listSelector: String = "",
    val formBody: Map<String, String> = emptyMap(),
    /** Templated request body for `method = "post-json"` (placeholders expanded like [url]). */
    val jsonBody: String = "",
    /** Predicates that keep/drop list items after extraction (e.g. drop novels). All must pass. */
    val listFilters: List<FilterSpec> = emptyList(),
    /**
     * For a paginated `chapters` endpoint: the URL var the engine increments per page (e.g. `"page"`).
     * When set, `details()` loops the endpoint `{pageParam}=1,2,…`, concatenating chapters until
     * [lastPageLocator] reports no more. Empty → single fetch.
     */
    val pageParam: String = "",
    /**
     * Locator (CSS/JSONPath) read from each chapters page to decide whether to keep paging: a numeric
     * set (e.g. pagination links → max page; loop while page < max) OR a `true`/`false` flag (e.g. a
     * JSON `has_next`; loop while true).
     */
    val lastPageLocator: String = "",
)

/**
 * A predicate applied to each extracted list item. [path] is a JSONPath (or CSS-text locator) read from
 * the item; [op] compares it; [mode] decides whether a match keeps (`include`) or drops (`exclude`) the item.
 */
@Serializable
data class FilterSpec(
    val path: String,
    /** `equals` | `notEquals` | `contains` | `notNull` | `isNull`. */
    val op: String,
    val value: String = "",
    /** `include` (keep when the predicate matches) | `exclude` (drop when it matches). */
    val mode: String = "exclude",
)

/**
 * How to extract one field from a list item or a detail/page response. Exactly one of [path]
 * (JSONPath) or [selector] (CSS) is the primary locator; the other family's fields are ignored.
 * [transform] applies an ordered chain of named, generic transforms (coalesce, regex-replace,
 * enum-map, strip-html, date-parse, …) that the engine resolves by name.
 */
@Serializable
data class FieldSpec(
    val path: String = "",
    val selector: String = "",
    val attr: String = "text",
    val fallbackPath: String = "",
    val fallbackSelectors: List<String> = emptyList(),
    val lazyAttrChain: List<String> = emptyList(),
    val template: String = "",
    val vars: Map<String, String> = emptyMap(),
    val listPath: String = "",
    val listSelector: String = "",
    val imageStrategy: String = "",
    val dateStrategy: String = "",
    val transform: List<TransformSpec> = emptyList(),
)

/** A single named transform step. [fn] names a generic transform in the engine's registry. */
@Serializable
data class TransformSpec(
    val fn: String,
    val args: Map<String, String> = emptyMap(),
    val list: List<String> = emptyList(),
)

/**
 * One user-facing search filter, fully declarative (config-driven filters, 2026-07). Runtime
 * behavior binds ONLY to [id] and [FilterOptionSpec.value] — labels are display-only and safe to
 * rename. Standard ids (`genres`, `sort`, `status`, `language`, `type`) are conventions the
 * validator type-checks and the UI localizes; they run through the exact same pipeline as any
 * custom filter. No expression language: the request contribution is fixed by [request] plus the
 * deterministic composition algorithm in the engine (`FilterRequestComposer`).
 */
@Serializable
data class FilterDefinition(
    /** Stable identity, `[a-z0-9_]{1,64}`. Renaming a shipped id is a retire + re-add. */
    val id: String,
    /** Display label (required non-blank). Standard ids may be re-titled by localized UI headers. */
    val label: String,
    /** `"select"` | `"multiselect"` | `"toggle"` | `"text"` | `"number"` (`range`/`date` reserved). */
    val type: String,
    /** Required for select/multiselect; forbidden for toggle/text/number. */
    val options: List<FilterOptionSpec> = emptyList(),
    /** Scalar default: a select option value, toggle `"true"`/`"false"`, or a text/number literal. */
    val default: String = "",
    /** Multiselect default (option values). Mutually exclusive with [default]. */
    val defaults: List<String> = emptyList(),
    /** Must always reach the request → the validator demands a usable default. */
    val required: Boolean = false,
    val request: FilterRequestSpec,
    /** ALL conditions must hold; a hidden filter is neither rendered nor sent. */
    val visibleWhen: List<FilterConditionSpec> = emptyList(),
    /**
     * Multiselect only: marks this filter as the exclusion counterpart of filter [excludeOf]
     * (include/exclude pairs, e.g. included vs excluded tags). A value selected on both sides is
     * dropped from THIS (exclude) side — include wins, deterministically.
     */
    val excludeOf: String = "",
    /** Endpoint verbs the filter is sent with. v1 whitelist: `search` only. */
    val appliesTo: List<String> = listOf("search"),
)

/** One selectable option. [value] is the backend value that reaches the request; stable like an id. */
@Serializable
data class FilterOptionSpec(
    val value: String,
    /** Display label; defaults to [value]. */
    val label: String = "",
)

/**
 * How a filter's effective value is injected into the request. Targets `query`/`form`/`header`
 * append parameters; `path`/`body-json` fill a `{param}` placeholder that must exist in the
 * endpoint's url/jsonBody template (placeholders cannot be omitted, so [omitIfEmpty] applies only
 * to the appending targets).
 */
@Serializable
data class FilterRequestSpec(
    /** `"query"` | `"path"` | `"form"` | `"header"` | `"body-json"`. */
    val target: String,
    /**
     * query/form/header: the parameter name (may be `"genre[]"` — percent-encoded on the wire for
     * query). path/body-json: the template placeholder name (`[a-zA-Z0-9_]+`, must not shadow a
     * reserved engine var).
     */
    val param: String,
    /** `"single"` | `"csv"` | `"repeat"` (query/form) | `"json-array"` (body-json). */
    val encode: String = "single",
    /** Join separator for `csv`. */
    val delimiter: String = ",",
    /** query/form/header only: skip the parameter entirely when the effective value is empty. */
    val omitIfEmpty: Boolean = true,
    /** Toggle only: value sent when on. */
    val trueValue: String = "true",
    /** Toggle only: value when off. `""` + [omitIfEmpty] ⇒ the parameter is absent when off. */
    val falseValue: String = "",
)

/** Visibility condition: holds when the referenced filter's effective value intersects [anyOf]. */
@Serializable
data class FilterConditionSpec(
    val filter: String,
    val anyOf: List<String>,
)
