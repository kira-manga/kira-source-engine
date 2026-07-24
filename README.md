# Kira Source Engine

Platform-neutral Kotlin Multiplatform contracts and execution for Kira’s config-driven manga sources.

## Modules

- `source-contract` — signed configuration models, transport ports, validation contracts, and neutral results.
- `source-engine` — deterministic generic request composition and extraction.
- `source-testkit` — reusable golden fixtures and parity assertions.

The mobile app and backend pin the same immutable package version. Networking is supplied through a port, and the backend alone owns canonical JSON, source publication, checksums, and signatures.

The package is built with Kotlin 2.2 while its public API and language level are capped at Kotlin
2.1. This is intentional: the Spring backend uses Kotlin 2.1 and the mobile app uses Kotlin 2.4,
so both consumers can read the same published metadata without skipping compiler checks.

## Verification

```bash
ANDROID_HOME="$HOME/Library/Android/sdk" ./gradlew check
ANDROID_HOME="$HOME/Library/Android/sdk" ./gradlew -PVERSION_NAME=0.1.0 publishToMavenLocal
```

Release tags use the exact immutable Maven version: tag `v0.1.0` publishes version `0.1.0`.
Consumers must pin that version rather than use snapshots or dynamic selectors.
