package br.com.maezo.workload;

import jakarta.servlet.http.*;
import java.io.IOException;
import java.util.*;

/** Direct mTLS native webapp: no reliance on engine-rest's separate auth filter. */
public final class WorkloadV2Servlet extends HttpServlet {
  @Override protected void service(HttpServletRequest request,HttpServletResponse response)throws IOException {
    response.setContentType("application/json");response.setCharacterEncoding("UTF-8");response.setHeader("Cache-Control","no-store");response.setHeader("X-Content-Type-Options","nosniff");
    NativeOutcomeV2.Encoded result;
    NativeAuthDocumentProducerV2.Response contextResponse=null;
    try {
      if(!BoundaryPolicyV2.route(request))throw Refused.denied();
      var plugin=WorkloadPlugin.running();var policy=plugin.v2();var peer=policy.authenticate(request);
      var identities=plugin.engine().getIdentityService();var prior=identities.getCurrentAuthentication();
      try {
        identities.setAuthentication(peer.engineUser(),List.of(),List.of(policy.transport.tenant));
        String path=request.getRequestURI();
        if(path.equals("/maezo-workload/v2/readiness"))result=plugin.executeV2(new NativeOperationV2.ReadCommand(plugin,peer,null,"",null));
        else {
          byte[] raw=request.getInputStream().readNBytes(Json.LIMIT+1);var body=Json.parse(raw);String digest=NativeOutcomeV2.bodyDigest(raw);
          if(path.equals("/maezo-workload/v2/outcomes"))result=plugin.executeV2(new NativeOperationV2.ReadCommand(plugin,peer,body,digest,null));
          else if(path.equals("/maezo-workload/v2/auth-document-request-context")) {
            var selected=br.com.maezo.human.HumanCommandPlugin.documentProducerContext(plugin.engine().getName());
            var observed=new NativeAuthDocumentProducerV2(plugin,selected).execute(peer,raw);
            contextResponse=observed;
            result=new NativeOutcomeV2.Encoded(observed.status(),observed.body());
          }
          else {
            var cap=policy.capability(peer,NativeOutcomeV2.digest(body,"capability_digest"));cap.validate(body);
            var command=new NativeOperationV2(plugin,peer,cap,body,digest);
            try {
              if("fetch_lock".equals(cap.schema.get("operation"))) {
                long poll=Json.number(Json.object(body.get("parameters")),"asyncResponseTimeout");
                if(poll>policy.transport.maxPollMillis)throw Refused.body();
                long start=System.nanoTime();
                while(System.nanoTime()-start<java.util.concurrent.TimeUnit.MILLISECONDS.toNanos(poll)){
                  var probe=plugin.executeV2(new NativeOperationV2.ReadCommand(plugin,peer,body,digest,cap));
                  if(probe.status()==200 && Json.bool(Json.parse(probe.bytes()),"candidate"))break;
                  if(Thread.currentThread().isInterrupted())throw Refused.unavailable();
                  java.util.concurrent.locks.LockSupport.parkNanos(Math.min(100000000L,Math.max(1,java.util.concurrent.TimeUnit.MILLISECONDS.toNanos(poll)-(System.nanoTime()-start))));
                  policy.current(peer);
                }
              }
              result=plugin.executeV2(command);
            }catch(RuntimeException e){
              // Physical commit may already have happened, even if COMMITTED/close failed.
              // Never retry or assert rollback; exact outcome retrieval owns reconciliation.
              result=NativeOutcomeV2.operation(command.binding(),"unavailable",null,null,List.of());
            }
          }
        }
        policy.current(peer);
      }finally {
        if(prior==null)identities.clearAuthentication();else identities.setAuthentication(prior);
      }
    }catch(Refused e){result=NativeOutcomeV2.refusal(e.status==400?"invalid_request":e.status==403?"denied":"unavailable");}
    catch(RuntimeException e){result=NativeOutcomeV2.refusal("unavailable");}
    // Context alone retains its original authorization ceiling through policy I/O
    // and the identity-restoration finally block; old v2 routes remain unchanged.
    if(contextResponse!=null && result.status()==200) {contextResponse.writeTo(response);return;}
    response.setStatus(result.status());response.getOutputStream().write(result.bytes());
  }
}
