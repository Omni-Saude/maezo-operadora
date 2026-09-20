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
import org.cibseven.bpm.engine.impl.cfg.StandaloneProcessEngineConfiguration;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/** A real started Context alone is insufficient: late failures must stop connector startup. */
class StartupPositiveGuardTest {
  @TempDir Path root;
  @ParameterizedTest @ValueSource(strings={"filter-failure","filter-incomplete","eager-failure","unenrolled-context"})
  void actualLateStartupFailureIsFatalBeforeConnectorStart(String fault)throws Exception {
    verifyActualContextCompletion(fault);
  }

  @Test void completeActualContextAllowsConnectorStart()throws Exception {
    verifyActualContextCompletion("none");
  }

  private void verifyActualContextCompletion(String fault)throws Exception {
    var server=new StandardServer();server.setPort(-1);server.setCatalinaBase(root.toFile());server.setCatalinaHome(root.toFile());
    var service=new StandardService();service.setName("positive-unit");server.addService(service);
    var engine=new StandardEngine();engine.setName("positive-unit");engine.setDefaultHost("localhost");service.setContainer(engine);
    var host=new StandardHost();host.setName("localhost");host.setAppBase(root.toString());host.setAutoDeploy(false);engine.addChild(host);
    var context=new StandardContext();context.setName("/one");context.setPath("/one");context.setDocBase(root.toString());context.setIgnoreAnnotations(true);
    context.addLifecycleListener(event->{if(Lifecycle.CONFIGURE_START_EVENT.equals(event.getType()))context.setConfigured(true);});host.addChild(context);
    var state=new StartupLifecycle(host,Set.of("/one"));state.enroll(context);state.acceptPolicy();state.vendorStarting();
    state.claimConfiguration(new StandaloneProcessEngineConfiguration());state.vendorStarted();
    var filter=new FilterDef();filter.setFilterName("maezo-boundary");filter.setFilter(new Filter(){
      public void init(FilterConfig config) {
        state.requireFilterStart(config);
        if(fault.equals("filter-failure"))throw Refused.unavailable();
        if(!fault.equals("filter-incomplete"))state.filterCompleted(config);
      }
      public void doFilter(ServletRequest request,ServletResponse response,FilterChain chain){fail("no connector acceptance");}
    });filter.setFilterClass(filter.getFilter().getClass().getName());context.addFilterDef(filter);
    var mapping=new FilterMap();mapping.setFilterName("maezo-boundary");mapping.addURLPattern("/*");context.addFilterMap(mapping);
    var wrapper=new StandardWrapper();wrapper.setName("eager");wrapper.setLoadOnStartup(0);
    wrapper.setServlet(new GenericServlet(){
      public void init()throws ServletException {
        if(fault.equals("eager-failure"))throw new ServletException("unit-late-failure");
      }
      public void service(ServletRequest req,ServletResponse res){fail("no requests in this fixture");}
    });
    context.addChild(wrapper);context.addServletMappingDecoded("/eager","eager");
    var connector=new Connector();connector.setPort(0);connector.setProperty("bindOnInit","false");service.addConnector(connector);
    var accepted=new AtomicBoolean();connector.addLifecycleListener(e->{if(Lifecycle.START_EVENT.equals(e.getType()))accepted.set(true);});
    var attempted=new AtomicBoolean();var completed=new AtomicBoolean();engine.addLifecycleListener(e->{if(Lifecycle.AFTER_START_EVENT.equals(e.getType())) {
      if(fault.equals("unenrolled-context")) {
        // A foreign actual Host child is never represented by the enrolled identity set.
        var extra=new StandardContext();extra.setName("/extra");extra.setPath("/extra");extra.setDocBase(root.toString());
        extra.setIgnoreAnnotations(true);extra.addLifecycleListener(x->{if(Lifecycle.CONFIGURE_START_EVENT.equals(x.getType()))extra.setConfigured(true);});host.addChild(extra);
      }
      if(fault.equals("eager-failure"))assertEquals(LifecycleState.STARTED,context.getState(),"Tomcat may log eager failure and still start the Context");
      attempted.set(true);
      state.verifyContextCompletion();
      completed.set(true);
    }});
    try {
      if(fault.equals("none")) {
        assertDoesNotThrow(server::start);
        assertEquals(LifecycleState.STARTED,context.getState());
        assertTrue(completed.get(),"the real context guards must return successfully");
        assertTrue(accepted.get(),"successful completion permits connector startup");
      } else {
        assertThrows(LifecycleException.class,server::start);
        assertFalse(completed.get(),"only the context helper must reject this fixture");
        assertFalse(accepted.get());
      }
      assertTrue(attempted.get());
    }
    finally {try{server.stop();}finally{server.destroy();}}
  }
}
