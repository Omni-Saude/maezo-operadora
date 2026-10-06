package br.com.maezo.workload;

import jakarta.servlet.http.*;
import java.io.IOException;
import org.cibseven.bpm.engine.*;

/** Closed versioned workload endpoint mounted inside authenticated engine-rest. */
public final class WorkloadServlet extends HttpServlet {
  @Override protected void service(HttpServletRequest request,HttpServletResponse response)throws IOException {
    response.setContentType("application/json");response.setCharacterEncoding("UTF-8");response.setHeader("Cache-Control","no-store");
    try {
      WorkloadPlugin plugin=WorkloadPlugin.running();
      BoundaryPolicy.Peer peer=plugin.policy().authenticate(request);
      BoundaryFilter.route(request,peer.purpose());
      byte[] result;
      if("GET".equals(request.getMethod()) && "/v1/readiness".equals(request.getPathInfo()))result=plugin.readiness(peer);
      else if("POST".equals(request.getMethod()) && "/v1/operations".equals(request.getPathInfo())) {
        byte[] raw=request.getInputStream().readNBytes(Json.LIMIT+1);
        var fields=Json.parse(raw);
        if("read_runtime_definition".equals(fields.get("operation"))) {
          response.setHeader("X-Content-Type-Options","nosniff");
          var emission=plugin.executeObservation(peer,new VerifiedObservationRequest(raw));
          response.setStatus(200);emission.writeVerified(response);return;
        }
        result=plugin.execute(peer,fields);
      } else throw Refused.denied();
      response.setStatus(200);response.getOutputStream().write(result);
    } catch(Refused e) {BoundaryFilter.error(response,e);}
    catch(AuthorizationException e) {BoundaryFilter.error(response,Refused.denied());}
    catch(OptimisticLockingException e) {response.setStatus(409);response.getOutputStream().write(Json.bytes(java.util.Map.of("error","engine_revision_conflict")));}
    catch(RuntimeException e) {BoundaryFilter.error(response,Refused.unavailable());}
  }
  /** Only authenticated ingress can construct this raw, detached request carrier. */
  static final class VerifiedObservationRequest {
    private final java.util.Map<String,Object> fields;
    private final String digest;
    private VerifiedObservationRequest(byte[] received) {
      byte[] copy=received.clone();fields=Json.parse(copy);Capability.validateEnvelope(fields);
      RuntimeObservationAdmission.validate("Request",fields);
      digest=br.com.maezo.human.Jcs.digest(copy);
    }
    java.util.Map<String,Object> fields(){return fields;}
    String digest(){return digest;}
  }
}
