package proof.runtime

import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import runtimeProof

@RunWith(AndroidJUnit4::class)
class RuntimeTest {
    @Test
    fun reopensSaleWithExactAmountsAndNoDuplicateEffects() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val database = File.createTempFile("sale", ".db", context.cacheDir)
        try {
            assertEquals(
                "PASS: durable sale saved; relaunch to verify persistence",
                runtimeProof(BundledSQLiteDriver(), database.path),
            )
            assertEquals(
                "PASS: persisted sale verified after relaunch",
                runtimeProof(BundledSQLiteDriver(), database.path),
            )
        } finally {
            database.delete()
        }
    }
}
