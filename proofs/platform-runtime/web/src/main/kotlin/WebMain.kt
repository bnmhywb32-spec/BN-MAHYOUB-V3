import kotlin.js.Promise

@JsModule("sql.js")
@JsNonModule
external fun initSqlJs(config: dynamic): Promise<dynamic>

external val localStorage: dynamic
external val navigator: dynamic
external fun btoa(data: String): String
external fun atob(data: String): String
external class Uint8Array(length: Int)

private const val DATABASE_KEY = "bn-v3-kotlin-runtime-v1"

private fun openBrowser(sql: dynamic): ProofConnection {
    val encoded = localStorage.getItem(DATABASE_KEY) as String?
    val bytes = encoded?.let {
        val binary = atob(it)
        val array = Uint8Array(binary.length)
        for (index in binary.indices) array.asDynamic()[index] = binary[index].code
        array
    }
    val db = if (bytes == null) js("new sql.Database()") else js("new sql.Database(bytes)")
    return object : ProofConnection {
        private var transaction = false

        override fun execSQL(sql: String) {
            db.run(sql)
            transaction = when (sql) {
                "BEGIN IMMEDIATE" -> true
                "COMMIT", "ROLLBACK" -> false
                else -> transaction
            }
            if (!transaction && sql != "ROLLBACK") {
                val data = db.export()
                val binary = buildString {
                    for (index in 0 until (data.length as Int)) {
                        append((data[index] as Int).toChar())
                    }
                }
                localStorage.setItem(DATABASE_KEY, btoa(binary))
            }
        }

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

        override fun close() { db.close() }
    }
}

fun main() {
    val config = js("({})")
    config.locateFile = { name: String -> "./" + name }
    val launch = initSqlJs(config).then<Promise<String>> { sql: dynamic ->
        val locks = navigator.locks
        check(locks != null) { "Browser storage coordination is unavailable" }
        locks.request(DATABASE_KEY, { runtimeProof { openBrowser(sql) } }).unsafeCast<Promise<String>>()
    }
    launch.unsafeCast<Promise<String>>().then<Unit> { result ->
        console.log("BN_V3_PROOF: " + result)
    }.catch { error ->
        console.error("BN_V3_PROOF: FAIL: " + error.message)
    }
}
