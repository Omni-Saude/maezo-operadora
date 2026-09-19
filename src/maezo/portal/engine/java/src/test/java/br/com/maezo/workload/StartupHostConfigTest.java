package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.*;
import java.util.function.Consumer;
import org.apache.catalina.*;
import org.apache.catalina.connector.Connector;
import org.apache.catalina.core.*;
import org.apache.catalina.startup.HostConfig;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/** Actual HostConfig/ContextConfig deployment and server TLL callbacks; no engine fixture claim. */
class StartupHostConfigTest {
  @TempDir Path root;
  private static final String WEB="<web-app xmlns=\"https://jakarta.ee/xml/ns/jakartaee\" version=\"6.0\" metadata-complete=\"true\"><absolute-ordering/></web-app>";

  private final class Installation implements AutoCloseable {
    final StandardServer server=new StandardServer();
    final StandardService service=new StandardService();
    final StandardEngine engine=new StandardEngine();
    final StandardHost host=new StandardHost();
    final ThreadLocalLeakPreventionListener tll=new ThreadLocalLeakPreventionListener();
    final Connector connector=new Connector();
    final AtomicReference<StartupHostCache> cache=new AtomicReference<>();
    final AtomicReference<StandardContext> context=new AtomicReference<>();
    final AtomicBoolean before=new AtomicBoolean(),configured=new AtomicBoolean(),connected=new AtomicBoolean();
    final AtomicBoolean oldGuardWouldRefuse=new AtomicBoolean(),guarded=new AtomicBoolean();
    final AtomicInteger listenersAtGate=new AtomicInteger(),configurationCount=new AtomicInteger();
    Runnable restoreHost=()->{};

    Installation(Consumer<StandardContext> mutate,boolean oldGuard) throws Exception {this(mutate,oldGuard,app->{});}

    Installation(Consumer<StandardContext> mutate,boolean oldGuard,Consumer<Installation> mutateHost) throws Exception {
      Files.createDirectories(root.resolve("conf"));Files.createDirectories(root.resolve("webapps/app/WEB-INF"));
      Files.writeString(root.resolve("conf/web.xml"),WEB);
      Files.writeString(root.resolve("conf/context.xml"),"<Context containerSciFilter=\".*\"/>");
      Files.writeString(root.resolve("webapps/app/WEB-INF/web.xml"),WEB);
      server.setPort(-1);server.setCatalinaBase(root.toFile());server.setCatalinaHome(root.toFile());
      server.setThrowOnFailure(true);service.setThrowOnFailure(true);engine.setThrowOnFailure(true);
      service.setName("real-host-config");server.addService(service);
      engine.setName("real-host-config");engine.setDefaultHost("localhost");service.setContainer(engine);
      host.setName("localhost");host.setAppBase("webapps");host.setAutoDeploy(false);host.setStartStopThreads(1);
      host.addLifecycleListener(new HostConfig());engine.addChild(host);
      server.addLifecycleListener(event->{if(Lifecycle.BEFORE_START_EVENT.equals(event.getType())) {
        cache.set(new StartupHostCache(host));
        host.addContainerListener(child->{if(Container.ADD_CHILD_EVENT.equals(child.getType())) {
          var app=(StandardContext)child.getData();context.set(app);
          StartupTopology.listeners(app,Set.of("org.apache.catalina.startup.ContextConfig",
              "org.apache.catalina.core.StandardHost$MemoryLeakTrackingListener"),null);
          int captured=app.findLifecycleListeners().length;
          if(oldGuard) app.addLifecycleListener(e->{if(Lifecycle.BEFORE_START_EVENT.equals(e.getType())) {
            oldGuardWouldRefuse.set(app.findLifecycleListeners().length!=captured+1);
            StartupAdmission.require(app.findLifecycleListeners().length==captured+1);
          }});
          else new StartupContextListeners(app,tll,()-> {
            before.set(true);cache.get().beforeContext(app);
          },()-> {cache.get().configuredContext(app);configured.set(true);configurationCount.incrementAndGet();});
        }});
      }});
      server.addLifecycleListener(tll);
      server.addLifecycleListener(event->{if(Lifecycle.BEFORE_START_EVENT.equals(event.getType()))
        host.addContainerListener(child->{if(Container.ADD_CHILD_EVENT.equals(child.getType()))mutate.accept((StandardContext)child.getData());});});
      engine.addLifecycleListener(event->{if(Lifecycle.AFTER_START_EVENT.equals(event.getType())) {
        StartupAdmission.require(context.get()!=null && context.get().getState()==LifecycleState.STARTED);
        cache.get().current();listenersAtGate.set(host.findLifecycleListeners().length);
        mutateHost.accept(this);cache.get().current();guarded.set(true);
      }});
      connector.setPort(0);connector.setProperty("bindOnInit","false");service.addConnector(connector);
      connector.addLifecycleListener(event->{if(Lifecycle.START_EVENT.equals(event.getType()))connected.set(true);});
    }
    @Override public void close() throws Exception {restoreHost.run();try {server.stop();} finally {server.destroy();}}
  }

