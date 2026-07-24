package me.manga.kira.sources.contracts

import kotlinx.datetime.LocalDate

/**
 * Network boundary used by the shared engine. Implementations are supplied by the app and backend;
 * the engine itself never depends on a concrete HTTP client.
 */
fun interface SourceTransport {
    suspend fun execute(request: SourceRequest): SourceResponse
}

enum class SourceHttpMethod {
    GET,
    POST_FORM,
    POST_JSON,
}

data class SourceRequest(
    val url: String,
    val method: SourceHttpMethod = SourceHttpMethod.GET,
    val headers: Map<String, String> = emptyMap(),
    val formBody: List<Pair<String, String>>? = null,
    val jsonBody: String? = null,
)

data class SourceResponse(
    val status: Int,
    val body: String,
    val headers: Map<String, String> = emptyMap(),
    val finalUrl: String? = null,
)

fun interface SourceHeaderProvider {
    suspend fun headersFor(api: String): Map<String, String>
}

fun interface SourceBaseUrlProvider {
    suspend fun baseUrlFor(api: String): String?
}

fun interface SourceChallengeListener {
    fun onChallenge(api: String, url: String)
}

data class SourceFilterSelections(
    val byId: Map<String, List<String>> = emptyMap(),
) {
    companion object {
        val EMPTY = SourceFilterSelections()
    }
}

sealed interface SourceEngineResult<out T> {
    data class Success<T>(val value: T) : SourceEngineResult<T>

    data class Failure(val error: SourceEngineError) : SourceEngineResult<Nothing>
}

sealed interface SourceEngineError {
    data class Required(val field: String) : SourceEngineError

    data class Http(val status: Int) : SourceEngineError

    data object NoConnectivity : SourceEngineError

    data object Timeout : SourceEngineError

    data object InvalidResponse : SourceEngineError

    data class Unexpected(val category: String) : SourceEngineError
}

data class SourceListItem(
    val api: String,
    val language: String,
    val title: String,
    val url: String,
    val coverUrl: String,
    val rating: Int?,
    val genres: List<String>,
    val recentChapters: List<SourceChapterRef> = emptyList(),
)

data class SourceChapterRef(
    val number: String,
    val url: String,
)

data class SourceFeaturedItem(
    val api: String,
    val language: String,
    val title: String,
    val url: String,
    val coverUrl: String,
)

data class SourceMangaRef(
    val api: String,
    val language: String,
    val title: String,
    val url: String,
    val coverUrl: String,
)

data class SourceDetails(
    val api: String,
    val language: String,
    val title: String,
    val url: String,
    val coverUrl: String,
    val description: String,
    val author: String,
    val rating: String,
    val status: String,
    val genres: List<String>,
    val chapters: List<SourceChapter>,
)

data class SourceChapter(
    val number: String,
    val name: String,
    val url: String,
    val date: LocalDate?,
)

data class SourcePage(
    val url: String,
    val headers: Map<String, String> = emptyMap(),
)
