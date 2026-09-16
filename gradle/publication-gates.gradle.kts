import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import java.io.File
import java.io.IOException
import java.net.URI
import java.nio.charset.StandardCharsets.UTF_8
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.security.MessageDigest
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.time.format.ResolverStyle
import java.util.HexFormat
import java.util.Properties
import javax.xml.XMLConstants
import javax.xml.parsers.DocumentBuilderFactory
import org.gradle.api.GradleException
import org.gradle.api.Named
import org.gradle.api.Project
import org.gradle.api.Task
import org.gradle.api.artifacts.ProjectDependency
import org.gradle.api.artifacts.repositories.MavenArtifactRepository
import org.gradle.api.attributes.AttributeContainer
import org.gradle.api.component.ComponentWithCoordinates
import org.gradle.api.component.ComponentWithVariants
import org.gradle.api.component.SoftwareComponentVariant
import org.gradle.api.internal.component.SoftwareComponentInternal
import org.gradle.api.publish.PublishingExtension
import org.gradle.api.publish.internal.PublicationInternal
import org.gradle.api.publish.maven.MavenPublication
import org.gradle.api.publish.maven.internal.dependencies.MavenDependency
import org.gradle.api.publish.maven.internal.publication.MavenPomInternal
import org.gradle.api.publish.maven.tasks.GenerateMavenPom
import org.gradle.api.publish.maven.tasks.PublishToMavenLocal
import org.gradle.api.publish.maven.tasks.PublishToMavenRepository
import org.gradle.api.publish.tasks.GenerateModuleMetadata
import org.gradle.api.services.BuildService
import org.gradle.api.services.BuildServiceParameters
import org.gradle.internal.artifacts.repositories.AuthenticationSupportedInternal
import org.jetbrains.kotlin.gradle.dsl.KotlinMultiplatformExtension
import org.jetbrains.kotlin.gradle.plugin.KotlinBasePlugin
import org.w3c.dom.Element

// Stage A is local file publication only, not release approval or a sandbox for arbitrary
// Gradle/init code. Keep the native component, artifacts and publisher actions unchanged.
abstract class KiraPublicationByteState : BuildService<BuildServiceParameters.None> {
    @Volatile var restoredSeal: String? = null
    @Volatile var verifiedSeal: String? = null
    val guardedWriters = linkedSetOf<String>()
}

fun requireBytes(condition: Boolean, code: String, detail: String) {
    if (!condition) throw GradleException("KIRA_PUBLICATION_BYTES_$code: $detail")
}

fun sha256(bytes: ByteArray): String = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes))

fun xmlElements(element: Element): List<Element> =
    (0 until element.childNodes.length).mapNotNull { element.childNodes.item(it) as? Element }

fun xmlChildren(element: Element, name: String): List<Element> = xmlElements(element).filter { it.tagName == name }

fun exactXmlChildren(element: Element, names: Set<String>) {
    val children = xmlElements(element)
    requireBytes(children.size == names.size && children.map { it.tagName }.toSet() == names,
        "READBACK", "Unexpected or duplicate mutable-index XML elements.")
}

fun xmlValue(element: Element, name: String): String {
    val values = xmlChildren(element, name)
    requireBytes(values.size == 1 && xmlElements(values.single()).isEmpty(), "METADATA", "Expected exactly one scalar XML $name.")
    return values.single().textContent
}

fun xmlOptionalValue(element: Element, name: String): String? {
    val values = xmlChildren(element, name)
    requireBytes(values.size <= 1 && values.all { xmlElements(it).isEmpty() }, "METADATA", "Duplicate or non-scalar XML $name.")
    return values.singleOrNull()?.textContent
}

fun parseXml(file: File): Element {
    requireBytes(file.length() <= 4L * 1024 * 1024, "METADATA", "Descriptor exceeds the local parser bound.")
    val factory = DocumentBuilderFactory.newInstance()
    factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true)
    factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD, "")
    factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA, "")
    return file.inputStream().use { factory.newDocumentBuilder().parse(it).documentElement }
}

fun jsonMap(value: Any?): Map<*, *> {
    requireBytes(value is Map<*, *>, "METADATA", "Expected a JSON object.")
    return value as Map<*, *>
}

fun jsonList(value: Any?): List<*> {
    requireBytes(value is List<*>, "METADATA", "Expected a JSON list.")
    return value as List<*>
}

data class KiraPublication(
    val project: Project,
    val publication: MavenPublication,
    val pom: GenerateMavenPom,
    val metadata: GenerateModuleMetadata,
)

data class KiraPayload(
    val model: KiraPublication,
    val kind: String,
    val classifier: String,
    val extension: String,
    val repositoryPath: String,
    val output: File,
    val producers: List<Task>,
)

data class KiraFileFacts(val size: Long, val hashes: Map<String, String>)

data class KiraByteRow(val payload: KiraPayload, val kind: String, val path: String, val size: Long, val sha256: String) {
    fun line(): String = listOf(
        payload.model.project.path, payload.model.publication.name,
        payload.model.publication.groupId, payload.model.publication.artifactId, payload.model.publication.version,
        kind, path, size.toString(), sha256,
    ).joinToString("\t")
}

data class KiraBundle(val seal: String, val rows: List<KiraByteRow>, val files: Map<String, File>)

data class KiraGav(val group: String, val module: String, val version: String) {
    fun fields(): List<String> = listOf(group, module, version)
}

fun gav(publication: MavenPublication): KiraGav = KiraGav(publication.groupId, publication.artifactId, publication.version)

data class KiraTargetModel(val name: String, val platform: String, val attributes: Map<String, Any>)

data class KiraVariantModel(
    val name: String,
    val attributes: Map<String, Any>,
    val projectDependencies: List<KiraGav>,
    val availableAt: KiraGav? = null,
)

data class KiraPomEdge(
    val gav: KiraGav,
    val scope: String?,
    val optional: Boolean,
    val type: String,
    val classifier: String,
    val exclusions: List<List<String>>,
) {
    fun text(): String = JsonOutput.toJson(listOf(gav.fields(), scope, optional, type, classifier, exclusions))
}

data class KiraNativeModel(
    val componentName: String,
    val target: KiraTargetModel?,
    val variants: List<KiraVariantModel>,
    val pomDependencies: List<KiraPomEdge>,
    val pomManagement: List<KiraPomEdge>,
)

// Gradle9.6.1's wire types, not arbitrary object.toString() or supplied JSON authority.
fun nativeAttributes(attributes: AttributeContainer): Map<String, Any> {
    val keys = attributes.keySet().sortedBy { it.name }
    requireBytes(keys.map { it.name }.distinct().size == keys.size, "MODEL", "Duplicate native attribute names.")
    return keys.associate { key ->
        val value = attributes.getAttribute(key)
        val wire: Any = when (value) {
            is Boolean -> value
            is Int -> value
            is String -> value
            is Named -> value.name
            is Enum<*> -> value.name
            else -> throw GradleException("KIRA_PUBLICATION_BYTES_MODEL: Unsupported native attribute type: ${key.name}")
        }
        key.name to wire
    }
}

fun <T : Any> modelValue(value: T?, detail: String): T =
    value ?: throw GradleException("KIRA_PUBLICATION_BYTES_MODEL: $detail")

