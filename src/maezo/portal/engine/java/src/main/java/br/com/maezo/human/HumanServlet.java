package br.com.maezo.human;

import java.io.IOException;
import java.security.cert.X509Certificate;
import java.util.*;
import jakarta.servlet.http.*;
import org.cibseven.bpm.engine.OptimisticLockingException;

/** Private workload endpoint: direct Tomcat mutual TLS, no forwarded identity headers. */
public final class HumanServlet extends HttpServlet {
  @Override
  protected void service(HttpServletRequest request, HttpServletResponse response)
      throws IOException {
    response.setHeader("Cache-Control", "no-store");
    response.setHeader("X-Content-Type-Options", "nosniff");
    response.setContentType("application/json");
    response.setCharacterEncoding("UTF-8");
    try {
      String peer = peer(request);
      String path = request.getPathInfo();
      byte[] result;
      if (request.getQueryString() != null) throw Rejected.invalid();
      var plugin = HumanCommandPlugin.running();
      if ("POST".equals(request.getMethod())
          && Set.of("/v1/commands", "/v1/authority").contains(path)) {
        if (!"application/json".equals(request.getContentType())) throw Rejected.invalid();
        byte[] raw = request.getInputStream().readNBytes(65537);
        if (raw.length > 65536) throw Rejected.invalid();
        result =
            plugin.execute(
                raw, peer, path.equals("/v1/commands") ? "human-command" : "human-authority");
      } else if ("POST".equals(request.getMethod())
          && path != null && Set.of("/v1/auth-start", "/v1/auth-documents", "/v1/auth-receipt",
              "/v1/auth-document-context", "/v1/auth-input-publication", "/v1/auth-publication-receipt").contains(path)) {
        if (!"application/json".equals(request.getContentType())) throw Rejected.invalid();
        byte[] raw = request.getInputStream().readNBytes(65537);
        if (raw.length > 65536) throw Rejected.invalid();
        result = plugin.executeAuth(path, raw, peer);
      } else if ("POST".equals(request.getMethod()) && path != null
          && Set.of("/v1/staff-case-detail","/v1/staff-case-finalize","/v1/staff-case-publication").contains(path)) {
        if(!"application/json".equals(request.getContentType()))throw Rejected.invalid();
        byte[] raw=request.getInputStream().readNBytes(65537);
        if(raw.length>65536)throw Rejected.invalid();
        var committed=plugin.executeStaff(path,raw,peer);
        writeStaff(response,committed);
        return;
      } else if("POST".equals(request.getMethod()) && Set.of("/v1/assignment-context","/v1/assignment-candidates","/v1/assignment-authority","/v1/assignment-receipt-authority").contains(path)) {
        if(!"application/json".equals(request.getContentType()))throw Rejected.invalid();
        byte[] raw=request.getInputStream().readNBytes(65537);if(raw.length>65536)throw Rejected.invalid();
        result=plugin.assignmentQuery(raw,peer,path.substring("/v1/assignment-".length()));
      } else if ("GET".equals(request.getMethod())
          && path != null
          && path.matches("/v1/receipts/[A-Za-z0-9_.:@-]{1,255}/[A-Za-z0-9_.:@-]{1,255}")) {
        String token = request.getHeader("Authorization");
        if (token == null || !token.startsWith("Maezo-Human ") || token.length() > 90000)
          throw Rejected.denied();
        String encoded = token.substring(12);
        if (!encoded.matches("[A-Za-z0-9_-]+")) throw Rejected.denied();
        byte[] raw;
        try {
          raw = Base64.getUrlDecoder().decode(encoded);
        } catch (IllegalArgumentException ex) {
          throw Rejected.denied();
        }
        if (!Base64.getUrlEncoder().withoutPadding().encodeToString(raw).equals(encoded))
          throw Rejected.denied();
        String[] parts = path.split("/");
        result = plugin.receipt(raw, peer, parts[3], parts[4]);
      } else throw new Rejected(404, "NOT_FOUND");
      response.setStatus(200);
      response.getOutputStream().write(result);
    } catch (Rejected rejected) {
      error(response, rejected.status, rejected.code);
    } catch (OptimisticLockingException race) {
      error(response, 409, "REVISION_CONFLICT");
    } catch (RuntimeException unavailable) {
      error(response, 503, "HUMAN_ENGINE_UNAVAILABLE");
    }
  }

  /** Actual staff output handoff; caller retains the existing closed error handler. */
  static void writeStaff(HttpServletResponse response,HumanCommandPlugin.StaffResponse committed)
      throws IOException {
    response.setStatus(200);
    var output=response.getOutputStream();
    byte[] body=committed.bytes();
    // Stream setup may block. Check the same original guard immediately before write.
    committed.requireCurrent();
    output.write(body);
  }

  static String peer(HttpServletRequest request) {
    Object certificates = request.getAttribute("jakarta.servlet.request.X509Certificate");
    if (!request.isSecure()
        || !(certificates instanceof X509Certificate[] chain)
        || chain.length == 0) throw Rejected.denied();
    try {
      chain[0].checkValidity();
    } catch (java.security.cert.CertificateException ex) {
      throw Rejected.denied();
    }
    return Jcs.digest(chain[0].getPublicKey().getEncoded());
  }

  private static void error(HttpServletResponse response, int status, String code)
      throws IOException {
    response.setStatus(status);
    response.getOutputStream().write(Jcs.canonical(Map.of("error", code)));
  }
}
