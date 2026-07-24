package me.manga.kira.source.contracts

/**
 * Platform-neutral execution surface shared by mobile runtime and backend previews.
 */
interface SourceEngine {
    val api: String

    suspend fun home(page: Int): SourceEngineResult<List<SourceListItem>>

    suspend fun featured(page: Int): SourceEngineResult<List<SourceFeaturedItem>>

    suspend fun search(
        query: String,
        page: Int,
        filters: SourceFilterSelections = SourceFilterSelections.EMPTY,
    ): SourceEngineResult<List<SourceListItem>>

    suspend fun details(manga: SourceMangaRef): SourceEngineResult<SourceDetails>

    suspend fun pages(
        manga: SourceMangaRef,
        chapter: SourceChapter,
    ): SourceEngineResult<List<SourcePage>>
}
