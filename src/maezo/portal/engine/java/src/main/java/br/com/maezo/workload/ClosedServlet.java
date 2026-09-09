package br.com.maezo.workload;

import jakarta.servlet.http.*;
import java.io.IOException;

/** Opt-in package only: historical webapp APIs remain closed until reviewed operational migration. */
public final class ClosedServlet extends HttpServlet {
  @Override protected void service(HttpServletRequest request,HttpServletResponse response)throws IOException {
    BoundaryFilter.error(response,Refused.denied());
  }
}
