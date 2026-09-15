import java.nio.file.Files
import java.nio.file.LinkOption
import java.security.MessageDigest

pluginManagement {
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
        gradlePluginPortal()
    }
}

// Native Gradle verification owns artifact/descriptor checks; do not replace it with a cache scan.
// The reviewed metadata and generated lock state are added only after the real release graph is
// captured on its supported runtimes. Until then the release bootstrap deliberately fails closed.
check(gradle.startParameter.dependencyVerificationMode.name == "STRICT") {
    "Kira builds require strict Gradle dependency verification"
}
gradle.beforeProject {
    dependencyLocking {
        lockAllConfigurations()
        lockMode.set(org.gradle.api.artifacts.dsl.LockMode.STRICT)
    }
    // Register before project evaluation, including the root plugin classpath. A late allprojects
    // block would miss root buildscript resolution and give incomplete plugin lock coverage.
    buildscript.configurations.configureEach {
        resolutionStrategy.activateDependencyLocking()
    }
}

// Source-bound Android candidate only; this integrity check is not runtime qualification.
// Check the exact AAR and its inspected POM before Gradle can resolve this local module.
val avifCandidateDirectory =
    file("platform/vendor/avif/maven/org/aomedia/avif/android/avif/1.3.0.841110fd-kira-limits1")
val avifCandidateInputs =
    mapOf(
        "avif-1.3.0.841110fd-kira-limits1.aar" to
            (3002110L to "3fb46514a771c9d8efc38192d80e69031021c3b6f7f5f09a154131ca083c5888"),
        "avif-1.3.0.841110fd-kira-limits1.pom" to
            (1247L to "b8190c2b737308dd81d1adcf362c64e26179a6d0913e9c9ed0a84a40091b0400"),
    )
avifCandidateInputs.forEach { (name, expected) ->
    val input = avifCandidateDirectory.resolve(name)
    check(Files.isRegularFile(input.toPath(), LinkOption.NOFOLLOW_LINKS) && input.length() == expected.first) {
        "Missing or changed pinned AVIF candidate input: $name"
    }
    val digest = MessageDigest.getInstance("SHA-256").digest(input.readBytes()).joinToString("") { byte ->
        (byte.toInt() and 0xff).toString(16).padStart(2, '0')
    }
    check(digest == expected.second) { "Pinned AVIF candidate checksum mismatch: $name" }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        // Only this Android module is local. Never fall back to the baseline AAR or
        // use this repository for unrelated dependencies; POM-only metadata is hash-pinned above.
        exclusiveContent {
            forRepository {
                maven {
                    name = "KiraAvifCandidate"
                    url = uri("platform/vendor/avif/maven")
                    metadataSources {
                        mavenPom()
                        ignoreGradleMetadataRedirection()
                    }
                }
            }
            filter {
                includeModule("org.aomedia.avif.android", "avif")
            }
        }
        google()
        mavenCentral()
        if (providers.gradleProperty("kiraUseMavenLocal").orNull == "true") {
            mavenLocal {
                content {
                    includeGroup("me.manga.kira.source")
                }
            }
        }
        maven("https://maven.pkg.github.com/kira-manga/kira-source-engine") {
            credentials {
                username =
                    providers.environmentVariable("KIRA_PACKAGES_USER").orNull
                        ?: providers.environmentVariable("GITHUB_ACTOR").orNull
                password =
                    providers.environmentVariable("KIRA_PACKAGES_READ_TOKEN").orNull
                        ?: providers.environmentVariable("MOBILE_RELEASE_PROJECT_READ_TOKEN").orNull
                        ?: providers.environmentVariable("GITHUB_TOKEN").orNull
            }
            content {
                includeGroup("me.manga.kira.source")
            }
        }
        maven("https://jitpack.io") {
            content {
                // JitPack may only serve its own com.github.* coordinates. Without this filter it
                // also answers for ANY GitHub project's group: it auto-builds tags on demand and
                // serves a synthetic root POM at io.github.<user>:<repo>:<tag> that hard-depends
                // on every module (Android AAR included) — which SHADOWED a real Maven Central
                // io.github.* release while Central was still propagating it, poisoning every
                // iOS-target resolve with androidJvm variants (seen 2026-07-06 with
                // io.github.apdelrahman1911:nativecomposekit:0.3.0). Central is listed first,
                // but first-resolve races + descriptor caching make ordering alone insufficient.
                includeGroupByRegex("com\\.github\\..*")
            }
        }
        // KCEF (Desktop JCEF wrapper) — JOGL native bindings are hosted at jogamp.org and are not
        // mirrored to Maven Central. Required for the `dev.datlag:kcef` transitive `org.jogamp.*`
        // deps used by Phase 14.x Desktop WebViewHost.
        maven("https://jogamp.org/deployment/maven")
    }
}

rootProject.name = "yami-kmp"

include(":app")
include(":composeApp")
include(":desktopApp")

// Architecture rework modules (introduced 2026-05-25 on branch architecture-rework).
// New modules are added empty, then existing code is migrated into them per the contract's
// Build Order. The legacy :shared / :composeApp modules keep working until the migration
// for each subsystem is complete.
include(":core")
include(":domain")
include(":data")
// :data:local — Room persistence foundation extracted from :shared (strangler-fig Phase 1). A leaf
// (deps only :core) that both :shared and :data depend DOWN onto, which avoids the :shared <-> :data
// cycle a direct Room-in-:data move would create.
include(":data:local")
// :data:remote — Ktor transport (HttpClientFactory + ApiClient) extracted from :shared (strangler
// Phase 2). A leaf (deps only :core) both :shared and :data depend DOWN onto — same acyclic pattern
// as :data:local.
include(":data:remote")
// :data:download — the legacy chapter-download engine extracted from :shared (strangler-fig Phase 4).
// Per-target impls (Android WorkManager / iOS URLSession / non-Android coroutine queue) behind a
// commonMain DownloadRepository interface + downloadModule() Koin bindings. Depends DOWN onto
// :data:local/:sources:legacy/:platform and TRANSITIONALLY onto :shared (legacy Library/Sources
// repos) — acyclic because :shared holds zero download references after the binding move.
include(":data:download")
include(":platform")
include(":presentation")
include(":ui")

// Generic sources subsystem (Stage-0, 2026-06). Isolated behind :sources:contracts so the rest of
// the app depends on stable interfaces only. :engine = execution; :config = remote update; the two
// never depend on each other (both meet at :contracts).
include(":sources:contracts")
include(":sources:engine")
include(":sources:config")
// :sources:legacy — the ~50 hand-written per-source scrapers extracted from :shared (strangler
// Phase 3). Depends DOWN on :core/:domain/:data:local/:data:remote/:platform (NOT on :contracts/:engine
// — the legacy scrapers predate the generic-sources API). Both :shared and :data depend down onto it.
include(":sources:legacy")
