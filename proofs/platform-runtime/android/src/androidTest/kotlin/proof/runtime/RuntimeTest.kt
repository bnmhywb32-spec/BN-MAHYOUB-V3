package proof.runtime

import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import openNative
import runtimeProof

@RunWith(AndroidJUnit4::class)
class RuntimeTest {
    @Test
    fun reopensSaleWithExactAmountsAndNoDuplicateEffects() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val phase = InstrumentationRegistry.getArguments().getString("phase")
        val database = File(instrumentation.targetContext.filesDir, "runtime-contract.db")
        val expected = when (phase) {
            "seed" -> {
                database.delete()
                "PASS: durable sale saved; relaunch to verify persistence"
            }
            "relaunch" -> "PASS: persisted sale verified after relaunch"
            else -> error("Expected seed or relaunch instrumentation argument")
        }
        assertEquals(expected, runtimeProof { openNative(BundledSQLiteDriver(), database.path) })
        println("BN_V3_PROOF: $expected")
    }
}
