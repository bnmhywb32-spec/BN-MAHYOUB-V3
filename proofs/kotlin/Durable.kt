import java.sql.DriverManager
import java.nio.file.Files
import java.util.concurrent.Executors
fun posting(qty:Long,price:Long):Long {
 require(qty>0 && price>=0)
 return java.math.BigInteger.valueOf(qty).multiply(java.math.BigInteger.valueOf(price)).add(java.math.BigInteger.valueOf(500)).divide(java.math.BigInteger.valueOf(1000)).longValueExact()
}
class Ledger(val path:String) {
 fun connect()=DriverManager.getConnection("jdbc:sqlite:"+path).also { c -> c.createStatement().use { it.execute("PRAGMA busy_timeout=10000") } }
 fun init(){connect().use { c -> Files.readString(java.nio.file.Path.of("proofs/schema.sql")).split(';').filter{it.isNotBlank()}.forEach { s -> c.createStatement().use{it.execute(s)} } }}
 fun sell(id:String, qty:Long, company:String="A", actor:String="cashier-A", fail:Boolean=false):Long {
 require(company=="A" && actor=="cashier-A" && qty>0)
 connect().use { c ->
 fun exec(s:String,vararg a:Any):Int=c.prepareStatement(s).use{p->a.forEachIndexed{i,v->p.setObject(i+1,v)};p.executeUpdate()}
 fun tx(s:String)=c.createStatement().use{it.execute(s)}
 tx("BEGIN IMMEDIATE")
 try {
 val fp="A/A1/"+qty
 val old=c.prepareStatement("SELECT fingerprint,remaining FROM commands WHERE id=?").use{p->p.setString(1,id);p.executeQuery().use{r->if(r.next()) Pair(r.getString(1),r.getLong(2)) else null}}
 if(old!=null){require(old.first==fp);tx("COMMIT");return old.second}
 check(exec("UPDATE stock SET qty=qty-? WHERE company='A' AND branch='A1' AND qty>=?",qty,qty)==1)
 val remaining=c.createStatement().use{it.executeQuery("SELECT qty FROM stock WHERE company='A' AND branch='A1'").use{r->r.next();r.getLong(1)}}
 val doc=java.util.UUID.randomUUID().toString()
 exec("INSERT INTO documents VALUES(?,?)",doc,qty)
 val rev=posting(qty,100L);val cost=posting(qty,60L)
 for((account,d,cr) in listOf(Triple("cash",rev,0L),Triple("revenue",0L,rev),Triple("cogs",cost,0L),Triple("inventory",0L,cost)))exec("INSERT INTO journal VALUES(?,?,?,?)",doc,account,d,cr)
 exec("INSERT INTO commands VALUES(?,?,?,?)",id,fp,doc,remaining)
 exec("INSERT INTO outbox VALUES(?)",id)
 if(fail)error("injected")
 tx("COMMIT");return remaining
 }catch(e:Throwable){tx("ROLLBACK");throw e}
 }}
 fun scalar(sql:String):Long=connect().use{c->c.createStatement().use{it.executeQuery(sql).use{r->r.next();r.getLong(1)}}}
}
fun main(){
 val p=Files.createTempFile("proof",".db").toString();val l=Ledger(p);l.init()
 check(l.sell("one",2000)==8000L)
 check(Ledger(p).sell("one",2000)==8000L)
 check(runCatching{l.sell("one",1000)}.isFailure)
 check(runCatching{l.sell("one",2000,"B")}.isFailure)
 check(runCatching{l.sell("fail",1000,fail=true)}.isFailure)
 check(l.scalar("SELECT qty FROM stock WHERE company='A'")==8000L)
 for(t in listOf("documents","commands","outbox"))check(l.scalar("SELECT count(*) FROM "+t)==1L)
 check(l.scalar("SELECT count(*) FROM journal")==4L)
 val pool=Executors.newFixedThreadPool(2)
 try{val jobs=listOf("two","three").map{id->pool.submit<Boolean>{runCatching{Ledger(p).sell(id,8000)}.isSuccess}};check(jobs.count{it.get()}==1)}finally{pool.shutdown()}
 check(l.scalar("SELECT qty FROM stock WHERE company='A'")==0L)
 check(l.scalar("SELECT qty FROM stock WHERE company='B'")==10000L)
 check(l.scalar("SELECT sum(debit-credit) FROM journal")==0L)
 for(t in listOf("documents","commands","outbox"))check(l.scalar("SELECT count(*) FROM "+t)==2L)
 val backup=Files.createTempFile("proof-backup",".db").toString()
 l.connect().use{c->c.prepareStatement("VACUUM INTO ?").use{it.setString(1,backup);it.execute()}}
 val restored=Ledger(backup)
 check(restored.sell("one",2000)==8000L)
 check(restored.scalar("SELECT qty FROM stock WHERE company='A'")==0L)
 check(restored.scalar("SELECT count(*) FROM documents")==2L)
 val q=Ledger(Files.createTempFile("fraction",".db").toString());q.init()
 check(q.sell("fraction",125)==9875L)
 check(q.scalar("SELECT debit FROM journal WHERE account='cash'")==13L)
 check(q.scalar("SELECT debit FROM journal WHERE account='cogs'")==8L)
 for ((qty,rev,cost) in listOf(Triple(1L,0L,0L),Triple(5L,1L,0L),Triple(499L,50L,30L),Triple(500L,50L,30L),Triple(999L,100L,60L))) {
  val file=Files.createTempFile("fraction-boundary",".db")
  try {
   val test=Ledger(file.toString());test.init()
   check(test.sell("boundary",qty)==10000L-qty)
   check(test.scalar("SELECT debit FROM journal WHERE account='cash'")==rev)
   check(test.scalar("SELECT debit FROM journal WHERE account='cogs'")==cost)
   check(test.scalar("SELECT sum(debit-credit) FROM journal")==0L)
  } finally { Files.deleteIfExists(file) }
 }
 check(posting(Long.MAX_VALUE,1000)==Long.MAX_VALUE)
 check(runCatching{posting(Long.MAX_VALUE,1001)}.exceptionOrNull() is ArithmeticException)
 println("SQLite JVM durable, fractional rounding and backup checks PASS")
}