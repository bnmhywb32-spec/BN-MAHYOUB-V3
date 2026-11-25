import 'dart:io';
import 'dart:isolate';
import 'package:sqlite3/sqlite3.dart';
void require(bool ok){if(!ok)throw StateError('check failed');}
Database open(String p)=>sqlite3.open(p)..execute('PRAGMA busy_timeout=10000');
int sell(String p,String id,int qty,{String company='A',String actor='cashier-A',bool fail=false}){
 require(company=='A' && actor=='cashier-A' && qty>0 && qty%1000==0);
 final db=open(p);
 try{
 db.execute('BEGIN IMMEDIATE');
 try{
 final fp='A/A1/$qty';
 final old=db.select('SELECT fingerprint,remaining FROM commands WHERE id=?',[id]);
 if(old.isNotEmpty){require(old.first['fingerprint']==fp);db.execute('COMMIT');return old.first['remaining'] as int;}
 db.execute("UPDATE stock SET qty=qty-? WHERE company='A' AND branch='A1' AND qty>=?",[qty,qty]);
 require(db.updatedRows==1);
 final remaining=db.select("SELECT qty FROM stock WHERE company='A'").first['qty'] as int;
 final doc='DOC-$id';
 db.execute('INSERT INTO documents VALUES(?,?)',[doc,qty]);
 final rev=qty~/1000*100,cost=qty~/1000*60;
 for(final row in [['cash',rev,0],['revenue',0,rev],['cogs',cost,0],['inventory',0,cost]]){db.execute('INSERT INTO journal VALUES(?,?,?,?)',[doc,...row]);}
 db.execute('INSERT INTO commands VALUES(?,?,?,?)',[id,fp,doc,remaining]);
 db.execute('INSERT INTO outbox VALUES(?)',[id]);
 if(fail)throw StateError('injected');
 db.execute('COMMIT');return remaining;
 }catch(e){db.execute('ROLLBACK');rethrow;}
 }finally{db.dispose();}
}
bool rejects(void Function() f){try{f();return false;}catch(_){return true;}}
int scalar(String p,String sql){final db=open(p);try{return db.select(sql).first.values.first as int;}finally{db.dispose();}}
Future<void> main()async{
 final dir=Directory.systemTemp.createTempSync('proof');final p='${dir.path}/ledger.db';
 final db=open(p);for(final s in File('../schema.sql').readAsStringSync().split(';')){if(s.trim().isNotEmpty)db.execute(s);}db.dispose();
 require(sell(p,'one',2000)==8000);
 require(sell(p,'one',2000)==8000);
 require(rejects(()=>sell(p,'one',1000)));
 require(rejects(()=>sell(p,'one',2000,company:'B')));
 require(rejects(()=>sell(p,'fail',1000,fail:true)));
 require(scalar(p,"SELECT qty FROM stock WHERE company='A'")==8000);
 for(final t in ['documents','commands','outbox']){require(scalar(p,'SELECT count(*) FROM $t')==1);}
 require(scalar(p,'SELECT count(*) FROM journal')==4);
 final results=await Future.wait(['two','three'].map((id)=>Isolate.run(()=>!rejects(()=>sell(p,id,8000)))));
 require(results.where((v)=>v).length==1);
 require(scalar(p,"SELECT qty FROM stock WHERE company='A'")==0);
 require(scalar(p,"SELECT qty FROM stock WHERE company='B'")==10000);
 require(scalar(p,'SELECT sum(debit-credit) FROM journal')==0);
 for(final t in ['documents','commands','outbox']){require(scalar(p,'SELECT count(*) FROM $t')==2);}
 dir.deleteSync(recursive:true);print('SQLite Dart native durable checks PASS');
}