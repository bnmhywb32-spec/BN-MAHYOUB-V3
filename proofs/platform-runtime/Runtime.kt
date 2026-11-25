import androidx.sqlite.SQLiteConnection
import androidx.sqlite.SQLiteDriver
import androidx.sqlite.execSQL

fun runtimeProof(driver: SQLiteDriver, path: String) {
 fun open()=driver.open(path)
 fun scalar(c:SQLiteConnection,sql:String):Long=c.prepare(sql).use { check(it.step());it.getLong(0) }
 var db=open()
 try {
 db.execSQL("CREATE TABLE IF NOT EXISTS stock(company TEXT PRIMARY KEY, qty INTEGER NOT NULL CHECK(qty>=0))")
 db.execSQL("CREATE TABLE IF NOT EXISTS sales(id TEXT PRIMARY KEY, qty INTEGER, remaining INTEGER)")
 db.execSQL("CREATE TABLE IF NOT EXISTS journal(id TEXT, account TEXT, debit INTEGER, credit INTEGER, PRIMARY KEY(id,account))")
 db.execSQL("CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY)")
 for(t in listOf("stock","sales","journal","outbox"))db.execSQL("DELETE FROM "+t)
 db.execSQL("INSERT INTO stock VALUES('A',10),('B',10)")
 fun sell(id:String,qty:Long,fail:Boolean=false):Long {
 require(id in listOf("one","fail") && qty>0)
 db.execSQL("BEGIN IMMEDIATE")
 try {
 val old=db.prepare("SELECT qty,remaining FROM sales WHERE id=?").use { it.bindText(1,id);if(it.step())Pair(it.getLong(0),it.getLong(1)) else null }
 if(old!=null){check(old.first==qty);db.execSQL("COMMIT");return old.second}
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
 check(sell("one",2)==8L)
 check(sell("one",2)==8L)
 check(runCatching{sell("one",1)}.isFailure)
 check(runCatching{sell("fail",1,true)}.isFailure)
 db.close();db=open()
 check(sell("one",2)==8L)
 check(scalar(db,"SELECT qty FROM stock WHERE company='A'")==8L)
 check(scalar(db,"SELECT qty FROM stock WHERE company='B'")==10L)
 check(scalar(db,"SELECT count(*) FROM sales")==1L)
 check(scalar(db,"SELECT count(*) FROM outbox")==1L)
 check(scalar(db,"SELECT count(*) FROM journal")==4L)
 check(scalar(db,"SELECT sum(debit-credit) FROM journal")==0L)
 }finally{db.close()}
}
