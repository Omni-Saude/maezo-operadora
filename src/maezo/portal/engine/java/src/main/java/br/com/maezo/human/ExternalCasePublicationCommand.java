package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import static br.com.maezo.human.ExternalCaseModels.*;
import static br.com.maezo.human.ExternalCaseStore.numberColumn;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;
import org.cibseven.bpm.engine.impl.interceptor.*;

/** Concrete enlisted external projection/checkpoint CAS; result bytes require COMMITTED. */
public final class ExternalCasePublicationCommand implements Command<ExternalCasePublicationCommand.Result> {
  public static final class Result {
    private byte[] bytes;private boolean committed;private Runnable guard;
    public byte[] bytes(){if(!committed||bytes==null)throw unavailable();guard.run();return bytes.clone();}
  }
  private final Verified verified;
  public ExternalCasePublicationCommand(Verified verified){if(verified==null)throw unavailable();this.verified=verified;}
  @Override public Result execute(CommandContext context){
    verified.current();if(!verified.configuration.purpose().equals("portal-external-case-publication"))throw denied();
    var r=publication(verified.request);if(!r.get("requester_fingerprint").equals(verified.configuration.transportFingerprint()))throw denied();
    var db=new ExternalCaseStore(context,verified.configuration.scope(),verified.admission.statementTimeoutSeconds(),verified.configuration.engineSchema());
    long authorityRevision=db.lock();var old=db.receipt(str(r,"publication_id"));var out=new Result();out.guard=verified::current;
    if(old!=null){if(!verified.digest.equals(old.get("request_digest"))||!r.get("kind").equals(old.get("kind"))
        ||!r.get("requester_fingerprint").equals(old.get("requester_fingerprint")))throw conflict();
      out.bytes=((byte[])old.get("canonical_receipt")).clone();finish(context,out);return out;}
    String outboxTable=r.get("kind").equals("case")?"mzo_external_publication_outbox":"mzo_external_checkpoint_outbox";
    var captured=db.one("SELECT canonical_request,request_digest FROM maezo_external."+outboxTable+" WHERE "+ExternalCaseStore.S+" AND publication_id=?",db.args(r.get("publication_id")));
    if(captured==null||!verified.digest.equals(captured.get("request_digest"))||!Arrays.equals(bounded(r),(byte[])captured.get("canonical_request")))throw denied();
    Authority authority=db.authority(verified.configuration);
    if(authority.revoked.contains(verified.configuration.transportFingerprint()))throw denied();
    out.guard=()->{verified.current();authority.current();};
    Map<String,Object> receipt;
    if(r.get("kind").equals("case"))receipt=project(db,r,authority,authorityRevision);
    else receipt=checkpoint(db,r,authority,authorityRevision);
    out.bytes=bounded(receipt);out.guard.run();finish(context,out);return out;
  }
  private void finish(CommandContext context,Result out){
    context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->out.guard.run());
    context.getTransactionContext().addTransactionListener(TransactionState.COMMITTED,ignored->out.committed=true);
  }
  private void receipt(ExternalCaseStore db,Map<String,Object> r,Map<String,Object> receipt){
    db.write("INSERT INTO maezo_external.mzo_external_publication_receipt VALUES(?,?,?,?,?,?,?,?,?,?)",
      db.args(r.get("publication_id"),r.get("kind"),verified.digest,r.get("requester_fingerprint"),bounded(receipt),Timestamp.from(time(receipt.get("committed_at")))));
  }
  private Map<String,Object> project(ExternalCaseStore db,Map<String,Object> r,Authority authority,long authorityRevision){
    var packet=obj(r,"packet");var source=authority.source(packet);var identity=obj(source,"identity");
    var ingress=obj(r,"ingress_receipt");String ref=str(source,"source_ref");long rev=number(source.get("source_revision"));
    var head=db.head(ref);var event=db.event(ref,rev);
    if(head==null||event==null||rev!=numberColumn(head,"source_revision")||!hash(packet).equals(head.get("payload_digest"))
        ||!Arrays.equals(bounded(packet),(byte[])event.get("canonical_payload"))
        ||!ingress.get("ingress_id").equals(event.get("ingress_id"))
        ||number(ingress.get("source_generation"))!=numberColumn(event,"source_generation"))throw conflict();
    var storedIngress=db.one("SELECT canonical_receipt FROM maezo_external.mzo_external_ingress_receipt WHERE "+ExternalCaseStore.S+" AND ingress_id=?",db.args(ingress.get("ingress_id")));
    if(storedIngress==null||!Arrays.equals(bounded(ingress),(byte[])storedIngress.get("canonical_receipt")))throw denied();
    var reservation=db.one("SELECT case_ref FROM maezo_external.mzo_external_case_reservation WHERE "+ExternalCaseStore.S+" AND upstream_resource_key=?",db.args(identity.get("upstream_resource_key")));
    if(reservation==null||!identity.get("case_ref").equals(reservation.get("case_ref"))||!identity.equals(ExternalCaseStore.json(head.get("identity"))))throw denied();
    db.nativeState(identity);
    var prior=db.one("SELECT identity FROM maezo_external.mzo_external_case WHERE "+ExternalCaseStore.S+" AND case_ref=?",db.args(identity.get("case_ref")));
    if(prior!=null&&!identity.equals(ExternalCaseStore.json(prior.get("identity"))))throw conflict();
    var covered=new ExternalCaseStore.CoverageDigest();
    String older="e.tenant=? AND e.environment=? AND e.engine_name=? AND e.database_incarnation=? AND e.source_ref=? AND e.source_revision<? AND NOT EXISTS(SELECT 1 FROM maezo_external.mzo_external_event_terminal t WHERE t.tenant=e.tenant AND t.environment=e.environment AND t.engine_name=e.engine_name AND t.database_incarnation=e.database_incarnation AND t.source_generation=e.source_generation)";
    db.stream("SELECT e.source_generation,e.source_ref,e.source_revision,e.payload_digest FROM maezo_external.mzo_external_source_event e WHERE "+older+" ORDER BY e.source_generation",
      ev->{verified.current();authority.current();covered.add(record("source_generation",ev.get("source_generation").toString(),"source_ref",ev.get("source_ref"),"source_revision",ev.get("source_revision").toString(),"source_digest",ev.get("payload_digest")));},db.args(ref,rev));
    String coverageDigest=covered.finish();
    String projection=hash(record("identity",identity,"owners",source.get("owners"),"grants",source.get("disclosure_grants"),"state",source.get("state")));
    var result=record("schema","portal-external-case-publication-receipt.v1","kind","case","scope",r.get("scope"),"publication_id",r.get("publication_id"),"request_digest",verified.digest,"requester_fingerprint",r.get("requester_fingerprint"),"source_ref",ref,"source_revision",source.get("source_revision"),"source_digest",hash(packet),"source_generation",ingress.get("source_generation"),"upstream_receipts_digest",ingress.get("upstream_receipts_digest"),"projection_digest",projection,"coverage_digest",coverageDigest,"coverage_count",Long.toString(covered.count),"authority_revision",Long.toString(authorityRevision),"designation_digest",authority.designation,"committed_at",time(Instant.now()));
    receipt(db,r,result);
    db.write("INSERT INTO maezo_external.mzo_external_case VALUES(?,?,?,?,?,?,?,CAST(? AS jsonb),?,?,?,?) ON CONFLICT(tenant,environment,engine_name,database_incarnation,case_ref) DO UPDATE SET source_revision=excluded.source_revision,owners_canonical=excluded.owners_canonical,projection_digest=excluded.projection_digest,publication_id=excluded.publication_id,revoked=excluded.revoked",
      db.args(identity.get("case_ref"),ref,rev,new String(bounded(identity),java.nio.charset.StandardCharsets.UTF_8),bounded(source.get("owners")),projection,r.get("publication_id"),source.get("state").equals("revoked")));
    db.write("DELETE FROM maezo_external.mzo_external_case_grant WHERE "+ExternalCaseStore.S+" AND case_ref=?",db.args(identity.get("case_ref")));
    for(Object g:list(source.get("disclosure_grants"))){var grant=map(g);for(Object operation:list(grant.get("operations")))
      db.write("INSERT INTO maezo_external.mzo_external_case_grant VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        db.args(identity.get("case_ref"),grant.get("grant_ref"),operation,grant.get("principal_ref"),grant.get("issuer"),grant.get("subject"),number(grant.get("membership_revision")),grant.get("audience"),rev,bounded(grant),Timestamp.from(time(grant.get("valid_until"))),grant.get("state").equals("revoked")));}
    long coveredRows=db.write("INSERT INTO maezo_external.mzo_external_event_terminal SELECT e.tenant,e.environment,e.engine_name,e.database_incarnation,e.source_generation,e.source_ref,e.source_revision,e.payload_digest,'covered',?,?,? FROM maezo_external.mzo_external_source_event e WHERE "+older,
      prefix(new Object[]{r.get("publication_id"),rev,hash(packet)},db.args(ref,rev)));
    if(coveredRows!=covered.count)throw conflict();
    terminal(db,r,record("source_generation",ingress.get("source_generation"),"source_ref",ref,"source_revision",source.get("source_revision"),"source_digest",hash(packet)),"projected",rev,hash(packet));
    var next=db.one("SELECT coalesce(min(e.source_generation)-1,g.accepted_generation) AS watermark FROM maezo_external.mzo_external_source_generation g LEFT JOIN maezo_external.mzo_external_source_event e ON e.tenant=g.tenant AND e.environment=g.environment AND e.engine_name=g.engine_name AND e.database_incarnation=g.database_incarnation AND NOT EXISTS(SELECT 1 FROM maezo_external.mzo_external_event_terminal t WHERE t.tenant=e.tenant AND t.environment=e.environment AND t.engine_name=e.engine_name AND t.database_incarnation=e.database_incarnation AND t.source_generation=e.source_generation) WHERE g.tenant=? AND g.environment=? AND g.engine_name=? AND g.database_incarnation=? GROUP BY g.accepted_generation",db.args());
    var generation=db.generation();
    if(numberColumn(next,"watermark")<numberColumn(generation,"published_generation")||numberColumn(next,"watermark")>numberColumn(generation,"accepted_generation"))throw conflict();
    db.write("UPDATE maezo_external.mzo_external_source_generation SET published_generation=? WHERE "+ExternalCaseStore.S,
      prepend(numberColumn(next,"watermark"),db.args()));
    return result;
  }
  private void terminal(ExternalCaseStore db,Map<String,Object> r,Map<String,Object> e,String kind,long coveringRevision,String coveringDigest){
    db.write("INSERT INTO maezo_external.mzo_external_event_terminal VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
      db.args(number(e.get("source_generation")),e.get("source_ref"),number(e.get("source_revision")),e.get("source_digest"),kind,r.get("publication_id"),coveringRevision,coveringDigest));
  }
  private Map<String,Object> checkpoint(ExternalCaseStore db,Map<String,Object> r,Authority authority,long authorityRevision){
    var packet=obj(r,"packet");var statement=authority.checkpoint(packet);var ingress=obj(r,"ingress_receipt");
    var event=db.one("SELECT canonical_signed_checkpoint,canonical_ingress_receipt FROM maezo_external.mzo_external_checkpoint_event WHERE "+ExternalCaseStore.S+" AND checkpoint_ref=? AND epoch=?",db.args(statement.get("checkpoint_ref"),number(statement.get("epoch"))));
    var accepted=db.one("SELECT checkpoint_digest FROM maezo_external.mzo_external_checkpoint_accepted WHERE "+ExternalCaseStore.S,db.args());
    if(event==null||accepted==null||!hash(packet).equals(accepted.get("checkpoint_digest"))
        ||!Arrays.equals(bounded(packet),(byte[])event.get("canonical_signed_checkpoint"))
        ||!Arrays.equals(bounded(ingress),(byte[])event.get("canonical_ingress_receipt")))throw conflict();
    db.exactCheckpoint(statement);
    for(Object item:list(statement.get("heads"))){var h=map(item);var ev=db.event(str(h,"source_ref"),number(h.get("source_revision")));
      if(ev==null)throw unavailable();authority.source(canonical((byte[])ev.get("canonical_payload")));}
    var g=db.generation();
    var result=record("schema","portal-external-checkpoint-publication-receipt.v1","kind","checkpoint","scope",r.get("scope"),"publication_id",r.get("publication_id"),"request_digest",verified.digest,"requester_fingerprint",r.get("requester_fingerprint"),"checkpoint_ref",statement.get("checkpoint_ref"),"epoch",statement.get("epoch"),"checkpoint_digest",hash(packet),"designation_digest",authority.designation,"heads_digest",statement.get("heads_digest"),"heads_count",statement.get("heads_count"),"accepted_generation",g.get("accepted_generation").toString(),"published_generation",g.get("published_generation").toString(),"authority_revision",Long.toString(authorityRevision),"committed_at",time(Instant.now()));
    receipt(db,r,result);
    db.write("INSERT INTO maezo_external.mzo_external_checkpoint_current VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(tenant,environment,engine_name,database_incarnation) DO UPDATE SET checkpoint_ref=excluded.checkpoint_ref,epoch=excluded.epoch,checkpoint_digest=excluded.checkpoint_digest,designation_digest=excluded.designation_digest,publication_id=excluded.publication_id,accepted_generation=excluded.accepted_generation,published_generation=excluded.published_generation,valid_until=excluded.valid_until",
      db.args(statement.get("checkpoint_ref"),number(statement.get("epoch")),hash(packet),authority.designation,r.get("publication_id"),numberColumn(g,"accepted_generation"),numberColumn(g,"published_generation"),Timestamp.from(authority.until())));
    return result;
  }
  static Object[] prefix(Object[] first,Object[] rest){var all=Arrays.copyOf(first,first.length+rest.length);System.arraycopy(rest,0,all,first.length,rest.length);return all;}
  static Object[] prepend(Object value,Object[] values){var result=new Object[values.length+1];result[0]=value;System.arraycopy(values,0,result,1,values.length);return result;}
}
