package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;

import java.io.*;
import java.lang.reflect.Proxy;
import java.util.*;
import javax.servlet.*;
import javax.servlet.http.*;
import org.junit.jupiter.api.Test;

/** Unit-level HTTP fence only; actual Tomcat TLS/load proof is a root-owned lane. */
class ServletBoundaryTest {
  @Test
  void plaintextAndSpoofedForwardedCertificatesNeverReachEngine() throws Exception {
    for (boolean secure : List.of(false, true)) {
      HttpServletRequest request =
          (HttpServletRequest)
              Proxy.newProxyInstance(
                  getClass().getClassLoader(),
                  new Class[] {HttpServletRequest.class},
                  (obj, method, args) ->
                      switch (method.getName()) {
                        case "isSecure" -> secure;
                        case "getHeader" -> "attacker-forwarded-certificate-and-private-narrative";
                        case "getAttribute" -> null;
                        default -> null;
                      });
      var output = new ByteArrayOutputStream();
      var status = new int[1];
      HttpServletResponse response =
          (HttpServletResponse)
              Proxy.newProxyInstance(
                  getClass().getClassLoader(),
                  new Class[] {HttpServletResponse.class},
                  (obj, method, args) -> {
                    if (method.getName().equals("setStatus")) status[0] = (Integer) args[0];
                    if (method.getName().equals("getOutputStream"))
                      return new ServletOutputStream() {
                        public void write(int b) {
                          output.write(b);
                        }

                        public boolean isReady() {
                          return true;
                        }

                        public void setWriteListener(WriteListener listener) {}
                      };
                    return null;
                  });
      new HumanServlet().service(request, response);
      assertEquals(403, status[0]);
      assertEquals(
          "{\"error\":\"AUTHORITY_DENIED\"}",
          output.toString(java.nio.charset.StandardCharsets.UTF_8));
    }
  }
}
