package me.manga.kira.sources.engine

import me.manga.kira.sources.contracts.SourceConfigParser
import me.manga.kira.sources.contracts.model.EndpointSpec
import me.manga.kira.sources.contracts.model.FieldSpec
import me.manga.kira.sources.contracts.model.IconSpec
import me.manga.kira.sources.contracts.model.SourceConfig
import me.manga.kira.sources.contracts.model.SourceConfigDocument
import me.manga.kira.sources.contracts.model.TransformSpec
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class DefaultSourceConfigValidatorTest {
    private val validator = DefaultSourceConfigValidator(DefaultStrategyRegistry())

    private fun genericSource(
        api: String = "s",
        engine: String = "generic",
        endpoints: Map<String, EndpointSpec> = mapOf("home" to EndpointSpec(url = "{baseUrl}/h", listSelector = "div")),
        fields: Map<String, FieldSpec> = emptyMap(),
    ) = SourceConfig(api = api, language = "en", baseUrl = "https://x.test", engine = engine, endpoints = endpoints, fields = fields)

    @Test
    fun the_golden_document_validates() {
        val document = (SourceConfigParser.parse(GOLDEN_CONFIG_JSON) as me.manga.kira.sources.contracts.SourceEngineResult.Success).value
        assertTrue(validator.validate(document).isValid)
    }

    @Test
    fun unsupported_schema_version_is_rejected() {
        val result = validator.validate(SourceConfigDocument(schemaVersion = 99))
        assertFalse(result.isValid)
        assertTrue(result.errors.single().contains("schemaVersion"))
    }

    @Test
    fun unknown_transform_is_rejected() {
        val doc =
            SourceConfigDocument(
                schemaVersion = 1,
                sources =
                    listOf(
                        genericSource(
                            fields =
                                mapOf(
                                    "item.title" to
                                        FieldSpec(
                                            selector = "h3",
                                            transform = listOf(TransformSpec(fn = "make-up-name")),
                                        ),
                                ),
                        ),
                    ),
            )
        val result = validator.validate(doc)
        assertFalse(result.isValid)
        assertTrue(result.errors.any { it.contains("make-up-name") })
    }

    @Test
    fun image_strategy_reference_is_rejected_because_engine_does_not_implement_it() {
        // Stage-0 image extraction is via attr/lazyAttrChain; the engine advertises no image strategies,
        // so a config referencing one must be rejected (fail-closed) rather than silently mis-extracting.
        val doc =
            SourceConfigDocument(
                schemaVersion = 1,
                sources =
                    listOf(
                        genericSource(
                            fields =
                                mapOf("item.cover" to FieldSpec(selector = "img.cover", imageStrategy = "data-src")),
                        ),
                    ),
            )
        val result = validator.validate(doc)
        assertFalse(result.isValid)
        assertTrue(result.errors.any { it.contains("image strategy") })
    }

    @Test
    fun unknown_engine_is_rejected() {
        val doc = SourceConfigDocument(schemaVersion = 1, sources = listOf(genericSource(engine = "wasm")))
        assertFalse(validator.validate(doc).isValid)
    }

    @Test
    fun duplicate_api_is_rejected() {
        val doc = SourceConfigDocument(schemaVersion = 1, sources = listOf(genericSource(api = "dup"), genericSource(api = "dup")))
        val result = validator.validate(doc)
        assertFalse(result.isValid)
        assertTrue(result.errors.any { it.contains("duplicate") })
    }

    @Test
    fun generic_source_without_home_or_popular_is_rejected() {
        val doc = SourceConfigDocument(schemaVersion = 1, sources = listOf(genericSource(endpoints = emptyMap())))
        assertFalse(validator.validate(doc).isValid)
    }

    @Test
    fun raw_query_var_in_a_url_is_rejected_but_encoded_variants_pass() {
        // Raw {query} in a URL breaks on the first space/&/quote in a search term — the validator
        // rejects it (fail-closed) and points at {queryEncoded}. The encoded/JSON variants must NOT
        // false-match ("{query}"'s closing brace pins the exact var name), and raw {query} stays
        // legitimate in formBody values (the executor form-encodes those — Madara configs use it).
        val bad =
            SourceConfigDocument(
                schemaVersion = 1,
                sources =
                    listOf(
                        genericSource(
                            endpoints =
                                mapOf(
                                    "home" to EndpointSpec(url = "{baseUrl}/h", listSelector = "div"),
                                    "search" to EndpointSpec(url = "{baseUrl}/s?q={query}", listSelector = "div"),
                                ),
                        ),
                    ),
            )
        val badResult = validator.validate(bad)
        assertFalse(badResult.isValid)
        assertTrue(badResult.errors.any { it.contains("{queryEncoded}") })

        val good =
            SourceConfigDocument(
                schemaVersion = 1,
                sources =
                    listOf(
                        genericSource(
                            endpoints =
                                mapOf(
                                    "home" to EndpointSpec(url = "{baseUrl}/h", listSelector = "div"),
                                    "search" to
                                        EndpointSpec(url = "{baseUrl}/s?q={queryEncoded}", listSelector = "div"),
                                    "details" to
                                        EndpointSpec(
                                            url = "{baseUrl}/d",
                                            method = "post-form",
                                            formBody = mapOf("vars[s]" to "{query}"),
                                            listSelector = "div",
                                        ),
                                ),
                        ),
                    ),
            )
        assertEquals(emptyList(), validator.validate(good).errors)
    }

    @Test
    fun raw_query_var_in_a_json_body_is_rejected() {
        // A raw quote/backslash in the search term would corrupt the JSON body — must be {queryJson}.
        val doc =
            SourceConfigDocument(
                schemaVersion = 1,
                sources =
                    listOf(
                        genericSource(
                            endpoints =
                                mapOf(
                                    "home" to EndpointSpec(url = "{baseUrl}/h", listSelector = "div"),
                                    "search" to
                                        EndpointSpec(
                                            url = "{baseUrl}/graphql",
                                            method = "post-json",
                                            jsonBody = """{"q":"{query}"}""",
                                            listSelector = "div",
                                        ),
                                ),
                        ),
                    ),
            )
        val result = validator.validate(doc)
        assertFalse(result.isValid)
        assertTrue(result.errors.any { it.contains("{queryJson}") })
    }

    @Test
    fun legacy_source_skips_strategy_checks() {
        // A legacy source carries no config behavior: no endpoints, references nothing -> still valid.
        val doc =
            SourceConfigDocument(
                schemaVersion = 1,
                sources =
                    listOf(
                        SourceConfig(api = "leg", language = "en", baseUrl = "https://x.test", engine = "legacy"),
                    ),
            )
        assertEquals(emptyList(), validator.validate(doc).errors)
    }

    // --- SourceRegistry retirement Phase 2: lifecycle metadata (siteState / lifecycle / hosts) ---

    @Test
    fun lifecycle_metadata_fields_parse_and_older_documents_default() {
        // Golden forward test: a document carrying the five new fields parses with the values…
        val withFields =
            (
                SourceConfigParser.parse(
                    """
                    {"schemaVersion":1,"sources":[{
                      "api":"Azora","language":"(AR)","baseUrl":"https://azoramoon.com",
                      "siteState":"UNDER_MAINTENANCE","lifecycle":"disabled",
                      "previousHosts":["azoramoon.co","azoraworld.com"],
                      "previousImageHosts":["oldimg.azora.net"],
                      "trustedHosts":["cdn.azora-images.net"]
                    }]}
                    """.trimIndent(),
                ) as me.manga.kira.sources.contracts.SourceEngineResult.Success
            ).value.sources.single()
        assertEquals("UNDER_MAINTENANCE", withFields.siteState)
        assertEquals("disabled", withFields.lifecycle)
        assertEquals(listOf("azoramoon.co", "azoraworld.com"), withFields.previousHosts)
        assertEquals(listOf("oldimg.azora.net"), withFields.previousImageHosts)
        assertEquals(listOf("cdn.azora-images.net"), withFields.trustedHosts)

        // …and a pre-Phase-2 document (no such fields) parses unchanged with the defaults.
        val withoutFields =
            (
                SourceConfigParser.parse(
                    """{"schemaVersion":1,"sources":[{"api":"Old","language":"en","baseUrl":"https://x.test"}]}""",
                ) as me.manga.kira.sources.contracts.SourceEngineResult.Success
            ).value.sources.single()
        assertEquals("WORKING", withoutFields.siteState)
        assertEquals("active", withoutFields.lifecycle)
        assertEquals(emptyList(), withoutFields.previousHosts)
        assertEquals(emptyList(), withoutFields.previousImageHosts)
        assertEquals(emptyList(), withoutFields.trustedHosts)
    }

    @Test
    fun unknown_siteState_is_rejected() {
        val doc = SourceConfigDocument(schemaVersion = 1, sources = listOf(genericSource().copy(siteState = "BROKEN")))
        val result = validator.validate(doc)
        assertFalse(result.isValid)
        assertTrue(result.errors.any { it.contains("siteState") && it.contains("BROKEN") })
    }

    @Test
    fun unknown_lifecycle_is_rejected() {
        val doc =
            SourceConfigDocument(schemaVersion = 1, sources = listOf(genericSource().copy(lifecycle = "archived")))
        val result = validator.validate(doc)
        assertFalse(result.isValid)
        assertTrue(result.errors.any { it.contains("lifecycle") && it.contains("archived") })
    }

    @Test
    fun host_list_entries_must_be_bare_hosts() {
        // Scheme'd, path'd, port-suffixed, and blank entries would silently never match a stored
        // URL's host (every matcher compares portless) — each must be rejected, naming the
        // offending field.
        val doc =
            SourceConfigDocument(
                schemaVersion = 1,
                sources =
                    listOf(
                        genericSource().copy(
                            previousHosts = listOf("https://azoramoon.co", "azoramoon.co:8080"),
                            previousImageHosts = listOf("img.azora.net/covers"),
                            trustedHosts = listOf(" "),
                        ),
                    ),
            )
        val result = validator.validate(doc)
        assertFalse(result.isValid)
        assertTrue(result.errors.any { it.contains("azoramoon.co:8080") })
        assertTrue(result.errors.any { it.contains("previousHosts") && it.contains("bare host") })
        assertTrue(result.errors.any { it.contains("previousImageHosts") && it.contains("bare host") })
        assertTrue(result.errors.any { it.contains("trustedHosts") && it.contains("bare host") })
    }

    @Test
    fun metadata_only_legacy_stanza_validates_and_bad_metadata_on_legacy_is_still_rejected() {
        // The Phase-4 shape: an engine="legacy" entry carrying ONLY lifecycle metadata must be
        // valid (no endpoints required)…
        val stanza =
            SourceConfig(
                api = "MangaLek",
                language = "(AR)",
                baseUrl = "https://lekmanga.net",
                engine = "legacy",
                siteState = "WORKING",
                lifecycle = "active",
                previousHosts = listOf("manga-lek.net", "mangalek.com"),
            )
        val valid = validator.validate(SourceConfigDocument(schemaVersion = 1, sources = listOf(stanza)))
        assertEquals(emptyList(), valid.errors)

        // …and the metadata rules run BEFORE the generic-only early return, so a bad value on a
        // legacy stanza is still caught.
        val bad =
            validator.validate(
                SourceConfigDocument(schemaVersion = 1, sources = listOf(stanza.copy(lifecycle = "deleted"))),
            )
        assertFalse(bad.isValid)
        assertTrue(bad.errors.any { it.contains("lifecycle") && it.contains("deleted") })
    }

    @Test
    fun valid_icon_descriptors_pass_packaged_remote_and_both() {
        val doc =
            SourceConfigDocument(
                schemaVersion = 1,
                sources =
                    listOf(
                        genericSource(api = "packaged").copy(icon = IconSpec(resourceKey = "azora_2")),
                        genericSource(api = "remote").copy(icon = IconSpec(remoteUrl = "https://cdn.test/icon.png")),
                        genericSource(api = "both").copy(
                            icon = IconSpec(resourceKey = "x", remoteUrl = "https://cdn.test/x.png"),
                        ),
                        genericSource(api = "none"),
                    ),
            )
        assertEquals(emptyList(), validator.validate(doc).errors)
    }

    @Test
    fun icon_with_cleartext_remote_url_is_rejected() {
        val doc =
            SourceConfigDocument(
                schemaVersion = 1,
                sources = listOf(genericSource().copy(icon = IconSpec(remoteUrl = "http://cdn.test/icon.png"))),
            )
        val result = validator.validate(doc)
        assertFalse(result.isValid)
        assertTrue(result.errors.any { it.contains("remoteUrl") && it.contains("https") })
    }

    @Test
    fun icon_with_malformed_resource_key_is_rejected() {
        val doc =
            SourceConfigDocument(
                schemaVersion = 1,
                sources = listOf(genericSource().copy(icon = IconSpec(resourceKey = "Res.drawable.ic_azora"))),
            )
        val result = validator.validate(doc)
        assertFalse(result.isValid)
        assertTrue(result.errors.any { it.contains("resourceKey") })
    }

    @Test
    fun empty_icon_block_is_rejected() {
        val doc =
            SourceConfigDocument(
                schemaVersion = 1,
                sources = listOf(genericSource().copy(icon = IconSpec())),
            )
        val result = validator.validate(doc)
        assertFalse(result.isValid)
        assertTrue(result.errors.any { it.contains("icon block is present but empty") })
    }
}
