-- Isolated proof only. Applied as v3_proof_gap_free_submit.
create or replace function v3_sync_proof.submit(p_company uuid,p_operation uuid,p_payload jsonb) returns jsonb language plpgsql security invoker set search_path='' as $$
declare a uuid:=auth.uid(); r v3_sync_proof.operations%rowtype;
begin
if a is null or not exists(select 1 from v3_sync_proof.members where company_id=p_company and user_id=a) then raise exception using errcode='42501',message='SYNC_DENIED'; end if;
if p_operation is null or p_payload is null or jsonb_typeof(p_payload)<>'object' or octet_length(p_payload::text)>16384 then raise exception using errcode='22023',message='SYNC_INVALID'; end if;
perform 1 from v3_sync_proof.companies where id=p_company for update;
select * into r from v3_sync_proof.operations where company_id=p_company and operation_id=p_operation;
if not found then
insert into v3_sync_proof.operations(company_id,operation_id,actor_id,payload) values(p_company,p_operation,a,p_payload) returning * into r;
end if;
if r.payload is distinct from p_payload or r.actor_id<>a then raise exception using errcode='P0001',message='SYNC_CONFLICT'; end if;
return jsonb_build_object('operation_id',r.operation_id,'received_at',r.received_at,'payload',r.payload,'revision',r.revision);
end $$;
revoke all on function v3_sync_proof.submit(uuid,uuid,jsonb) from public,anon,authenticated;
update v3_sync_proof.companies c set sync_revision=0 where not exists(select 1 from v3_sync_proof.operations o where o.company_id=c.id);
