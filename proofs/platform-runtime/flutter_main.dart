import 'package:flutter/material.dart';
import 'package:sqflite_common/sqlite_api.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:sqflite_common_ffi_web/sqflite_ffi_web.dart';
import 'package:sqflite/sqflite.dart' as mobile;
import 'package:flutter/foundation.dart';
DatabaseFactory factory() {
 if(kIsWeb)return databaseFactoryFfiWeb;
 if(defaultTargetPlatform==TargetPlatform.windows){sqfliteFfiInit();return databaseFactoryFfi;}
 return mobile.databaseFactory;
}
void check(bool b){if(!b)throw StateError('assertion failed');}
Future<void> proof() async {
 final f=factory(), path='bn_v3_runtime_proof.db';
 await f.deleteDatabase(path);
 Future<Database> open()=>f.openDatabase(path,options:OpenDatabaseOptions(version:1,onCreate:(db,v)async{
 await db.execute('CREATE TABLE stock(company TEXT PRIMARY KEY, qty INTEGER NOT NULL CHECK(qty>=0))');
 await db.execute('CREATE TABLE sales(id TEXT PRIMARY KEY, qty INTEGER, remaining INTEGER)');
 await db.execute('CREATE TABLE journal(id TEXT, account TEXT, debit INTEGER, credit INTEGER, PRIMARY KEY(id,account))');
 await db.execute('CREATE TABLE outbox(id TEXT PRIMARY KEY)');
 await db.insert('stock',{'company':'A','qty':10});await db.insert('stock',{'company':'B','qty':10});
 }));
 var db=await open();
 Future<int> sell(String id,int qty,{bool fail=false})=>db.transaction((t)async{
 final old=await t.query('sales',where:'id=?',whereArgs:[id]);
 if(old.isNotEmpty){check(old.first['qty']==qty);return old.first['remaining'] as int;}
 check(await t.rawUpdate("UPDATE stock SET qty=qty-? WHERE company='A' AND qty>=?",[qty,qty])==1);
 final remaining=(await t.query('stock',where:'company=?',whereArgs:['A'])).first['qty'] as int;
 await t.insert('sales',{'id':id,'qty':qty,'remaining':remaining});
 for(final row in [['cash',qty*100,0],['revenue',0,qty*100],['cogs',qty*60,0],['inventory',0,qty*60]]){
 await t.insert('journal',{'id':id,'account':row[0],'debit':row[1],'credit':row[2]});}
 await t.insert('outbox',{'id':id});
 if(fail)throw StateError('injected');
 return remaining;
 });
 Future<bool> rejects(Future<int> Function() action)async{try{await action();return false;}catch(_){return true;}}
 try{
 check(await sell('one',2)==8);
 check(await sell('one',2)==8);
 check(await rejects(()=>sell('one',1)));
 check(await rejects(()=>sell('fail',1,fail:true)));
 await db.close();db=await open();
 check(await sell('one',2)==8);
 check((await db.query('stock',where:'company=?',whereArgs:['A'])).first['qty']==8);
 check((await db.query('stock',where:'company=?',whereArgs:['B'])).first['qty']==10);
 check((await db.query('sales')).length==1);
 check((await db.query('outbox')).length==1);
 check((await db.query('journal')).length==4);
 check((await db.rawQuery('SELECT SUM(debit-credit) AS balance FROM journal')).first['balance']==0);
 }finally{await db.close();}
}
void main()=>runApp(MaterialApp(home:Scaffold(body:Center(child:FutureBuilder<void>(future:proof(),builder:(c,s)=>Text(s.hasError?'FAIL: ${s.error}':s.connectionState==ConnectionState.done?'PASS: durable platform proof':'RUNNING'))))));
