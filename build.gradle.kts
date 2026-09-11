plugins {
    alias(libs.plugins.kotlin.multiplatform) apply false
    alias(libs.plugins.android.kotlin.multiplatform.library) apply false
    alias(libs.plugins.kotlin.serialization) apply false
}

allprojects {
    group = "me.manga.kira.source"
    version = providers.gradleProperty("VERSION_NAME").getOrElse("0.1.0-SNAPSHOT")

    plugins.withId("maven-publish") {
        tasks.named("publish") {
            doFirst {
                throw GradleException(
                    "Remote package publication is disabled (Engine6 current-source containment). " +
                        "Use publishToMavenLocal for local verification only."
                )
            }
        }
    }
}
