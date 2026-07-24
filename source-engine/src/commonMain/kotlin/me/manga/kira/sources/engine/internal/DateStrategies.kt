package me.manga.kira.sources.engine.internal

import kotlinx.datetime.LocalDate
import kotlinx.datetime.TimeZone
import kotlinx.datetime.toLocalDateTime
import kotlin.time.Instant

/**
 * Named, generic chapter-date parsers. Stage-0 ships the unambiguous, locale-free strategies; the
 * fuzzy locale/relative ("2 days ago") parsers the legacy sources carry are deliberately deferred to
 * a later stage so each can be added with its own golden fixtures rather than guessed at now.
 */
internal object DateStrategies {
    val NAMES: Set<String> = setOf("iso", "epoch-seconds", "epoch-millis")

    fun parse(raw: String, strategy: String): LocalDate? {
        val value = raw.trim()
        if (value.isEmpty()) return null
        return when (strategy) {
            "epoch-seconds" -> value.toLongOrNull()?.let { epochToDate(it * 1000) }
            "epoch-millis" -> value.toLongOrNull()?.let { epochToDate(it) }
            // default: ISO-8601 date or date-time prefix (e.g. "2024-01-15" / "2024-01-15T10:00:00Z")
            else -> runCatching { LocalDate.parse(value.substringBefore('T')) }.getOrNull()
        }
    }

    private fun epochToDate(millis: Long): LocalDate? =
        runCatching { Instant.fromEpochMilliseconds(millis).toLocalDateTime(TimeZone.UTC).date }.getOrNull()
}
