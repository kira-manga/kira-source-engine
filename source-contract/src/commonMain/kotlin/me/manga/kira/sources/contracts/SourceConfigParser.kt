package me.manga.kira.source.contracts

import kotlinx.serialization.json.Json
import me.manga.kira.source.contracts.model.SourceConfig
import me.manga.kira.source.contracts.model.SourceConfigDocument

/**
 * Compatibility parser for trusted bundled/cached source JSON. Strict administrative authoring and
 * canonicalization remain backend responsibilities.
 */
object SourceConfigParser {
    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
    }

    fun parse(raw: String): SourceEngineResult<SourceConfigDocument> =
        decode { json.decodeFromString(SourceConfigDocument.serializer(), raw) }

    fun parseSource(raw: String): SourceEngineResult<SourceConfig> =
        decode { json.decodeFromString(SourceConfig.serializer(), raw) }

    private inline fun <T> decode(block: () -> T): SourceEngineResult<T> =
        try {
            SourceEngineResult.Success(block())
        } catch (_: Throwable) {
            SourceEngineResult.Failure(SourceEngineError.InvalidResponse)
        }
}
