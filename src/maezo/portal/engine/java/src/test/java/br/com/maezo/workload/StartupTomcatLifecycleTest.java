package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import jakarta.servlet.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicBoolean;
import org.apache.catalina.*;
import org.apache.catalina.connector.Connector;
import org.apache.catalina.core.*;
import org.apache.tomcat.util.descriptor.web.FilterDef;
import org.apache.tomcat.util.descriptor.web.FilterMap;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** Real Tomcat lifecycle mechanics; no engine is constructed and no connector is started. */
class StartupTomcatLifecycleTest {
  @TempDir Path root;
  @Test void actualFailedFilterIsCleanedBeforeEngineGuardAndThrownGuardPreventsConnectorStart() throws Exception {
    var server=new StandardServer();server.setPort(-1);server.setCatalinaBase(root.toFile());server.setCatalinaHome(root.toFile());
    var service=new StandardService();service.setName("startup-test");server.addService(service);
    var engine=new StandardEngine();engine.setName("startup-test");engine.setDefaultHost("localhost");service.setContainer(engine);
    var host=new StandardHost();host.setName("localhost");host.setAppBase(root.toString());host.setAutoDeploy(false);engine.addChild(host);
    var context=new StandardContext();context.setName("/failed");context.setPath("/failed");context.setDocBase(root.toString());
    context.setIgnoreAnnotations(true);
    context.addLifecycleListener(event->{if(Lifecycle.CONFIGURE_START_EVENT.equals(event.getType()))context.setConfigured(true);});
    host.addChild(context);
    var state=new StartupLifecycle(host,Set.of("/failed"));state.enroll(context);state.refusePolicy();
    var filter=new FilterDef();filter.setFilterName("maezo-boundary");
    filter.setFilter(new Filter() {
      @Override public void init(FilterConfig config){state.requireFilterStart(config);}
      @Override public void doFilter(ServletRequest req,ServletResponse res,FilterChain chain) {fail("failed context cannot dispatch");}
    });
    filter.setFilterClass(filter.getFilter().getClass().getName());context.addFilterDef(filter);
    var mapping=new FilterMap();mapping.setFilterName("maezo-boundary");mapping.addURLPattern("/*");context.addFilterMap(mapping);
    var connector=new Connector();connector.setPort(0);connector.setProperty("bindOnInit","false");service.addConnector(connector);
    var connectorStarted=new AtomicBoolean();connector.addLifecycleListener(event->{if(Lifecycle.START_EVENT.equals(event.getType()))connectorStarted.set(true);});
    var guarded=new AtomicBoolean();engine.addLifecycleListener(event->{
      if(Lifecycle.AFTER_START_EVENT.equals(event.getType())) {
        state.verifyRefusedContexts();guarded.set(true);throw new IllegalStateException("residual-resource-control");
      }
    });
    try {
      assertThrows(LifecycleException.class,server::start);
      assertTrue(guarded.get(),"actual filter failure and cleanup must precede Engine AFTER_START");
      assertFalse(connectorStarted.get());assertFalse(connector.getState().isAvailable());
      assertEquals(LifecycleState.STOPPED,context.getState());
    } finally {
      state.stopped();
      try {server.stop();} finally {server.destroy();}
    }
  }
}
