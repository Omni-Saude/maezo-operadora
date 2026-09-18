package br.com.maezo.human;

import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.impl.interceptor.*;

/** Fresh authorization for historical effect receipts and exact current request bindings. */
final class AuthReadCommand implements Command<AuthRuntime.Result> {
  private final AuthRuntime runtime;private final byte[] raw;private final String peer,purpose;
  AuthReadCommand(AuthRuntime runtime,byte[] raw,String peer,String purpose){this.runtime=runtime;this.raw=raw.clone();this.peer=peer;this.purpose=purpose;}
  @Override public AuthRuntime.Result execute(CommandContext context) {
    var invocation=runtime.invoke(context,raw,peer,purpose);var c=invocation.envelope.command();var db=invocation.store;
    if(purpose.equals("human-auth-publication-read")) {
      var row=db.optional("SELECT REQUEST_ FROM MZO_AUTH_INPUT_VERSION WHERE TENANT_=? AND PUBLICATION_=?",db.tenant,Jcs.ref(c,"publication_id"));
      if(row!=null){var publication=AuthStore.parse(row.get("request_"));invocation.envelope.key().requireSource(Jcs.string(publication,"kind"),Jcs.object(publication.get("source")),Jcs.ref(publication,"resource_ref"));}
      var receipt=db.publication(Jcs.ref(c,"publication_id"),Jcs.hash(c,"expected_digest"));
      var result=PortalReadModels.record("schema","human-auth-publication-lookup.v1","scope",runtime.scope,
        "query_id",c.get("query_id"),"query_digest",invocation.envelope.digest(),"status",receipt==null?"absent":"committed",
        "receipt",receipt,"observed_at",PortalReadModels.time(Instant.now()));
      AuthModels.validate("publication-lookup",result);
      return invocation.finish(()->result,()->{},invocation.envelope.expiresAt());
    }
    var inputs=new AuthInputs(db);var actor=Jcs.object(c.get("actor"));
    if(c.get("schema").equals("human-auth-receipt-query.v1")) {
      String resource=Jcs.ref(c,"intake_or_case_ref");
      inputs.readAuthority(actor,"auth.receipt.read",c.get("operation").equals("auth.start")?"intake":"case",resource,null,Instant.now());
      var receipt=db.effectReceipt(Jcs.ref(c,"command_id"),Jcs.hash(c,"expected_command_digest"));
      if(receipt!=null&&(!c.get("operation").equals(receipt.get("operation"))
          ||!actor.get("principal_ref").equals(receipt.get("actor_principal_ref"))
          ||!resource.equals(receipt.get(c.get("operation").equals("auth.start")?"intake_ref":"case_ref"))))throw Rejected.denied();
      var result=PortalReadModels.record("schema","human-auth-receipt-lookup.v1","scope",runtime.scope,
        "query_id",c.get("query_id"),"query_digest",invocation.envelope.digest(),"observed_at",PortalReadModels.time(Instant.now()),
        "status",receipt==null?"absent":"committed","receipt",receipt);
      AuthModels.validate("lookup",result);return invocation.finish(()->result,()->inputs.current(Instant.now()),inputs.ceiling());
    }
    String caseRef=Jcs.ref(c,"case_ref"),request=Jcs.ref(c,"request_ref");
    inputs.readAuthority(actor,"auth.document_context.read","case",caseRef,request,Instant.now());
    var row=db.occurrence(request);if(row==null)throw Rejected.denied();
    var occurrence=AuthModels.validate("occurrence",AuthStore.parse(row.get("record_")));
    if(!caseRef.equals(occurrence.get("case_ref")))throw Rejected.denied();
    if(occurrence.get("binding")!=null&&occurrence.get("state").equals("bound"))AuthNativeWait.require(db,Jcs.object(occurrence.get("binding")),Instant.now());
    // Context identifies native occurrence only. Dispatcher must separately obtain qualified
    // actor/authority/policy/custody/audit publication pins before constructing a mutation.
    var result=PortalReadModels.record("schema","human-auth-document-context.v1","scope",runtime.scope,
      "query_id",c.get("query_id"),"query_digest",invocation.envelope.digest(),"actor",actor,
      "occurrence",occurrence,"input_pins",List.of(),"valid_until",PortalReadModels.time(inputs.ceiling()));
    AuthModels.validate("context",result);
    return invocation.finish(()->result,()->inputs.current(Instant.now()),inputs.ceiling());
  }
}
