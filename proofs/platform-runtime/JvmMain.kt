import androidx.sqlite.driver.bundled.BundledSQLiteDriver

fun main(args: Array<String>) {
    require(args.size == 2) { "Expected database path and phase (seed or relaunch)" }
    val expected = when (args[1]) {
        "seed" -> "PASS: durable sale saved; relaunch to verify persistence"
        "relaunch" -> "PASS: persisted sale verified after relaunch"
        else -> error("Unknown phase")
    }
    val actual = runtimeProof(BundledSQLiteDriver(), args[0])
    check(actual == expected) { "Expected '$expected', got '$actual'" }
    println(actual)
}
