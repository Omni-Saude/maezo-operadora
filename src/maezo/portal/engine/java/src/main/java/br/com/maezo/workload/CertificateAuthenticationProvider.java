package br.com.maezo.workload;

import jakarta.servlet.http.*;
import java.util.List;
import org.cibseven.bpm.engine.ProcessEngine;
import org.cibseven.bpm.engine.rest.security.auth.*;

/** Actual CIB 2.1 Jakarta AuthenticationProvider ABI; packaged in WEB-INF/lib only. */
public final class CertificateAuthenticationProvider implements AuthenticationProvider {
  @Override public AuthenticationResult extractAuthenticatedUser(HttpServletRequest request,ProcessEngine engine) {
    try {
      BoundaryPolicy.Peer peer=WorkloadPlugin.running().nativePeer(request,engine.getName());
      AuthenticationResult result=AuthenticationResult.successful(peer.engineUser());
      result.setGroups(List.of());result.setTenants(List.of((String)peer.identity().get("tenant")));return result;
    } catch(Refused e){return AuthenticationResult.unsuccessful();}
  }
  @Override public void augmentResponseByAuthenticationChallenge(HttpServletResponse response,ProcessEngine engine) {
    response.setHeader("Cache-Control","no-store"); // No Basic/Bearer challenge or credential fallback.
  }
}