val byteMode = providers.gradleProperty("kiraPublicationMode").orNull
if (byteMode == null) {
    // Destination removal remains the ordinary build's boundary; even an accidentally added
    // repository must not make aggregate publish dependencies write before its late refusal.
    gradle.taskGraph.whenReady {
        requireBytes(allTasks.none { it is PublishToMavenRepository }, "MODE", "Native repository publication requires the explicit owned-file mode.")
    }
} else {
    requireBytes(byteMode in setOf("export", "promote"), "MODE", "Only export and promote owned-file modes exist.")
    requireBytes(gradle.gradleVersion == "9.6.1", "TOOLCHAIN", "The local byte binding requires Gradle9.6.1.")
    requireBytes(JavaVersion.current() == JavaVersion.VERSION_21, "TOOLCHAIN", "The local byte binding requires Java21.")
    requireBytes(gradle.startParameter.isOffline, "OFFLINE", "Owned-file byte work requires offline mode; do not fetch missing prerequisites.")
    @Suppress("DEPRECATION")
    val configurationCacheRequested = gradle.startParameter.isConfigurationCacheRequested
    requireBytes(!configurationCacheRequested, "CACHE", "The invocation-local gate does not support configuration cache.")

    val ownedRoot = rootProject.projectDir.toPath().toRealPath()
    val byteRoot = ownedRoot.resolve("build/publication-bytes")
    val exported = byteRoot.resolve("bundle")
    val incoming = byteRoot.resolve("input")
    val destination = byteRoot.resolve("owned-maven")
    val repositoryName = "engine6Owned"
    val ownedRepositories = linkedMapOf<Project, MavenArtifactRepository>()
    val algorithms = linkedMapOf("md5" to "MD5", "sha1" to "SHA-1", "sha256" to "SHA-256", "sha512" to "SHA-512")
    val modules = listOf("source-contract", "source-engine", "source-testkit")
    val targets = linkedMapOf(
        "kotlinMultiplatform" to "", "android" to "-android", "jvm" to "-jvm",
        "iosArm64" to "-iosarm64", "iosSimulatorArm64" to "-iossimulatorarm64",
    )
    val group = "me.manga.kira.source"
    val version = rootProject.version.toString()
    requireBytes(version.matches(Regex("(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)")),
        "VERSION", "Only a canonical numeric owned-local test version is supported; SNAPSHOT is not eligible.")

    fun owned(file: File, required: Boolean, code: String = "PATH"): Path {
        val path = file.toPath().toAbsolutePath().normalize()
        requireBytes(path.startsWith(ownedRoot), code, "A path escapes the owned workspace.")
        var cursor = ownedRoot
        ownedRoot.relativize(path).forEach { part ->
            cursor = cursor.resolve(part)
            requireBytes(!Files.isSymbolicLink(cursor), code, "Symlink paths are not accepted.")
        }
        if (required) requireBytes(Files.isRegularFile(path, NOFOLLOW_LINKS), code, "A required regular file is absent.")
        return path
    }

    fun relative(file: File): String = ownedRoot.relativize(owned(file, false)).toString().replace(File.separatorChar, '/')

    fun field(value: String): String {
        requireBytes(value.none { it == '\t' || it == '\n' || it == '\r' || it == '\u0000' }, "MODEL", "Unsafe model field.")
        return value
    }

    fun safeRepositoryPath(value: String) {
        requireBytes(!value.startsWith('/') && !value.contains('\\') && value.split('/').all {
            it.isNotEmpty() && it != "." && it != ".." && it.matches(Regex("[A-Za-z0-9_.-]+"))
        }, "PATH", "Unsafe Maven-relative output path.")
    }

    fun facts(file: File): KiraFileFacts {
        owned(file, true)
        val digests = algorithms.mapValues { MessageDigest.getInstance(it.value) }
        var size = 0L
        file.inputStream().use { input ->
            val buffer = ByteArray(64 * 1024)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                size += count
                digests.values.forEach { it.update(buffer, 0, count) }
            }
        }
        requireBytes(size == file.length(), "BYTES", "A file changed while reading.")
        return KiraFileFacts(size, digests.mapValues { HexFormat.of().formatHex(it.value.digest()) })
    }

    fun emptyDestination() {
        owned(destination.toFile(), false, "DESTINATION")
        if (Files.exists(destination, NOFOLLOW_LINKS)) {
            requireBytes(Files.isDirectory(destination, NOFOLLOW_LINKS), "DESTINATION", "The owned destination is not a directory.")
            Files.list(destination).use { requireBytes(!it.findAny().isPresent, "DESTINATION", "A fresh empty destination is required; no retry/reuse.") }
        }
    }

    rootProject.subprojects.forEach { project ->
        project.plugins.withId("maven-publish") {
            val publishing = project.extensions.getByType(PublishingExtension::class.java)
            ownedRepositories[project] = publishing.repositories.maven {
                name = repositoryName
                url = destination.toUri()
            }
        }
    }

    gradle.projectsEvaluated {
        requireBytes(rootProject.subprojects.map { it.name }.sorted() == modules, "MODEL", "Exactly the existing three modules are required.")
        val models = rootProject.subprojects.sortedBy { it.path }.flatMap { project ->
            val publications = project.extensions.getByType(PublishingExtension::class.java).publications
            requireBytes(publications.size == targets.size && publications.toList().all { it is MavenPublication }, "MODEL", "Unexpected publication type/count.")
            publications.withType(MavenPublication::class.java).sortedBy { it.name }.map { publication ->
                val name = publication.name.replaceFirstChar { it.uppercaseChar() }
                KiraPublication(
                    project, publication,
                    project.tasks.named("generatePomFileFor${name}Publication", GenerateMavenPom::class.java).get(),
                    project.tasks.named("generateMetadataFileFor${name}Publication", GenerateModuleMetadata::class.java).get(),
                )
            }
        }
        val writers = rootProject.subprojects.flatMap { it.tasks.withType(PublishToMavenRepository::class.java).toList() }.sortedBy { it.path }
        val state = gradle.sharedServices.registerIfAbsent("kiraPublicationByteState", KiraPublicationByteState::class.java) { maxParallelUsages.set(1) }
        val recipes = linkedMapOf<Task, List<Any>>()

        // The only new internal reads are component, usages and pre-XML POM dependencies.
        // Never execute a descriptor/XML action or use a private KGP coordinate resolver.
        fun nativeModel(): Map<KiraPublication, KiraNativeModel> {
            requireBytes(gradle.gradleVersion == "9.6.1", "TOOLCHAIN", "The native model adapter requires Gradle9.6.1.")
            val pomRewriteKey = "kotlin.mpp.keepMppDependenciesIntactInPoms"
            val kotlin = rootProject.subprojects.associateWith { project ->
                val plugin = project.plugins.findPlugin("org.jetbrains.kotlin.multiplatform") as? KotlinBasePlugin
                requireBytes(plugin?.pluginVersion == "2.2.21", "TOOLCHAIN", "Every publication requires the applied KGP2.2.21 plugin.")
                requireBytes(!project.extensions.extraProperties.has(pomRewriteKey) && !project.providers.gradleProperty(pomRewriteKey).isPresent &&
                    project.findProperty(pomRewriteKey) == null,
                    "MODEL", "POM rewrite customization is outside the supported default-selector cohort.")
                project.extensions.getByType(KotlinMultiplatformExtension::class.java)
            }
            // KGP also falls back to root local.properties. Match its UTF-8 Java Properties
            // syntax, but refuse any declaration of this one key; never expose private data.
            val localReadFailure = "KIRA_PUBLICATION_BYTES_MODEL: Cannot establish the default POM rewrite policy."
            val localPomRewriteConfigured = try {
                val local = owned(File(rootProject.rootDir, "local.properties"), false, "MODEL")
                if (Files.notExists(local, NOFOLLOW_LINKS)) false else {
                    requireBytes(Files.isRegularFile(local, NOFOLLOW_LINKS), "MODEL", "Root local properties must be a regular file.")
                    local.toFile().bufferedReader(UTF_8).use { reader ->
                        Properties().apply { load(reader) }.containsKey(pomRewriteKey)
                    }
                }
            } catch (_: IOException) {
                throw GradleException(localReadFailure)
            } catch (_: IllegalArgumentException) {
                throw GradleException(localReadFailure)
            } catch (_: SecurityException) {
                throw GradleException(localReadFailure)
            }
            requireBytes(!localPomRewriteConfigured, "MODEL", "POM rewrite customization is outside the supported default-selector cohort.")
            val roots = models.filter { it.publication.name == "kotlinMultiplatform" }.associateBy { it.project.path }
            val components: Map<KiraPublication, SoftwareComponentInternal> = models.associateWith { model ->
                val publication = modelValue(model.publication as? PublicationInternal<*>, "Unsupported native publication interface.")
                modelValue(publication.component.orNull, "A native publication has no attached component.")
            }
            requireBytes(components.values.toList().let { all -> all.indices.all { i -> (0 until i).none { j -> all[i] === all[j] } } },
                "MODEL", "Publication component identities must be unambiguous.")
            components.forEach { (model, component) ->
                if (component is ComponentWithCoordinates) {
                    val coordinates = component.coordinates
                    requireBytes(KiraGav(coordinates.group, coordinates.name, coordinates.version) == gav(model.publication),
                        "MODEL", "Component coordinates disagree with its attached publication.")
                }
            }
            val targetModels = models.filter { it.publication.name != "kotlinMultiplatform" }.associateWith { model ->
                val target = modelValue(kotlin.getValue(model.project).targets.singleOrNull { target ->
                    target.components.any { it === components.getValue(model) }
                }, "A target publication must map to exactly one actual KGP target.")
                requireBytes(target.publishable, "MODEL", "A cohort target is not publishable.")
                KiraTargetModel(target.name, target.platformType.name, nativeAttributes(target.attributes))
            }
            val localVariants = components.mapValues { (_, component) ->
                component.usages.map { usage: SoftwareComponentVariant ->
                    requireBytes(usage.name.isNotEmpty() && usage.capabilities.isEmpty() && usage.globalExcludes.isEmpty() &&
                        usage.dependencyConstraints.none { it.group == group }, "MODEL", "Unsupported native usage capabilities/excludes/cohort constraints.")
                    val edges = usage.dependencies.mapNotNull { dependency ->
                        if (dependency is ProjectDependency) {
                            requireBytes(dependency.targetConfiguration == null && dependency.artifacts.isEmpty() && dependency.attributes.isEmpty &&
                                dependency.capabilitySelectors.isEmpty() && dependency.excludeRules.isEmpty() && dependency.isTransitive &&
                                !dependency.isEndorsingStrictVersions && dependency.reason == null,
                                "MODEL", "Only default project-dependency selectors are supported.")
                            gav(modelValue(roots[dependency.path], "A project dependency is outside the cohort.").publication)
                        } else {
                            requireBytes(dependency.group != group, "MODEL", "A cohort dependency must be an actual project dependency.")
                            null
                        }
                    }.sortedBy { it.module }
                    requireBytes(edges.distinct().size == edges.size, "MODEL", "Duplicate native project selectors are unsupported.")
                    KiraVariantModel(usage.name, nativeAttributes(usage.attributes), edges)
                }.sortedBy { it.name }.also { variants ->
                    requireBytes(variants.isNotEmpty() && variants.map { it.name }.distinct().size == variants.size,
                        "MODEL", "Missing or duplicate native usage names.")
                }
            }
            // Same named/typed target and published-usage shape in every module. This is an
            // intentionally narrow rule, not Gradle's general variant-selection algorithm.
            targetModels.entries.groupBy { it.value }.values.forEach { peers ->
                requireBytes(peers.size == roots.size && peers.map { it.key.project.path }.toSet() == roots.keys &&
                    peers.map { peer -> localVariants.getValue(peer.key).map { it.name to it.attributes } }.distinct().size == 1,
                    "MODEL", "The actual cohort targets/usages are not homogeneous.")
            }
            return models.associateWith { model ->
                val component = components.getValue(model)
                val children = (component as? ComponentWithVariants)?.variants.orEmpty().map { child ->
                    modelValue(models.singleOrNull { components.getValue(it) === child }, "A child component is outside the native publication cohort.")
                }
                val expectedChildren = models.filter { it.project == model.project && it.publication.name != "kotlinMultiplatform" }
                requireBytes(if (model.publication.name == "kotlinMultiplatform") {
                    component is ComponentWithVariants && children.size == expectedChildren.size && children.toSet() == expectedChildren.toSet()
                } else children.isEmpty(), "MODEL", "Unsupported native child-component topology.")
                val variants = (localVariants.getValue(model) + children.flatMap { child ->
                    localVariants.getValue(child).map { it.copy(projectDependencies = emptyList(), availableAt = gav(child.publication)) }
                }).sortedBy { it.name }
                requireBytes(variants.map { it.name }.distinct().size == variants.size, "MODEL", "Duplicate local/remote native variant names.")

                val pom = modelValue(model.publication.pom as? MavenPomInternal, "Unsupported native POM interface.")
                val dependencies = modelValue(pom.dependencies.orNull, "Missing native pre-XML POM dependencies.")
                fun pomEdges(values: List<MavenDependency>, rewrite: Boolean): List<KiraPomEdge> = values.filter { it.groupId == group }.map { edge ->
                    val root = modelValue(roots.values.singleOrNull {
                        it.publication.groupId == edge.groupId && it.publication.artifactId == edge.artifactId && it.publication.version == edge.version
                    }, "Native pre-XML POM project coordinates are not a logical-root cohort GAV.")
                    val target = targetModels[model]
                    val destination = if (rewrite && target != null) modelValue(models.singleOrNull {
                        it.project == root.project && targetModels[it] == target
                    }, "No unique corresponding actual target publication for a POM project edge.") else root
                    requireBytes(rewrite || !edge.isOptional && edge.classifier.isNullOrEmpty() && edge.excludeRules.isEmpty(),
                        "MODEL", "Unsupported dependency-management flags omitted by the native POM writer.")
                    KiraPomEdge(gav(destination.publication), edge.scope, edge.isOptional, edge.type ?: "jar", edge.classifier.orEmpty(),
                        edge.excludeRules.map { listOf(it.group.orEmpty().ifEmpty { "*" }, it.module.orEmpty().ifEmpty { "*" }) }
                            .sortedBy { JsonOutput.toJson(it) })
                }.sortedBy { it.text() }
                // KGP rewrites direct dependency GAVs with withXml, not scopes/flags or
                // dependencyManagement. We assert only the admitted homogeneous mapping.
                KiraNativeModel(component.name, targetModels[model], variants,
                    pomEdges(dependencies.dependencies, true), pomEdges(dependencies.dependencyManagement, false))
            }
        }

        fun semanticLines(native: Map<KiraPublication, KiraNativeModel>): List<String> {
            // All callers first complete nativeModel(); record only its fixed supported policy.
            val lines = mutableListOf("pom-rewrite-policy\tKGP2.2.21\tdefault-no-opt-out-key")
            native.forEach { (model, value) ->
                fun row(kind: String, vararg fields: String) {
                    lines += (listOf(kind, model.project.path, model.publication.name) + fields).joinToString("\t") { field(it) }
                }
                row("component", value.componentName, "KGP2.2.21", value.target?.let {
                    JsonOutput.toJson(listOf(it.name, it.platform, it.attributes))
                } ?: "logical-root")
                value.variants.forEach { variant ->
                    row("variant", variant.name, JsonOutput.toJson(variant.attributes),
                        JsonOutput.toJson(variant.projectDependencies.map { it.fields() }),
                        variant.availableAt?.let { JsonOutput.toJson(it.fields()) } ?: "local")
                }
                row("pom-project-dependencies", value.pomDependencies.size.toString())
                value.pomDependencies.forEach { row("pom-project-edge", it.text()) }
                row("pom-project-management", value.pomManagement.size.toString())
                value.pomManagement.forEach { row("pom-project-managed-edge", it.text()) }
            }
            return lines.sorted()
        }

        fun auditActions() {
            recipes.forEach { (task, expected) ->
                requireBytes(task.enabled && task.actions.size == expected.size && task.actions.indices.all { task.actions[it] === expected[it] },
                    "ACTION_RECIPE", "A byte-path task's enabled state or action identities/order changed: ${task.path}")
            }
        }

        fun repositoryModel() {
            owned(destination.toFile(), false, "DESTINATION")
            requireBytes(writers.size == 15 && rootProject.subprojects.flatMap {
                it.tasks.withType(PublishToMavenRepository::class.java).toList()
            }.toSet() == writers.toSet(), "DESTINATION", "Exactly fifteen modeled native writers are required.")
            rootProject.subprojects.forEach { project ->
                val configured = project.extensions.getByType(PublishingExtension::class.java).repositories.toList()
                val repository = ownedRepositories.getValue(project)
                requireBytes(configured.size == 1 && configured.single() === repository, "DESTINATION", "Only the fixed owned-file repository is permitted.")
                val uri = repository.url
                requireBytes(repository.name == repositoryName && uri.scheme == "file" && uri.authority == null && uri.query == null &&
                    uri.fragment == null && Path.of(uri).toAbsolutePath().normalize() == destination,
                    "DESTINATION", "The native destination must be build/publication-bytes/owned-maven.")
                // Reuse the separately reviewed Gradle9.6.1 presence query. The public getter
                // creates empty credentials; never call it or inspect any credential values.
                val noCredentials = runCatching {
                    !(repository as AuthenticationSupportedInternal).configuredCredentials.isPresent
                }.getOrDefault(false)
                requireBytes(noCredentials && repository.authentication.isEmpty(), "DESTINATION", "Credentials/authentication are not permitted.")
            }
            writers.forEach { writer ->
                requireBytes(writer.repository === ownedRepositories.getValue(writer.project) &&
                    models.count { it.project == writer.project && it.publication === writer.publication } == 1,
                    "DESTINATION", "An unmodeled native writer is present.")
            }
        }

        fun payloads(): List<KiraPayload> {
            repositoryModel()
            return models.flatMap { model ->
                val project = model.project
                val publication = model.publication
                val actual = project.extensions.getByType(PublishingExtension::class.java).publications
                requireBytes(actual.size == 5 && actual.map { it.name }.toSet() == targets.keys && actual.any { it === publication },
                    "MODEL", "The complete original publication set changed.")
                requireBytes(project.group.toString() == group && publication.groupId == group && project.version.toString() == version &&
                    publication.version == version && publication.artifactId == project.name + targets.getValue(publication.name),
                    "MODEL", "Publication/project coordinates changed.")
                requireBytes(model.pom.enabled && model.metadata.enabled && model.pom.pom === publication.pom && model.metadata.publication.get() === publication,
                    "MODEL", "Original descriptor producers must remain enabled and attached.")
                val buildDirectory = ownedRoot.resolve(project.name).resolve("build")
                requireBytes(project.layout.buildDirectory.get().asFile.toPath().toAbsolutePath().normalize() == buildDirectory,
                    "PATH", "Module output roots must retain their original owned locations.")
                val base = "${group.replace('.', '/')}/${publication.artifactId}/$version/${publication.artifactId}-$version"
                fun output(kind: String, classifier: String, extension: String, file: File, producers: List<Task>): KiraPayload {
                    requireBytes(extension.matches(Regex("[A-Za-z0-9][A-Za-z0-9._-]*")) &&
                        (classifier.isEmpty() || classifier.matches(Regex("[A-Za-z0-9][A-Za-z0-9._-]*"))), "MODEL", "Unsafe artifact classifier/extension.")
                    val path = base + (if (classifier.isEmpty()) "" else "-$classifier") + ".$extension"
                    safeRepositoryPath(path)
                    requireBytes(owned(file, false).startsWith(buildDirectory), "PATH", "Native outputs must remain inside their module build directory.")
                    requireBytes(producers.isNotEmpty() && producers.all { it.enabled }, "PRODUCER", "Every outgoing file needs enabled modeled producers, including when excluded.")
                    return KiraPayload(model, kind, classifier, extension, path, file, producers.distinctBy { it.path }.sortedBy { it.path })
                }
                val artifacts = publication.artifacts.map { artifact ->
                    val classifier = artifact.classifier.orEmpty()
                    output(if (classifier == "sources") "sources" else "artifact", classifier, artifact.extension,
                        artifact.file, artifact.buildDependencies.getDependencies(null).toList())
                }
                val binaryExtension = when (publication.name) { "android" -> "aar"; "iosArm64", "iosSimulatorArm64" -> "klib"; else -> "jar" }
                requireBytes(artifacts.count { it.classifier.isEmpty() && it.extension == binaryExtension } == 1 &&
                    artifacts.any { it.classifier == "sources" && it.extension == "jar" }, "MODEL", "A native binary or sources artifact is missing.")
                requireBytes(model.pom.outputs.files.files == setOf(model.pom.destination) &&
                    model.metadata.outputs.files.files == setOf(model.metadata.outputFile.get().asFile), "MODEL", "Descriptors must retain their single native output.")
                val result = artifacts + listOf(
                    output("pom", "", "pom", model.pom.destination, listOf(model.pom)),
                    output("module", "", "module", model.metadata.outputFile.get().asFile, listOf(model.metadata)),
                )
                val writer = writers.single { it.publication === publication }
                requireBytes(writer.inputs.files.files.map { owned(it, false) }.toSet() == result.map { owned(it.output, false) }.toSet(),
                    "MODEL", "Native writer inputs contain a missing or unmodeled derived artifact/signature.")
                result
            }.sortedBy { it.repositoryPath }.also { list ->
                requireBytes(list.map { it.repositoryPath }.toSet().size == list.size, "MODEL", "Duplicate outgoing Maven paths.")
            }
        }

        fun producers(payloads: List<KiraPayload>): List<Task> =
            payloads.flatMap { it.producers }.distinctBy { it.path }.sortedBy { it.path }

        fun producerList(payloads: List<KiraPayload>): String = producers(payloads).joinToString("\n", postfix = "\n") { field(it.path) }

        fun modelText(payloads: List<KiraPayload>): String {
            val sourceFiles = mutableListOf<File>()
            listOf("build.gradle.kts", "settings.gradle.kts", "gradle.properties", "gradle/libs.versions.toml",
                "gradle/publication-gates.gradle.kts", "gradle/wrapper/gradle-wrapper.properties", "gradle/wrapper/gradle-wrapper.jar").forEach {
                sourceFiles += ownedRoot.resolve(it).toFile()
            }
            modules.forEach { module ->
                sourceFiles += ownedRoot.resolve("$module/build.gradle.kts").toFile()
                val sourceRoot = ownedRoot.resolve("$module/src")
                owned(sourceRoot.toFile(), false)
                Files.walk(sourceRoot).use { paths -> paths.forEach { path ->
                    owned(path.toFile(), false)
                    if (!Files.isDirectory(path, NOFOLLOW_LINKS)) sourceFiles += path.toFile()
                } }
            }
            val lines = mutableListOf("tool\tgradle\t${gradle.gradleVersion}")
            lines += semanticLines(nativeModel())
            listOf("java.runtime.version", "java.vendor", "os.name", "os.arch").forEach {
                lines += "tool\t$it\t${field(System.getProperty(it))}"
            }
            sourceFiles.distinct().sortedBy(::relative).forEach { file ->
                val value = facts(file)
                lines += "source\t${field(relative(file))}\t${value.size}\t${value.hashes.getValue("sha256")}"
            }
            payloads.forEach { payload ->
                lines += listOf("output", payload.model.project.path, payload.model.publication.name,
                    group, payload.model.publication.artifactId, version, payload.kind, payload.classifier,
                    payload.extension, payload.repositoryPath, relative(payload.output),
                    payload.producers.joinToString(",") { it.path }).joinToString("\t") { field(it) }
            }
            return lines.sorted().joinToString("\n", postfix = "\n")
        }

        fun linkedPath(descriptor: String, value: Any?): String {
            requireBytes(value is String && value.isNotEmpty(), "METADATA", "Missing descriptor URL.")
            val url = value as String
            val uri = runCatching { URI(url) }.getOrElse { throw GradleException("KIRA_PUBLICATION_BYTES_METADATA: Invalid descriptor URL.") }
            requireBytes(!uri.isAbsolute && uri.authority == null && uri.query == null && uri.fragment == null && uri.rawPath == uri.path &&
                !url.contains('\\') && !url.startsWith('/') && url.split('/').all { it.isNotEmpty() && it.matches(Regex("[A-Za-z0-9_.-]+")) },
                "METADATA", "Descriptor URLs must be plain bounded Maven-relative links.")
            val resolved = Path.of(descriptor).parent.resolve(url).normalize().toString().replace(File.separatorChar, '/')
            safeRepositoryPath(resolved)
            return resolved
        }

        fun validateMetadata(payloads: List<KiraPayload>, files: Map<String, File>, values: Map<String, KiraFileFacts>) {
            val byPath = payloads.associateBy { it.repositoryPath }
            val roots = models.filter { it.publication.name == "kotlinMultiplatform" }.associateBy { it.project.name }
            val native = nativeModel()
            fun descriptor(model: KiraPublication): KiraPayload = payloads.single { it.model === model && it.kind == "module" }
            val documents = models.associateWith { model ->
                val file = files.getValue(descriptor(model).repositoryPath)
                requireBytes(file.length() <= 4L * 1024 * 1024, "METADATA", "Descriptor exceeds the local parser bound.")
                jsonMap(JsonSlurper().parseText(file.readText(UTF_8)))
            }
            fun variants(model: KiraPublication): List<Map<*, *>> = jsonList(documents.getValue(model)["variants"]).map(::jsonMap)
            fun pomEdges(sections: List<Element>): List<KiraPomEdge> {
                requireBytes(sections.size <= 1 && sections.all { xmlElements(it).all { child -> child.tagName == "dependency" } },
                    "METADATA", "Duplicate or malformed POM dependency sections.")
                return sections.flatMap { xmlChildren(it, "dependency") }.filter { xmlValue(it, "groupId") == group }.map { dependency ->
                    requireBytes(xmlElements(dependency).all { it.tagName in setOf("groupId", "artifactId", "version", "scope", "optional", "type", "classifier", "exclusions") },
                        "METADATA", "Unsupported POM project-dependency fields.")
                    val optional = xmlOptionalValue(dependency, "optional")
                    requireBytes(optional in setOf(null, "true", "false"), "METADATA", "Invalid POM dependency optionality.")
                    val exclusions = xmlChildren(dependency, "exclusions")
                    requireBytes(exclusions.size <= 1 && exclusions.all { xmlElements(it).all { child -> child.tagName == "exclusion" } },
                        "METADATA", "Duplicate or malformed POM exclusions.")
                    val excluded = exclusions.flatMap { xmlChildren(it, "exclusion") }.map { exclusion ->
                        requireBytes(xmlElements(exclusion).size == 2 && xmlElements(exclusion).map { it.tagName }.toSet() == setOf("groupId", "artifactId"),
                            "METADATA", "Malformed POM exclusion tuple.")
                        listOf(xmlValue(exclusion, "groupId"), xmlValue(exclusion, "artifactId"))
                    }.sortedBy { JsonOutput.toJson(it) }
                    KiraPomEdge(KiraGav(xmlValue(dependency, "groupId"), xmlValue(dependency, "artifactId"), xmlValue(dependency, "version")),
                        xmlOptionalValue(dependency, "scope"), optional == "true", xmlOptionalValue(dependency, "type") ?: "jar",
                        xmlOptionalValue(dependency, "classifier").orEmpty(), excluded)
                }.sortedBy { it.text() }
            }
            models.forEach { model ->
                val publication = model.publication
                val expected = native.getValue(model)
                val pom = payloads.single { it.model === model && it.kind == "pom" }
                val pomXml = parseXml(files.getValue(pom.repositoryPath))
                requireBytes(pomXml.tagName == "project" && xmlValue(pomXml, "groupId") == group &&
                    xmlValue(pomXml, "artifactId") == publication.artifactId && xmlValue(pomXml, "version") == version,
                    "METADATA", "POM coordinates disagree with the native model.")
                requireBytes(xmlChildren(pomXml, "parent").isEmpty() && xmlChildren(pomXml, "profiles").isEmpty(),
                    "METADATA", "Inherited/profile POM dependencies are outside the supported model.")
                requireBytes(pomEdges(xmlChildren(pomXml, "dependencies")) == expected.pomDependencies,
                    "METADATA", "POM project tuples/scopes disagree with the native model and bounded target mapping.")
                val management = xmlChildren(pomXml, "dependencyManagement")
                requireBytes(management.size <= 1 && management.all { xmlElements(it).all { child -> child.tagName == "dependencies" } },
                    "METADATA", "Duplicate or malformed POM dependency management.")
                requireBytes(pomEdges(management.flatMap { xmlChildren(it, "dependencies") }) == expected.pomManagement,
                    "METADATA", "POM cohort dependency management disagrees with the native pre-XML model.")
                val metadata = descriptor(model)
                val document = documents.getValue(model)
                val component = jsonMap(document["component"])
                requireBytes(document["formatVersion"] == "1.1" && component["group"] == group && component["module"] == model.project.name &&
                    component["version"] == version, "METADATA", "Module component must identify its logical root cohort.")
                if (publication.name == "kotlinMultiplatform") {
                    requireBytes(!component.containsKey("url"), "METADATA", "Root component cannot redirect elsewhere.")
                } else {
                    requireBytes(linkedPath(metadata.repositoryPath, component["url"]) == descriptor(roots.getValue(model.project.name)).repositoryPath,
                        "METADATA", "Target component backlink disagrees with the root publication.")
                }
                val nativeVariants = variants(model)
                requireBytes(nativeVariants.isNotEmpty() && nativeVariants.all { it["name"] is String && (it["name"] as String).isNotEmpty() } &&
                    nativeVariants.map { it["name"] }.toSet().size == nativeVariants.size, "METADATA", "Missing or duplicate metadata variants.")
                requireBytes(nativeVariants.map { it["name"] as String }.sorted() == expected.variants.map { it.name },
                    "METADATA", "Variant membership differs from the actual native component usages.")
                val expectedVariants = expected.variants.associateBy { it.name }
                val referenced = linkedSetOf<String>()
                nativeVariants.forEach { variant ->
                    val expectedVariant = expectedVariants.getValue(variant["name"] as String)
                    val attrs = jsonMap(variant["attributes"])
                    requireBytes(attrs == expectedVariant.attributes && variant.containsKey("available-at") == (expectedVariant.availableAt != null),
                        "METADATA", "Variant attributes/local-remote role differ from the native usage.")
                    requireBytes(jsonList(variant["capabilities"] ?: emptyList<Any>()).isEmpty() &&
                        jsonList(variant["dependencyConstraints"] ?: emptyList<Any>()).map(::jsonMap).none { it["group"] == group },
                        "METADATA", "Unsupported metadata capabilities/cohort constraints.")
                    if (expectedVariant.availableAt != null) {
                        requireBytes(publication.name == "kotlinMultiplatform" && !variant.containsKey("files") && !variant.containsKey("dependencies") &&
                            !variant.containsKey("dependencyConstraints") && !variant.containsKey("capabilities"),
                            "METADATA", "Only root target-reference variants may use available-at.")
                        val link = jsonMap(variant["available-at"])
                        val path = linkedPath(metadata.repositoryPath, link["url"])
                        val target = byPath[path]
                        requireBytes(target != null && target.kind == "module" && target.model.project == model.project &&
                            target.model.publication.name != "kotlinMultiplatform", "METADATA", "A target link is absent from the complete publication set.")
                        val targetModel = requireNotNull(target).model
                        requireBytes(gav(targetModel.publication) == expectedVariant.availableAt && link["group"] == group &&
                            link["module"] == targetModel.publication.artifactId && link["version"] == version,
                            "METADATA", "Target link coordinates disagree with its destination.")
                    } else {
                        jsonList(variant["files"] ?: emptyList<Any>()).map(::jsonMap).forEach { file ->
                            val path = linkedPath(metadata.repositoryPath, file["url"])
                            val payload = byPath[path]
                            requireBytes(payload != null && payload.model === model && payload.kind in setOf("artifact", "sources"),
                                "METADATA", "A variant file is outside its native publication.")
                            val name = file["name"]
                            requireBytes(name is String && name.isNotEmpty() && name != "." && name != ".." && !name.contains('/') && !name.contains('\\'),
                                "METADATA", "Unsafe variant file name.")
                            val actual = values.getValue(path)
                            requireBytes(file["size"].toString().toLongOrNull() == actual.size && algorithms.keys.all { file[it] == actual.hashes.getValue(it) },
                                "METADATA", "Variant file size/checksums disagree with the sealed bytes.")
                            referenced += path
                        }
                        val edges = jsonList(variant["dependencies"] ?: emptyList<Any>()).map(::jsonMap).filter { it["group"] == group }.map { dependency ->
                            val target = models.singleOrNull { it.publication.artifactId == dependency["module"] }
                            requireBytes(target != null && target.publication.name == "kotlinMultiplatform" &&
                                dependency.keys == setOf("group", "module", "version") && jsonMap(dependency["version"]) == mapOf("requires" to version),
                                "METADATA", "Module project dependency is outside the default-selector cohort.")
                            gav(requireNotNull(target).publication)
                        }.sortedBy { it.module }
                        requireBytes(edges == expectedVariant.projectDependencies,
                            "METADATA", "Per-variant project dependencies differ from the actual native usage: ${expectedVariant.name}")
                    }
                }
                requireBytes(payloads.filter { it.model === model && (it.classifier.isEmpty() && it.kind == "artifact" || it.kind == "sources") }
                    .all { it.repositoryPath in referenced }, "METADATA", "A binary or sources artifact lost its native variant reference.")
            }
        }

        fun inventory(payloads: List<KiraPayload>, files: Map<String, File>): List<KiraByteRow> {
            val values = files.mapValues { facts(it.value) }
            validateMetadata(payloads, files, values)
            return payloads.flatMap { payload ->
                val value = values.getValue(payload.repositoryPath)
                listOf(KiraByteRow(payload, payload.kind, payload.repositoryPath, value.size, value.hashes.getValue("sha256"))) +
                    algorithms.keys.map { algorithm ->
                        val bytes = value.hashes.getValue(algorithm).toByteArray(UTF_8)
                        KiraByteRow(payload, "checksum:$algorithm", "${payload.repositoryPath}.$algorithm", bytes.size.toLong(), sha256(bytes))
                    }
            }.sortedBy { it.path }.also { rows ->
                requireBytes(rows.map { it.path }.toSet().size == rows.size, "MODEL", "Duplicate native output/sidecar paths.")
            }
        }

        fun inventoryText(rows: List<KiraByteRow>): String = rows.joinToString("\n", postfix = "\n") { it.line() }

        fun metadataFiles(payloads: List<KiraPayload>, rows: List<KiraByteRow>): Map<String, ByteArray> = linkedMapOf(
            "inventory.tsv" to inventoryText(rows).toByteArray(UTF_8),
            "model.tsv" to modelText(payloads).toByteArray(UTF_8),
            "producers.txt" to producerList(payloads).toByteArray(UTF_8),
        )

        fun sealText(metadata: Map<String, ByteArray>): ByteArray = metadata.entries.sortedBy { it.key }
            .joinToString("\n", postfix = "\n") { "${sha256(it.value)}  ${it.key}" }.toByteArray(UTF_8)

        fun exactTree(root: Path, expectedFiles: Set<String>) {
            owned(root.toFile(), false)
            requireBytes(Files.isDirectory(root, NOFOLLOW_LINKS), "INPUT", "A required owned directory is absent.")
            val expectedDirectories = mutableSetOf("")
            expectedFiles.forEach { value ->
                var parent = Path.of(value).parent
                while (parent != null) { expectedDirectories += parent.toString().replace(File.separatorChar, '/'); parent = parent.parent }
            }
            val observed = mutableSetOf<String>()
            Files.walk(root).use { paths -> paths.forEach { path ->
                owned(path.toFile(), false)
                val relative = root.relativize(path).toString().replace(File.separatorChar, '/')
                if (Files.isDirectory(path, NOFOLLOW_LINKS)) requireBytes(relative in expectedDirectories, "INPUT", "Unexpected directory in sealed byte tree.")
                else {
                    requireBytes(Files.isRegularFile(path, NOFOLLOW_LINKS), "INPUT", "A non-regular byte-tree entry is not accepted.")
                    observed += relative
                }
            } }
            requireBytes(observed == expectedFiles, "INPUT", "The exact sealed file/path set does not match.")
        }

        val initialPayloads = payloads()
        // Eager read-only census: export --dry-run --info can observe these exact seams
        // without running producers. Task-action checks still re-read the complete model.
        semanticLines(nativeModel()).forEach { logger.info("Kira publication model: $it") }
        val initialProducers = producers(initialPayloads)
        val requiredExclusions = initialProducers.map { it.path }.toSet()
        val expectedSeal = rootProject.providers.gradleProperty("kiraPublicationSealSha256").orNull
        requireBytes(if (byteMode == "promote") expectedSeal?.matches(Regex("[0-9a-f]{64}")) == true else expectedSeal == null,
            "INPUT", "Promotion requires the independently retained SHA256 of seal.sha256; export must not accept a previous seal.")

        fun readBundle(): KiraBundle {
            val current = payloads()
            requireBytes(producers(current).toSet() == initialProducers.toSet(), "MODEL", "Native producer mapping changed after configuration.")
            val files = current.associate { it.repositoryPath to incoming.resolve("payloads/${it.repositoryPath}").toFile() }
            exactTree(incoming, files.keys.map { "payloads/$it" }.toSet() + setOf("inventory.tsv", "model.tsv", "producers.txt", "seal.sha256"))
            val sealFile = incoming.resolve("seal.sha256").toFile()
            requireBytes(sealFile.length() <= 1024 && sha256(sealFile.readBytes()) == expectedSeal, "SEAL", "The retained external seal pin does not match.")
            val rows = inventory(current, files)
            val metadata = metadataFiles(current, rows)
            metadata.forEach { (name, bytes) ->
                val file = incoming.resolve(name).toFile()
                requireBytes(file.length() == bytes.size.toLong() && file.readBytes().contentEquals(bytes), "BYTES", "Sealed model/inventory/producer bytes disagree: $name")
            }
            requireBytes(sealFile.readBytes().contentEquals(sealText(metadata)), "SEAL", "The input files do not match the retained seal.")
            return KiraBundle(requireNotNull(expectedSeal), rows, files)
        }

        fun compareRestored(bundle: KiraBundle) {
            val current = payloads()
            val actual = inventory(current, current.associate { it.repositoryPath to it.output })
            requireBytes(inventoryText(actual) == inventoryText(bundle.rows), "BYTES", "The entire restored native output set must match the sealed inventory.")
        }

        fun completed(task: Task) {
            val status = task.state
            requireBytes(task in gradle.taskGraph.allTasks && task.enabled && status.executed && status.failure == null && !status.skipped,
                "FRESH_CHECK", "The current invocation did not complete its required byte-path task: ${task.path}")
        }

        // Assigned after all task providers exist; called at graph readiness and again at
        // each byte-path entry. This validates selection, never mutates the Gradle graph.
        lateinit var auditPromotionGraph: () -> List<PublishToMavenRepository>

        val exportTask = rootProject.tasks.register("exportKiraPublicationBytes") {
            dependsOn(initialProducers)
            usesService(state)
            doNotTrackState("An explicit separate build/export, never cached authorization.")
            doLast {
                auditActions()
                emptyDestination()
                val current = payloads()
                producers(current).forEach { producer ->
                    val status = producer.state
                    requireBytes(producer in gradle.taskGraph.allTasks && producer.enabled && status.executed && status.failure == null &&
                        (!status.skipped || status.upToDate), "PRODUCER", "Producer omitted, failed or skipped: ${producer.path}")
                }
                owned(exported.toFile(), false)
                requireBytes(!Files.exists(exported, NOFOLLOW_LINKS), "OUTPUT", "Export requires an absent bundle directory; no overwrite/reseal.")
                val rows = inventory(current, current.associate { it.repositoryPath to it.output })
                val metadata = metadataFiles(current, rows)
                current.forEach { payload ->
                    val target = exported.resolve("payloads/${payload.repositoryPath}")
                    owned(target.toFile(), false)
                    Files.createDirectories(target.parent)
                    Files.copy(payload.output.toPath(), target)
                }
                val copied = inventory(current, current.associate { it.repositoryPath to exported.resolve("payloads/${it.repositoryPath}").toFile() })
                requireBytes(inventoryText(copied) == inventoryText(rows) && modelText(current).toByteArray(UTF_8).contentEquals(metadata.getValue("model.tsv")),
                    "BYTES", "Source/model/output bytes changed during export.")
                metadata.forEach { (name, bytes) -> Files.write(exported.resolve(name), bytes) }
                val seal = sealText(metadata)
                Files.write(exported.resolve("seal.sha256"), seal)
                exactTree(exported, current.map { "payloads/${it.repositoryPath}" }.toSet() + metadata.keys + "seal.sha256")
                logger.lifecycle("Owned-local byte export only; seal.sha256 SHA256=${sha256(seal)}. Not provenance or release approval.")
            }
        }
        val restoreTask = rootProject.tasks.register("restoreKiraPublicationBytes") {
            usesService(state)
            doNotTrackState("Restore verified raw files in this invocation; never restore caches or task histories.")
            doLast {
                auditPromotionGraph()
                emptyDestination()
                val bundle = readBundle()
                val current = payloads()
                val outputs = current.groupBy { owned(it.output, false) }
                outputs.forEach { (path, payloads) ->
                    requireBytes(!Files.exists(path, NOFOLLOW_LINKS), "RESTORE", "Restore requires absent native output files; use a fresh owned workspace.")
                    requireBytes(payloads.map { facts(bundle.files.getValue(it.repositoryPath)) }.distinct().size == 1,
                        "RESTORE", "Publications sharing an original output path disagree on its bytes.")
                }
                outputs.forEach { (path, payloads) ->
                    Files.createDirectories(path.parent)
                    Files.copy(bundle.files.getValue(payloads.first().repositoryPath).toPath(), path)
                }
                compareRestored(bundle)
                state.get().restoredSeal = bundle.seal
            }
        }
        val verifyTask = rootProject.tasks.register("verifyKiraPublicationBytes") {
            dependsOn(restoreTask)
            usesService(state)
            doNotTrackState("Fresh whole-cohort verification before the first native write.")
            doLast {
                auditPromotionGraph()
                completed(restoreTask.get())
                emptyDestination()
                val bundle = readBundle()
                requireBytes(state.get().restoredSeal == bundle.seal, "FRESH_CHECK", "No successful restore in this invocation.")
                compareRestored(bundle)
                state.get().verifiedSeal = bundle.seal
            }
        }
        val promoteTask = rootProject.tasks.register("promoteKiraPublicationBytes") { dependsOn(writers) }
        val readbackTask = rootProject.tasks.register("verifyKiraPublicationReadback") {
            usesService(state)
            mustRunAfter(writers)
            doNotTrackState("Read actual native output bytes; native exit0 alone is not complete readback.")
            doLast {
                val selected = auditPromotionGraph()
                completed(restoreTask.get())
                completed(verifyTask.get())
                val bundle = readBundle()
                requireBytes(state.get().verifiedSeal == bundle.seal, "FRESH_CHECK", "No successful current-invocation verification.")
                compareRestored(bundle)
                requireBytes(selected.isNotEmpty() && state.get().guardedWriters == selected.map { it.path }.toSet(), "READBACK", "Missing native writer guards.")
                selected.forEach(::completed)
                val selectedPublications = selected.map { it.publication }.toSet()
                val rows = bundle.rows.filter { it.payload.model.publication in selectedPublications }
                val expected = rows.associate { it.path to Pair(it.size, it.sha256) }.toMutableMap()
                selectedPublications.forEach { publication ->
                    val index = "${group.replace('.', '/')}/${publication.artifactId}/maven-metadata.xml"
                    val file = destination.resolve(index).toFile()
                    owned(file, true)
                    val xml = parseXml(file)
                    requireBytes(xml.tagName == "metadata" && xmlValue(xml, "groupId") == group && xmlValue(xml, "artifactId") == publication.artifactId,
                        "READBACK", "Mutable index coordinates changed.")
                    exactXmlChildren(xml, setOf("groupId", "artifactId", "versioning"))
                    val versioning = xmlChildren(xml, "versioning").singleOrNull()
                    requireBytes(versioning != null, "READBACK", "Missing mutable index versioning.")
                    val values = requireNotNull(versioning)
                    exactXmlChildren(values, setOf("latest", "release", "versions", "lastUpdated"))
                    val versions = xmlChildren(values, "versions").single()
                    exactXmlChildren(versions, setOf("version"))
                    requireBytes(xmlValue(values, "latest") == version && xmlValue(values, "release") == version && xmlValue(versions, "version") == version,
                        "READBACK", "The fresh owned index contains a different or partial version set.")
                    val timestamp = xmlValue(values, "lastUpdated")
                    requireBytes(timestamp.matches(Regex("[0-9]{14}")), "READBACK", "Invalid native index timestamp.")
                    LocalDateTime.parse(timestamp, DateTimeFormatter.ofPattern("uuuuMMddHHmmss").withResolverStyle(ResolverStyle.STRICT))
                    val value = facts(file)
                    expected[index] = Pair(value.size, value.hashes.getValue("sha256"))
                    algorithms.keys.forEach { algorithm ->
                        val bytes = value.hashes.getValue(algorithm).toByteArray(UTF_8)
                        expected["$index.$algorithm"] = Pair(bytes.size.toLong(), sha256(bytes))
                    }
                }
                exactTree(destination, expected.keys)
                expected.forEach { (path, value) ->
                    val actual = facts(destination.resolve(path).toFile())
                    requireBytes(actual.size == value.first && actual.hashes.getValue("sha256") == value.second,
                        "READBACK", "Native output bytes differ from the sealed expectation: $path")
                }
                logger.lifecycle("Owned-file native readback matched ${selected.size} selected publications; not remote publication or consumer acceptance.")
            }
        }

        writers.forEach { writer ->
            val nativeActions = writer.actions.toList()
            requireBytes(nativeActions.size == 1, "ACTION_RECIPE", "Pinned native writer must retain exactly one original action.")
            writer.dependsOn(verifyTask)
            writer.usesService(state)
            writer.finalizedBy(readbackTask)
            writer.doFirst("whole-cohort sealed byte guard") {
                auditPromotionGraph()
                completed(restoreTask.get())
                completed(verifyTask.get())
                val bundle = readBundle()
                requireBytes(state.get().restoredSeal == bundle.seal && state.get().verifiedSeal == bundle.seal,
                    "FRESH_CHECK", "No successful whole-set check in this invocation.")
                compareRestored(bundle)
                val guarded = state.get().guardedWriters
                if (guarded.isEmpty()) emptyDestination()
                else writers.filter { it.path in guarded }.forEach(::completed)
                requireBytes(state.get().guardedWriters.add(writer.path), "FRESH_CHECK", "A native writer cannot be reused in this invocation.")
            }
            val recipe = writer.actions.toList()
            requireBytes(recipe.size == 2 && recipe[1] === nativeActions.single(), "ACTION_RECIPE", "The unchanged native action must immediately follow its guard.")
            recipes[writer] = recipe
        }
        listOf(exportTask, restoreTask, verifyTask, promoteTask, readbackTask).forEach { recipes[it.get()] = it.get().actions.toList() }

        auditPromotionGraph = {
            auditActions()
            requireBytes(byteMode == "promote" && gradle.startParameter.excludedTaskNames == requiredExclusions,
                "EXCLUSIONS", "Use exactly the sealed model-derived fully qualified -x producer arguments.")
            requireBytes(producers(payloads()).map { it.path }.toSet() == requiredExclusions, "EXCLUSIONS", "Producer mapping changed after configuration.")
            val selectedTasks = gradle.taskGraph.allTasks
            val selected = selectedTasks.filterIsInstance<PublishToMavenRepository>()
            val allowed: Set<Task> = writers.toSet() + setOf(restoreTask.get(), verifyTask.get(), promoteTask.get(), readbackTask.get())
            requireBytes(selected.isNotEmpty() && selectedTasks.all { it in allowed } && restoreTask.get() in selectedTasks &&
                verifyTask.get() in selectedTasks && readbackTask.get() in selectedTasks && selected.all { it in writers },
                "GRAPH", "Promotion permits only restore, fresh verification, modeled native writers and readback; no producer/generator/check may run.")
            selected
        }

        gradle.taskGraph.whenReady {
            auditActions()
            payloads()
            requireBytes(allTasks.none { it is PublishToMavenLocal || it.name == "publish" }, "GRAPH", "Maven-local and ordinary aggregate publication are forbidden in byte mode.")
            if (byteMode == "export") {
                requireBytes(gradle.startParameter.excludedTaskNames.isEmpty() && exportTask.get() in allTasks &&
                    allTasks.none { it is PublishToMavenRepository || it in listOf(restoreTask.get(), verifyTask.get(), promoteTask.get(), readbackTask.get()) },
                    "GRAPH", "Export must be separate from promotion with no omitted producers.")
            } else {
                auditPromotionGraph()
                emptyDestination()
            }
        }
    }
}
