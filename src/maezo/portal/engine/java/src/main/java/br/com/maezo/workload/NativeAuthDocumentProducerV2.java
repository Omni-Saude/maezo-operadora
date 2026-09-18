package br.com.maezo.workload;

import java.time.Instant;
import java.io.IOException;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.LongSupplier;
import jakarta.servlet.http.HttpServletResponse;
import java.util.*;
import br.com.maezo.human.AuthDocumentProducerContext;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;

/** Separate producer read, current fetch preflight only; no acquisition or effect writes. */
public final class NativeAuthDocumentProducerV2 {
  public record Response(int status,byte[] body,long validUntil) {
    public Response {if(status!=200 || validUntil<=0)throw Refused.unavailable();body=body.clone();}
    @Override public byte[] body(){return body.clone();}
    /** Route-only release after engine, policy I/O and identity restoration. */
    public void writeTo(HttpServletResponse response)throws IOException {
      writeTo(response,System::currentTimeMillis);
    }
    void writeTo(HttpServletResponse response,LongSupplier clock)throws IOException {
      byte[] frozen=body();
      var output=response.getOutputStream();
      response.setStatus(status);
      // No source reload or I/O between this original-deadline check and body write.
      if(clock.getAsLong()>=validUntil) {
        var unavailable=NativeOutcomeV2.refusal("unavailable");
        response.setStatus(unavailable.status());output.write(unavailable.bytes());return;
      }
      output.write(frozen);
    }
  }
  private final WorkloadPlugin plugin;
  private final AuthDocumentProducerContext auth;
  public NativeAuthDocumentProducerV2(WorkloadPlugin plugin,AuthDocumentProducerContext auth) {
    this.plugin=Objects.requireNonNull(plugin);this.auth=Objects.requireNonNull(auth);
  }
  public Response execute(BoundaryPolicy.Peer peer,byte[] raw) {
    var query=Json.parse(raw);validate(query);
    var response=new AtomicReference<Response>();
    var value=plugin.executeV2(context->read(context,peer,query,NativeOutcomeV2.bodyDigest(raw),response));
    var frozen=response.get();
    if(frozen==null || frozen.status()!=value.status() || !Arrays.equals(frozen.body(),value.bytes()))throw Refused.unavailable();
    return frozen;
  }
  static void validate(Map<String,Object> q) {
    Json.keys(q,"protocol","designation_digest","query_id","reader_activation_ref","fetch_command","resource_ref","resource_acquisition");
    if(!"maezo.auth-document-producer-query.v1".equals(q.get("protocol")))throw Refused.body();
    NativeOutcomeV2.digest(q,"designation_digest");NativeOutcomeV2.ref(q,"query_id");
    Json.token(q,"reader_activation_ref");Json.token(q,"resource_ref");NativeOutcomeV2.reference(q.get("resource_acquisition"));
    if(!"fetch_lock".equals(NativeOutcomeV2.command(q.get("fetch_command")).get("operation")))throw Refused.body();
  }
  private NativeOutcomeV2.Publication read(CommandContext ctx,BoundaryPolicy.Peer peer,Map<String,Object> query,String digest,AtomicReference<Response> response) {
    boolean prior=WorkloadPlugin.GUARDED.get();WorkloadPlugin.GUARDED.set(true);
    try {
      var policy=plugin.v2();var command=NativeOutcomeV2.command(query.get("fetch_command"));
      var cap=policy.capability(peer,Json.token(command,"capability_digest"));
      if(!"fetch_lock".equals(cap.schema.get("operation"))||!"runtime".equals(policy.admission.get("purpose"))
        ||!"ACTIVE".equals(policy.admission.get("admission_phase"))
        ||!query.get("reader_activation_ref").equals(policy.admission.get("activation_ref"))
        ||!peer.identity().equals(command.get("identity"))||!peer.engineUser().equals(command.get("native_user")))throw Refused.denied();
      var admission=new NativeAdmissionV2(policy,peer,ctx,plugin.engine(),cap.digest,cap.binding,"readiness");admission.grants(cap);
      // R receipt lock precedes designation/occurrence and P/T/L acquisition locks.
      var receiptRow=admission.call("read_receipt_v2",Map.of("command",command,"history",false));
      if(!"committed".equals(receiptRow.get("status")))throw Refused.resource();
      var receipt=NativeOutcomeV2.receipt(receiptRow.get("receipt"),command);
      var lease=auth.open(ctx,Json.token(query,"designation_digest"));var designation=lease.designation();
      if(!designation.get("identity").equals(peer.identity())||!designation.get("native_user").equals(peer.engineUser())
        ||!designation.get("fetch_capability_digest").equals(cap.digest)||!designation.get("target").equals(cap.target))throw Refused.denied();
      String task=Json.token(query,"resource_ref");var reference=NativeOutcomeV2.reference(query.get("resource_acquisition"));
      boolean selected=Json.list(receipt.get("acquisitions")).stream().map(Json::object).anyMatch(s->
        "resource".equals(s.get("role"))&&task.equals(s.get("task_ref"))&&Long.valueOf(1).equals(s.get("lease_revision"))
        &&reference.get("acquisition_ref").equals(s.get("acquisition_ref")));
      if(!selected)throw Refused.resource();
      var store=new NativeAcquisitionStoreV2(admission);
      store.lock(List.of(task),List.of(),NativeAcquisitionStoreV2.selection(query,List.of()));
      var acquisition=store.consume(task,reference,cap.target,cap.worker,peer.identity(),peer.engineUser(),cap.digest);
      if(!command.get("command_id").equals(acquisition.get("command_id"))||!command.get("request_digest").equals(acquisition.get("request_digest")))throw Refused.resource();
      var taskRow=store.task(task);String instance=Json.token(taskRow,"process"),execution=Json.token(taskRow,"execution"),definition=Json.token(taskRow,"definition");
      var context=lease.read(task,instance,execution,definition);
      long deadline=Math.min(Math.min(admission.deadline,lease.validUntil()),Json.number(acquisition,"lock_expires_at"));
      lease.audit(Json.token(query,"query_id"),digest,Json.token(reference,"acquisition_ref"));
      var publication=new NativeOutcomeV2.Publication();
      ctx.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->{
        var actual=store.rows(List.of(task)).get(task);
        NativeAcquisitionStoreV2.checkConsumed(acquisition,reference,task,actual,cap.target,cap.worker,peer.identity(),peer.engineUser(),cap.digest,policy.admission,admission.now());
        if(!context.equals(lease.read(task,instance,execution,definition)))throw Refused.resource();
        lease.currentRows();admission.grantsFinal(cap);lease.currentLocal();
        long now=Instant.now().toEpochMilli();if(now>=deadline)throw Refused.unavailable();
        Map<String,Object> result=new HashMap<>(query);result.remove("protocol");
        result.put("protocol","maezo.auth-document-producer-result.v1");result.put("query_digest",digest);
        result.put("observed_at",now);result.put("valid_until",deadline);result.put("context",context);
        var encoded=new NativeOutcomeV2.Encoded(200,Json.bytes(result));
        if(Instant.now().toEpochMilli()>=deadline)throw Refused.unavailable();
        if(!response.compareAndSet(null,new Response(encoded.status(),encoded.bytes(),deadline)))throw Refused.unavailable();
        publication.prepare(encoded);
      });
      ctx.getTransactionContext().addTransactionListener(TransactionState.COMMITTED,ignored->publication.publish());
      return publication;
    } finally {if(prior)WorkloadPlugin.GUARDED.set(true);else WorkloadPlugin.GUARDED.remove();}
  }
}
