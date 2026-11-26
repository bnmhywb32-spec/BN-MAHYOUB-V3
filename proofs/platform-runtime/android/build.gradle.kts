plugins {
    id("com.android.application") version "8.7.3"
    kotlin("android") version "2.1.20"
}

android {
    namespace = "proof.runtime"
    compileSdk = 35
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "proof.runtime"
        minSdk = 23
        targetSdk = 35
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
}

val copyRuntimeContract by tasks.registering(Copy::class) {
    from("../Runtime.kt", "../NativeDriver.kt")
    into(layout.buildDirectory.dir("generated/runtime-contract"))
}

tasks.named("preBuild").configure { dependsOn(copyRuntimeContract) }

kotlin {
    jvmToolchain(17)
    sourceSets.named("main") { kotlin.srcDir(copyRuntimeContract) }
}

dependencies {
    implementation("androidx.sqlite:sqlite-bundled:2.5.0")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
}
