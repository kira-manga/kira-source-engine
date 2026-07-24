package me.manga.kira.sources.engine

import me.manga.kira.sources.contracts.StrategyRegistry
import me.manga.kira.sources.engine.internal.DateStrategies
import me.manga.kira.sources.engine.internal.Transforms

/**
 * The set of generic strategies THIS build ships. The validator consults it to reject any config
 * that references a strategy the binary doesn't contain — the guarantee that a (signed) data-only
 * config can never introduce new executable behavior. Extending the engine = adding a strategy here
 * (and its implementation); configs then reference it by name.
 */
class DefaultStrategyRegistry : StrategyRegistry {

    // Stage-0 image extraction is expressed via FieldSpec.attr / lazyAttrChain (both consumed by the
    // engine), NOT via a named image strategy. The set is intentionally empty so the validator REJECTS
    // any config that references an imageStrategy the engine does not yet implement (fail-closed),
    // rather than silently accepting it and falling back to attr="text" (empty for <img>). Named image
    // strategies (data-src/lazy/abs) are a Stage-1 addition with their own golden fixtures.
    private val image = emptySet<String>()

    private val pagination = setOf("page-number")

    override fun hasTransform(name: String): Boolean = name in Transforms.NAMES

    override fun hasImageStrategy(name: String): Boolean = name in image

    override fun hasDateStrategy(name: String): Boolean = name in DateStrategies.NAMES

    override fun hasPagination(name: String): Boolean = name in pagination
}