  @Test void actualHostConfigAddsExactServerTllThenConfiguresCacheAndStartsConnector() throws Exception {
    try(var app=new Installation(context->{},false)) {
      app.server.start();assertTrue(app.before.get());assertTrue(app.configured.get());assertTrue(app.connected.get());
      assertEquals(LifecycleState.STARTED,app.context.get().getState());
      assertEquals(1,Arrays.stream(app.context.get().findLifecycleListeners()).filter(x->x==app.tll).count());
      assertTrue(app.guarded.get());assertEquals(2,app.listenersAtGate.get());
      assertEquals(List.of("org.apache.catalina.startup.HostConfig",
          "org.apache.catalina.startup.ContextConfig$HostWebXmlCacheCleaner",
          "org.apache.catalina.mapper.MapperListener"),
          Arrays.stream(app.host.findLifecycleListeners()).map(x->x.getClass().getName()).toList());
    }
  }

  @Test void actualVendorSequenceReproducesOldBeforeStartCountFailure() throws Exception {
    try(var app=new Installation(context->{},true)) {
      assertThrows(LifecycleException.class,app.server::start);
      assertTrue(app.oldGuardWouldRefuse.get());assertFalse(app.connected.get());
    }
  }

  @ParameterizedTest @ValueSource(strings={"unknown","duplicate","replacement","missing","reordered","prefix-replaced"})
  void modifiedActualListenerSequenceRefusesBeforeConnector(String change) throws Exception {
    try(var app=new Installation(context->{
      var listeners=new ArrayList<>(Arrays.asList(context.findLifecycleListeners()));
      var actualTll=listeners.get(listeners.size()-1);
      switch(change) {
        case "unknown" -> listeners.add(event->{});
        case "duplicate" -> listeners.add(actualTll);
        case "replacement" -> listeners.set(listeners.size()-1,new ThreadLocalLeakPreventionListener());
        case "missing" -> listeners.remove(listeners.size()-1);
        case "reordered" -> Collections.swap(listeners,listeners.size()-1,listeners.size()-2);
        case "prefix-replaced" -> listeners.set(0,new org.apache.catalina.startup.ContextConfig());
        default -> throw new AssertionError(change);
      }
      for(var listener:context.findLifecycleListeners())context.removeLifecycleListener(listener);
      for(var listener:listeners)context.addLifecycleListener(listener);
    },false)) {
      assertThrows(LifecycleException.class,app.server::start);
      assertFalse(app.connected.get());assertFalse(app.configured.get());
    }
  }

  @ParameterizedTest @ValueSource(strings={"extra","duplicate","missing","reordered","cleaner-replaced","cache-removed","cache-replaced"})
  void capturedHostCleanerAndCacheCannotDrift(String change) throws Exception {
    try(var app=new Installation(context->{},false,owned->{
      try {
        var listeners=owned.host.findLifecycleListeners();
        var field=org.apache.catalina.startup.ContextConfig.class.getDeclaredField("hostWebXmlCache");field.setAccessible(true);
        @SuppressWarnings("unchecked") var cache=(Map<Object,Object>)field.get(null);
        var original=cache.get(owned.host);
        owned.restoreHost=()-> {
          for(var listener:owned.host.findLifecycleListeners())owned.host.removeLifecycleListener(listener);
          for(var listener:listeners)owned.host.addLifecycleListener(listener);
          cache.put(owned.host,original);
        };
        switch(change) {
          case "cache-removed" -> cache.remove(owned.host);
          case "cache-replaced" -> cache.put(owned.host,new Object());
          case "extra" -> owned.host.addLifecycleListener(event->{});
          case "duplicate" -> owned.host.addLifecycleListener(listeners[1]);
          case "missing" -> owned.host.removeLifecycleListener(listeners[1]);
          case "cleaner-replaced" -> {
            var constructor=listeners[1].getClass().getDeclaredConstructor();constructor.setAccessible(true);
            owned.host.removeLifecycleListener(listeners[1]);owned.host.addLifecycleListener((LifecycleListener)constructor.newInstance());
          }
          case "reordered" -> {for(var listener:listeners)owned.host.removeLifecycleListener(listener);owned.host.addLifecycleListener(listeners[1]);owned.host.addLifecycleListener(listeners[0]);}
          default -> throw new AssertionError(change);
        }
      } catch(ReflectiveOperationException failure) {throw new AssertionError(failure);}
    })) {
      assertThrows(LifecycleException.class,app.server::start);
      assertTrue(app.configured.get());assertEquals(2,app.listenersAtGate.get(),"unchanged positive cache guard must pass first");
      assertFalse(app.connected.get());assertFalse(app.guarded.get());
    }
  }

  @Test void serialSecondApplicationUsesSameRealCacheAndCleaner() throws Exception {
    try(var app=new Installation(context->{},false)) {
      Files.createDirectories(root.resolve("webapps/second/WEB-INF"));Files.writeString(root.resolve("webapps/second/WEB-INF/web.xml"),WEB);
      app.server.start();assertEquals(2,app.configurationCount.get());assertTrue(app.guarded.get());assertTrue(app.connected.get());
      assertEquals(2,app.listenersAtGate.get());
      for(var context:app.host.findChildren())assertEquals(LifecycleState.STARTED,context.getState());
    }
  }

  @Test void unsupportedParallelDeploymentRefusesBeforeVendorConfiguration() {
    var host=new StandardHost();host.addLifecycleListener(new HostConfig());host.setStartStopThreads(2);
    assertThrows(Refused.class,()->new StartupHostCache(host));
  }
}
