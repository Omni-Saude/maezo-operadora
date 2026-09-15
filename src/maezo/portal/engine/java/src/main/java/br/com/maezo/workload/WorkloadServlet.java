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
        result=plugin.execute(peer,Json.parse(raw));
      } else throw Refused.denied();
      response.setStatus(200);response.getOutputStream().write(result);
    } catch(Refused e) {BoundaryFilter.error(response,e);}
    catch(AuthorizationException e) {BoundaryFilter.error(response,Refused.denied());}
    catch(OptimisticLockingException e) {response.setStatus(409);response.getOutputStream().write(Json.bytes(java.util.Map.of("error","engine_revision_conflict")));}
    catch(RuntimeException e) {BoundaryFilter.error(response,Refused.unavailable());}
  }
}
