package me.manga.kira.sources.contracts

/**
 * The governance surface over the generic strategies the engine ships. A config may only reference
 * strategies by name; this registry answers "is that name known to this build?" so the validator can
 * reject a config that asks for a capability the binary does not contain. That check is the whole
 * point of the data-only-config model: a remote document can never introduce new executable behavior,
 * only re-wire behaviors already compiled in.
 *
 * Categories mirror the kinds of named references a [model.SourceConfig] can hold. The engine's
 * concrete registry (`:sources:engine`) both implements this query interface and performs the actual
 * dispatch; `:sources:contracts` exposes only the query surface so `:data` can validate without
 * depending on execution.
 */
interface StrategyRegistry {
    fun hasTransform(name: String): Boolean

    fun hasImageStrategy(name: String): Boolean

    fun hasDateStrategy(name: String): Boolean

    fun hasPagination(name: String): Boolean
}
