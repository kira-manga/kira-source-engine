# Repository Guidelines

## Project Structure

This repository owns the platform-neutral Kira source contract and generic execution engine:

- `source-contract/` contains serializable source configuration, transport ports, result models, and validation contracts.
- `source-engine/` interprets validated generic configuration. It must not depend on Ktor, Spring, Android APIs, or app-domain models.
- `source-testkit/` contains reusable golden fixtures and parity assertions for the app and backend.

The Kira backend remains the authority for `kcj-1` canonical JSON, checksums, signatures, and publication.

## Build and Test

Run commands from the repository root:

```bash
./gradlew check
./gradlew jvmTest
./gradlew publishToMavenLocal
```

Use Java 21. Android compilation requires SDK 37. Do not publish a Maven version before all app/backend parity consumers pass.

## Style and Contracts

Use four-space Kotlin indentation and explicit immutable models. Keep public contracts data-only and backward compatible. A source configuration may reference only named strategies compiled into the engine. Never add arbitrary script execution, implicit legacy fallback, secrets, cookies, or platform-specific networking.

## Tests and Changes

Every engine capability needs a deterministic golden fixture. Test success, malformed input, missing fields, transport failures, and cancellation. Commit messages should describe behavior, for example `feat: add neutral source preview results`.

Pull requests must identify contract changes, app/backend compatibility impact, published coordinates, and verification commands. Never commit registry tokens, captured headers, production payloads, or private source responses.
