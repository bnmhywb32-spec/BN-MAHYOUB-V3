import androidx.sqlite.SQLiteDriver
import androidx.sqlite.execSQL

fun openNative(driver: SQLiteDriver, path: String): ProofConnection {
    val db = driver.open(path)
    return object : ProofConnection {
        override fun execSQL(sql: String) = db.execSQL(sql)
        override fun close() = db.close()
        override fun prepare(sql: String): ProofStatement {
            val statement = db.prepare(sql)
            return object : ProofStatement {
                override fun bindText(index: Int, value: String) = statement.bindText(index, value)
                override fun bindLong(index: Int, value: Long) = statement.bindLong(index, value)
                override fun step() = statement.step()
                override fun getLong(index: Int) = statement.getLong(index)
                override fun getText(index: Int) = statement.getText(index)
                override fun close() = statement.close()
            }
        }
    }
}
