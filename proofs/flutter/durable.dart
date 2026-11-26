import 'dart:io';
import 'dart:isolate';
import 'package:sqlite3/sqlite3.dart';
void require(bool ok){if(!ok)throw StateError('check failed');}
int posting(int qty,int price){
 if(qty<=0 || price<0)throw ArgumentError('INVALID_AMOUNT');
 final value=(BigInt.from(qty)*BigInt.from(price)+BigInt.from(500))~/BigInt.from(1000);
 if(value>BigInt.parse('9223372036854775807'))throw StateError('AMOUNT_OVERFLOW');
 return value.toInt();
}
Database open(String p)=>sqlite3.open(p)..execute('PRAGMA busy_timeout=10000');
int sell(String p,String id,int qty,{String company='A',String actor='cashier-A',bool fail=false}){
 require(company=='A' && actor=='cashier-A' && qty>0);
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
 final rev=posting(qty,100),cost=posting(qty,60);
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
 final backup='${dir.path}/backup.db';
 final source=open(p);source.execute('VACUUM INTO ?',[backup]);source.dispose();
 require(sell(backup,'one',2000)==8000);
 require(scalar(backup,"SELECT qty FROM stock WHERE company='A'")==0);
 require(scalar(backup,'SELECT count(*) FROM documents')==2);
 final fraction='${dir.path}/fraction.db';
 final f=open(fraction);for(final s in File('../schema.sql').readAsStringSync().split(';')){if(s.trim().isNotEmpty)f.execute(s);}f.dispose();
 require(sell(fraction,'fraction',125)==9875);
 require(scalar(fraction,"SELECT debit FROM journal WHERE account='cash'")==13);
 require(scalar(fraction,"SELECT debit FROM journal WHERE account='cogs'")==8);
 for(final (qty,rev,cost) in [(1,0,0),(5,1,0),(499,50,30),(500,50,30),(999,100,60)]){
  final path='${dir.path}/boundary-$qty.db';
  final db=open(path);try{for(final s in File('../schema.sql').readAsStringSync().split(';')){if(s.trim().isNotEmpty)db.execute(s);}}finally{db.dispose();}
  require(sell(path,'boundary',qty)==10000-qty);
  require(scalar(path,"SELECT debit FROM journal WHERE account='cash'")==rev);
  require(scalar(path,"SELECT debit FROM journal WHERE account='cogs'")==cost);
  require(scalar(path,'SELECT sum(debit-credit) FROM journal')==0);
 }
 require(posting(9223372036854775807,1000)==9223372036854775807);
 require(rejects(()=>posting(9223372036854775807,1001)));
 dir.deleteSync(recursive:true);print('SQLite Dart native durable, fractional rounding and backup checks PASS');
}