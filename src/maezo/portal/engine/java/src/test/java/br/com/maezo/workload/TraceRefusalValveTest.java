package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicInteger;
import org.apache.catalina.connector.Connector;
import org.apache.catalina.connector.Request;
import org.apache.catalina.connector.Response;
import org.apache.catalina.core.StandardContext;
import org.apache.catalina.core.StandardEngine;
import org.apache.catalina.core.StandardHost;
import org.apache.catalina.valves.ValveBase;
import org.apache.coyote.OutputBuffer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class TraceRefusalValveTest {
  private static final class Packet {
    final Request request = new Request(new Connector());
    final Response response = new Response();
    final org.apache.coyote.Request rawRequest = new org.apache.coyote.Request();
    final org.apache.coyote.Response rawResponse = new org.apache.coyote.Response();
    final ByteArrayOutputStream bytes = new ByteArrayOutputStream();
    final AtomicInteger next = new AtomicInteger();
    final TraceRefusalValve valve = new TraceRefusalValve();
    Packet(String method) {
      rawResponse.setRequest(rawRequest);rawRequest.setResponse(rawResponse);
      request.setCoyoteRequest(rawRequest); response.setCoyoteResponse(rawResponse);
      request.setResponse(response); response.setRequest(request);
      request.getMappingData().host = new StandardHost();
      request.getMappingData().context = new StandardContext();
      rawRequest.setNote(org.apache.catalina.connector.CoyoteAdapter.ADAPTER_NOTES,request);
      rawRequest.method().setString(method);
      rawRequest.requestURI().setString("/engine-rest/maezo/v1/operations;alias?secret=canary");
      rawRequest.getMimeHeaders().addValue("Authorization").setString("canary-secret");
      rawResponse.setOutputBuffer(new OutputBuffer() {
        @Override public int doWrite(ByteBuffer input) {
          int length=input.remaining();byte[] copy=new byte[length];input.get(copy);bytes.writeBytes(copy);return length;
        }
        @Override public long getBytesWritten() { return bytes.size(); }
      });
      valve.setNext(new ValveBase(true) {
        @Override public void invoke(Request r,Response s) { Packet.this.next.incrementAndGet(); }
      });
    }
  }

  @ParameterizedTest @ValueSource(booleans={false,true})
  void traceNeverReachesServletAndHasExactFiniteResponse(boolean coyoteRefused)throws Exception {
    var p=new Packet("TRACE");
    assertNotNull(p.request.getHost());assertNotNull(p.request.getContext());
    if(coyoteRefused) {
      p.response.setHeader("Allow","GET, POST");
      p.response.sendError(405,"vendor trace detail");
      assertTrue(p.response.isSuspended());assertTrue(p.response.isError());
    }
    p.valve.invoke(p.request,p.response);p.response.finishResponse();
    assertEquals(0,p.next.get());assertEquals(403,p.response.getStatus());
    assertEquals("{\"error\":\"engine_operation_denied\"}",p.bytes.toString(StandardCharsets.UTF_8));
    assertEquals("no-store",p.response.getHeader("Cache-Control"));
    assertEquals("nosniff",p.response.getHeader("X-Content-Type-Options"));
    assertEquals("application/json",p.response.getContentType());
    assertNull(p.response.getHeader("Allow"));assertFalse(p.response.isError());
  }

  @ParameterizedTest @ValueSource(booleans={false,true})
  void unmappedTraceReachesTomcatMissingHostOrContextHandling(boolean mappedHost)throws Exception {
    var p=new Packet("TRACE");
    if(!mappedHost) p.request.getMappingData().host=null;
    p.request.getMappingData().context=null;
    assertNull(p.request.getContext());
    assertEquals(200,p.response.getStatus());assertFalse(p.response.isError());
    // Actual Tomcat Engine/Host basic valves assign 404 only after our Engine valve.
    p.valve.setNext(new StandardEngine().getPipeline().getBasic());
    p.valve.invoke(p.request,p.response);
    assertEquals(404,p.response.getStatus());assertTrue(p.response.isError());
    assertTrue(p.response.isSuspended());assertEquals(0,p.bytes.size());
    assertNull(p.response.getHeader("Cache-Control"));
  }

  @Test void committedTraceCannotFallThroughOrChangeResponse() {
    var p=new Packet("TRACE");p.rawResponse.setCommitted(true);
    assertThrows(java.io.IOException.class,()->p.valve.invoke(p.request,p.response));
    assertEquals(0,p.next.get());assertEquals(0,p.bytes.size());
  }

  @ParameterizedTest @ValueSource(ints={400,404,503})
  void existingParserOrAbsentApplicationErrorsKeepTheirOriginalHandling(int status)throws Exception {
    var p=new Packet("TRACE");p.response.sendError(status,"original");
    p.valve.invoke(p.request,p.response);
    assertEquals(1,p.next.get());assertEquals(status,p.response.getStatus());
    assertTrue(p.response.isError());assertTrue(p.response.isSuspended());assertEquals(0,p.bytes.size());
  }

  @ParameterizedTest @ValueSource(strings={"GET","HEAD","POST","PUT","PATCH","DELETE","OPTIONS","CONNECT"})
  void unrelatedMethodsKeepOriginalPipeline(String method)throws Exception {
    var p=new Packet(method);p.response.setStatus(405);p.valve.invoke(p.request,p.response);
    assertEquals(1,p.next.get());assertEquals(405,p.response.getStatus());assertEquals(0,p.bytes.size());
  }
}
