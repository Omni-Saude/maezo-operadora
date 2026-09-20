package br.com.maezo.workload;

import jakarta.servlet.*;
import jakarta.servlet.http.*;
import java.io.IOException;
import java.util.*;

/** Finite gate installed globally in actual Tomcat, including native SPI whitelist aliases. */
public final class BoundaryFilter implements Filter {
  private BoundaryPolicy policy;
  private BoundaryPolicyV2 v2;
  static final ThreadLocal<BoundaryPolicy.Peer> REQUEST_PEER=new ThreadLocal<>();
  @Override public void init(FilterConfig config) {
    if(!BoundaryPolicyV2.configured())SecuredBpmPlatformBootstrap.filterStart(config);
    policy=BoundaryPolicy.environment();SecureLayout.verify(policy);
    if(BoundaryPolicyV2.configured()){v2=BoundaryPolicyV2.environment();SecureLayout.verifyV2(v2);}
    else SecuredBpmPlatformBootstrap.filterCompleted(config);
  }
  @Override public void doFilter(ServletRequest req,ServletResponse res,FilterChain chain) throws IOException,ServletException {
    if(!(req instanceof HttpServletRequest request)||!(res instanceof HttpServletResponse response))throw new ServletException("engine_operation_denied");
    response.setHeader("Cache-Control","no-store");response.setHeader("X-Content-Type-Options","nosniff");
    try {
      if(request.getRequestURI()!=null && request.getRequestURI().startsWith("/maezo-workload/")) {
        if(v2==null || !BoundaryPolicyV2.route(request))throw Refused.denied();
        v2.authenticate(request);chain.doFilter(request,response);return;
      }
      BoundaryPolicy.Peer peer=policy.authenticate(request);
      route(request,peer.purpose());
      // Request attributes carry server-produced objects, never a forwarded principal/certificate header.
      request.setAttribute(BoundaryFilter.class.getName()+".peer",peer);
      REQUEST_PEER.set(peer);
      try {chain.doFilter(request,response);} finally {REQUEST_PEER.remove();}
    } catch(Refused e) {
      if(request.getRequestURI()!=null && request.getRequestURI().startsWith("/maezo-workload/")) {
        var result=NativeOutcomeV2.refusal(e.status==400?"invalid_request":e.status==403?"denied":"unavailable");
        response.setStatus(result.status());response.setContentType("application/json");response.getOutputStream().write(result.bytes());
      }else error(response,e);
    }
  }
  static void route(HttpServletRequest r,String purpose) {
    if(r.getDispatcherType()!=DispatcherType.REQUEST || r.isAsyncStarted() || r.getQueryString()!=null)throw Refused.denied();
    String uri=r.getRequestURI();
    if(uri==null || !uri.matches("/[A-Za-z0-9_./:@-]+") || uri.contains("//") || uri.contains("/.")
        || !uri.equals(r.getContextPath()+r.getServletPath()+(r.getPathInfo()==null?"":r.getPathInfo())))throw Refused.denied();
    // Native mutations, engine-name aliases, identity/verify and engine enumeration never reach RESTEasy.
    if ("/engine-rest/version".equals(uri) && "GET".equals(r.getMethod()) && !"human-relay".equals(purpose)) {
      if(r.getContentLengthLong()>0 || r.getHeader("Transfer-Encoding")!=null)throw Refused.body();return;
    }
    if ("/engine-rest/maezo/v1/operations".equals(uri) && "POST".equals(r.getMethod()) && "nonhuman".equals(purpose)) {
      if(!"application/json".equals(r.getContentType()) || r.getContentLengthLong()>Json.LIMIT)throw Refused.body();return;
    }
    if ("/engine-rest/maezo/v1/readiness".equals(uri) && "GET".equals(r.getMethod()) && "nonhuman".equals(purpose)) {
      if(r.getContentLengthLong()>0 || r.getHeader("Transfer-Encoding")!=null)throw Refused.body();return;
    }
    if("human-relay".equals(purpose) && uri.startsWith("/maezo-human/") && (
        ("POST".equals(r.getMethod()) && Set.of("/maezo-human/v1/commands","/maezo-human/v1/authority").contains(uri)) ||
        ("GET".equals(r.getMethod()) && uri.matches("/maezo-human/v1/receipts/[A-Za-z0-9_.:@-]{1,255}/[A-Za-z0-9_.:@-]{1,255}"))))return;
    throw Refused.denied();
  }
  static void error(HttpServletResponse response,Refused e)throws IOException {
    if(response.isCommitted())throw new IOException("engine_operation_denied");
    response.resetBuffer();response.setStatus(e.status);response.setContentType("application/json");
    response.getOutputStream().write(Json.bytes(Map.of("error",e.code)));
  }
}
