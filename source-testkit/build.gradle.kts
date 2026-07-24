import org.jetbrains.kotlin.gradle.ExperimentalKotlinGradlePluginApi
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.kotlin.multiplatform)
    alias(libs.plugins.android.kotlin.multiplatform.library)
    alias(libs.plugins.kotlin.serialization)
    `maven-publish`
}

kotlin {
    android {
        namespace = "me.manga.kira.source.testkit"
        compileSdk = 37
        minSdk = 26
        withHostTest {}
        @OptIn(ExperimentalKotlinGradlePluginApi::class)
        compilerOptions.jvmTarget.set(JvmTarget.JVM_11)
    }

    jvm()
    iosArm64()
    iosSimulatorArm64()
    applyDefaultHierarchyTemplate()

    sourceSets {
        commonMain.dependencies {
            api(project(":source-contract"))
            api(project(":source-engine"))
            implementation(libs.kotlinx.serialization.json)
        }
        commonTest.dependencies {
            implementation(kotlin("test"))
            implementation(libs.kotlinx.coroutines.test)
        }
    }
}

publishing {
    repositories {
        maven {
            name = "GitHubPackages"
            url = uri("https://maven.pkg.github.com/kira-manga/kira-source-engine")
            credentials {
                username = System.getenv("GITHUB_ACTOR") ?: System.getenv("KIRA_PACKAGES_USER")
                password = System.getenv("GITHUB_TOKEN") ?: System.getenv("KIRA_PACKAGES_READ_TOKEN")
            }
        }
    }
}
