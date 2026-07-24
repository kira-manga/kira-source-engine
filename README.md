# Kira Source Engine

Platform-neutral Kotlin Multiplatform contracts and execution for Kira’s config-driven manga sources.

## Modules

- `source-contract` — signed configuration models, transport ports, validation contracts, and neutral results.
- `source-engine` — deterministic generic request composition and extraction.
- `source-testkit` — reusable golden fixtures and parity assertions.

The mobile app and backend pin the same immutable package version. Networking is supplied through a port, and the backend alone owns canonical JSON, source publication, checksums, and signatures.

## Verification

```bash
./gradlew check publishToMavenLocal
```

Release tags use the exact immutable Maven version: tag `v0.1.0` publishes version `0.1.0`.
Consumers must pin that version rather than use snapshots or dynamic selectors.
