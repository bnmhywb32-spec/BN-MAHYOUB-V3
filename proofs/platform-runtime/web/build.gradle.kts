plugins { kotlin("js") version "2.1.20" }

val copyRuntimeContract by tasks.registering(Copy::class) {
    from("../Runtime.kt")
    into(layout.buildDirectory.dir("generated/runtime-contract"))
}

kotlin {
    js(IR) {
        browser {
            commonWebpackConfig { outputFileName = "proof.js" }
        }
        binaries.executable()
    }
    sourceSets.named("main") {
        kotlin.srcDir(copyRuntimeContract)
        dependencies { implementation(npm("sql.js", "1.13.0")) }
    }
}
