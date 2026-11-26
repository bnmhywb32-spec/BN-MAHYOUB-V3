import androidx.sqlite.SQLiteConnection
import androidx.sqlite.SQLiteDriver
import androidx.sqlite.execSQL

fun runtimeProof(driver: SQLiteDriver, path: String): String {
 fun open()=driver.open(path)
 fun scalar(c:SQLiteConnection,sql:String):Long=c.prepare(sql).use { check(it.step());it.getLong(0) }
 var db=open()
 try {
 db.execSQL("CREATE TABLE IF NOT EXISTS stock(company TEXT PRIMARY KEY, qty INTEGER NOT NULL CHECK(qty>=0))")
 db.execSQL("CREATE TABLE IF NOT EXISTS sales(id TEXT PRIMARY KEY, qty INTEGER, remaining INTEGER)")
 db.execSQL("CREATE TABLE IF NOT EXISTS journal(id TEXT, account TEXT, debit INTEGER, credit INTEGER, PRIMARY KEY(id,account))")
 db.execSQL("CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY)")
 db.execSQL("CREATE TABLE IF NOT EXISTS proof_complete(id INTEGER PRIMARY KEY)")
 db.execSQL("INSERT OR IGNORE INTO stock VALUES('A',10),('B',10)")
 fun sell(id:String,qty:Long,fail:Boolean=false):Long {
 require(id in listOf("one","fail") && qty>0)
 db.execSQL("BEGIN IMMEDIATE")
 try {
 val old=db.prepare("SELECT qty,remaining FROM sales WHERE id=?").use { it.bindText(1,id);if(it.step())Pair(it.getLong(0),it.getLong(1)) else null }
 if(old!=null){check(old.first==qty) { "payload conflict" };db.execSQL("COMMIT");return old.second}
 db.prepare("UPDATE stock SET qty=qty-? WHERE company='A' AND qty>=?").use{it.bindLong(1,qty);it.bindLong(2,qty);it.step()}
 check(scalar(db,"SELECT changes()")==1L)
 val remaining=scalar(db,"SELECT qty FROM stock WHERE company='A'")
 db.prepare("INSERT INTO sales VALUES(?,?,?)").use{it.bindText(1,id);it.bindLong(2,qty);it.bindLong(3,remaining);it.step()}
 for((account,d,cr) in listOf(Triple("cash",qty*100,0L),Triple("revenue",0L,qty*100),Triple("cogs",qty*60,0L),Triple("inventory",0L,qty*60))){
 db.prepare("INSERT INTO journal VALUES(?,?,?,?)").use{it.bindText(1,id);it.bindText(2,account);it.bindLong(3,d);it.bindLong(4,cr);it.step()}
 }
 db.prepare("INSERT INTO outbox VALUES(?)").use{it.bindText(1,id);it.step()}
 if(fail)error("injected")
 db.execSQL("COMMIT");return remaining
 }catch(e:Throwable){db.execSQL("ROLLBACK");throw e}
 }
 fun verifyState() {
 db.prepare("SELECT CAST(CAST('9007199254740993' AS INTEGER) + 2 AS TEXT) AS exact_value").use {
 check(it.step() && it.getText(0)=="9007199254740995")
 }
 check(scalar(db,"SELECT count(*) FROM sales WHERE id='one'")==1L)
 check(sell("one",2)==8L)
 check(scalar(db,"SELECT qty FROM stock WHERE company='A'")==8L)
 check(scalar(db,"SELECT qty FROM stock WHERE company='B'")==10L)
 check(scalar(db,"SELECT count(*) FROM sales")==1L)
 check(scalar(db,"SELECT count(*) FROM outbox")==1L)
 check(scalar(db,"SELECT count(*) FROM journal")==4L)
 check(scalar(db,"SELECT sum(debit-credit) FROM journal")==0L)
 check(scalar(db,"SELECT debit FROM journal WHERE account='cash'")==200L)
 check(scalar(db,"SELECT debit FROM journal WHERE account='cogs'")==120L)
 }
 if (scalar(db,"SELECT count(*) FROM proof_complete")==1L) {
 verifyState()
 return "PASS: persisted sale verified after relaunch"
 }
 check(sell("one",2)==8L)
 check(sell("one",2)==8L)
 val conflict=runCatching{sell("one",1)}.exceptionOrNull()
 check(conflict is IllegalStateException && conflict.message=="payload conflict")
 val injected=runCatching{sell("fail",1,true)}.exceptionOrNull()
 check(injected is IllegalStateException && injected.message=="injected")
 db.close();db=open()
 verifyState()
 db.execSQL("INSERT INTO proof_complete VALUES(1)")
 return "PASS: durable sale saved; relaunch to verify persistence"
 }finally{db.close()}
}
