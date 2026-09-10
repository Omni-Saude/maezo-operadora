package br.com.maezo.human;
import static br.com.maezo.human.PortalReadModels.*;

import jakarta.servlet.http.*;
import java.io.IOException;
import java.security.cert.X509Certificate;
import java.util.*;
import org.cibseven.bpm.engine.OptimisticLockingException;
/** Only fixed POST routes, direct Tomcat mTLS, bounded JSON and constant private errors. */
public final class PortalReadServlet extends HttpServlet {
  protected void service(HttpServletRequest request, HttpServletResponse response)
      throws IOException {
    response.setHeader("Cache-Control", "no-store");
    response.setHeader("Pragma", "no-cache");
    response.setHeader("X-Content-Type-Options", "nosniff");
    response.setContentType("application/json");
    response.setCharacterEncoding("UTF-8");
    try {
      String peer = peer(request);
      String path = request.getPathInfo();
      if (request.getQueryString() != null || !"POST".equals(request.getMethod())
          || !"application/json".equals(request.getContentType())
          || request.getHeader("Cookie") != null || path == null
          || !Set
              .of("/v1/catalog", "/v1/discover", "/v1/task", "/v1/authority", "/v1/disclosure",
                  "/v1/publications")
              .contains(path))
        throw invalid();
      byte[] raw = request.getInputStream().readNBytes(MAX + 1);
      if (raw.length > MAX)
        throw invalid();
      byte[] result = PortalReadPlugin.running().execute(raw, peer, path.substring(4));
      response.setStatus(200);
      response.getOutputStream().write(result);
    } catch (Rejected ex) {
      String code = Set.of("INVALID_REQUEST", "READ_AUTHENTICATION_DENIED", "RESOURCE_UNAVAILABLE",
                           "READ_REVISION_CONFLICT", "READ_DEPENDENCY_UNAVAILABLE")
                        .contains(ex.code)
          ? ex.code
          : (ex.status == 400 ? "INVALID_REQUEST" : "READ_DEPENDENCY_UNAVAILABLE");
      error(response,
          code.equals("INVALID_REQUEST")                  ? 400
              : code.equals("READ_AUTHENTICATION_DENIED") ? 403
              : code.equals("RESOURCE_UNAVAILABLE")       ? 404
              : code.equals("READ_REVISION_CONFLICT")     ? 409
                                                          : 503,
          code);
    } catch (OptimisticLockingException ex) {
      error(response, 409, "READ_REVISION_CONFLICT");
    } catch (RuntimeException ex) {
      error(response, 503, "READ_DEPENDENCY_UNAVAILABLE");
    }
  }
  static String peer(HttpServletRequest request) {
    Object value = request.getAttribute("jakarta.servlet.request.X509Certificate");
    if (!request.isSecure() || !(value instanceof X509Certificate[] chain) || chain.length == 0)
      throw denied();
    try {
      chain[0].checkValidity();
    } catch (java.security.cert.CertificateException ex) {
      throw denied();
    }
    return Jcs.digest(chain[0].getPublicKey().getEncoded());
  }
  static void error(HttpServletResponse response, int status, String code) throws IOException {
    response.setStatus(status);
    response.getOutputStream().write(
        Jcs.canonical(Map.of("schema", "portal-engine-read-error.v1", "code", code)));
  }
}
