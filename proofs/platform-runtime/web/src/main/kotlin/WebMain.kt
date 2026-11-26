import kotlin.js.Promise

@JsModule("sql.js")
@JsNonModule
external fun initSqlJs(config: dynamic): Promise<dynamic>

external val navigator: dynamic
external val location: dynamic
external fun openProofStore(): Promise<dynamic>
external fun readProofSnapshot(store: dynamic): Promise<dynamic>
external fun publishProofSnapshot(store: dynamic, bytes: dynamic, abort: Boolean): Promise<Unit>

private const val DATABASE_KEY = "bn-v3-kotlin-runtime-v1"

private class Snapshot(var bytes: dynamic)

private fun openBrowser(sql: dynamic, snapshot: Snapshot): ProofConnection {
    val bytes = snapshot.bytes
    val db = if (bytes == null) js("new sql.Database()") else js("new sql.Database(bytes)")
    if (bytes == null) {
        db.run("""
            CREATE TABLE catalog(sku INTEGER PRIMARY KEY, title TEXT NOT NULL);
            WITH RECURSIVE seed(n) AS (
                SELECT 1 UNION ALL SELECT n+1 FROM seed WHERE n<100000
            )
            INSERT INTO catalog SELECT n, hex(randomblob(160)) FROM seed;
        """.trimIndent())
    }
    check((db.exec("SELECT COUNT(*) FROM catalog")[0].values[0][0] as Int) == 100000)
    check((db.exec("SELECT COUNT(*) FROM sqlite_master WHERE name='aborted_probe'")[0].values[0][0] as Int) == 0)
    return object : ProofConnection {
        override fun execSQL(sql: String) { db.run(sql) }

        override fun prepare(sql: String): ProofStatement {
            val statement = db.prepare(sql)
            return object : ProofStatement {
                private val bindings = mutableMapOf<Int, String>()
                private var bound = false

                override fun bindText(index: Int, value: String) { bindings[index] = value }
                override fun bindLong(index: Int, value: Long) { bindings[index] = value.toString() }
                override fun step(): Boolean {
                    if (!bound && bindings.isNotEmpty()) {
                        val last = bindings.keys.max()
                        statement.bind(Array(last) { bindings[it + 1] })
                        bound = true
                    }
                    return statement.step() as Boolean
                }
                override fun getLong(index: Int) = (statement.get()[index] as Double).toLong()
                override fun getText(index: Int) = statement.get()[index] as String
                override fun close() { statement.free() }
            }
        }

        override fun close() {
            try { snapshot.bytes = db.export() }
            finally { db.close() }
        }
    }
}

fun main() {
    val config = js("({})")
    config.locateFile = { name: String -> "./" + name }
    val abort = location.search == "?abort-write"
    val launch = initSqlJs(config).then<Promise<String>> { sql: dynamic ->
        val locks = navigator.locks
        check(locks != null) { "Browser storage coordination is unavailable" }
        locks.request(DATABASE_KEY, {
            openProofStore().then<dynamic> { store: dynamic ->
                readProofSnapshot(store).then<dynamic> { bytes: dynamic ->
                    val snapshot = Snapshot(bytes)
                    val result = runtimeProof { openBrowser(sql, snapshot) }
                    if (abort) {
                        val failedBytes = snapshot.bytes
                        val db = js("new sql.Database(failedBytes)")
                        try {
                            db.run("CREATE TABLE aborted_probe(id INTEGER)")
                            snapshot.bytes = db.export()
                        } finally { db.close() }
                    }
                    if (abort || bytes == null) {
                        publishProofSnapshot(store, snapshot.bytes, abort).then { result }
                    } else {
                        result
                    }
                }.finally { store.close() }
            }
        }).unsafeCast<Promise<String>>()
    }
    launch.unsafeCast<Promise<String>>().then<Unit> { result ->
        console.log("BN_V3_PROOF: " + result)
    }.catch { error ->
        console.error("BN_V3_PROOF: FAIL: " + error.message)
    }
}
