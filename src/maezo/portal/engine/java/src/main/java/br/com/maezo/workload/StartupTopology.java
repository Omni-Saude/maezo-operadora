package br.com.maezo.workload;

import java.util.*;
import org.apache.catalina.*;
import org.apache.catalina.core.*;
import org.apache.catalina.connector.Connector;
import org.apache.catalina.util.LifecycleBase;

/** Actual objects, not only a plausible unused XML descriptor. */
final class StartupTopology {
  final StandardServer server;
  final StandardService service;
  final StandardEngine engine;
  final StandardHost host;
  final ThreadLocalLeakPreventionListener threadLocal;
  final StartupHostCache hostCache;
  private final Connector connector;
  private final StartupCustody custody;
  private final LifecycleListener owner;
  private LifecycleListener engineObserver;
  StartupTopology(StandardServer server,StartupCustody custody,LifecycleListener owner) {
    this.server=server;this.custody=custody;this.owner=owner;
    require(server.getClass()==StandardServer.class && server.findServices().length==1);
    require(server.findServices()[0].getClass()==StandardService.class);
    service=(StandardService)server.findServices()[0];
    require(service.getContainer()!=null && service.getContainer().getClass()==StandardEngine.class);
    engine=(StandardEngine)service.getContainer();
    require(engine.findChildren().length==1 && engine.findChildren()[0].getClass()==StandardHost.class);
    host=(StandardHost)engine.findChildren()[0];
    require(host.findChildren().length==0);
    var serverListeners=Arrays.asList(server.findLifecycleListeners());
    var leakListeners=serverListeners.stream().filter(x->x.getClass()==ThreadLocalLeakPreventionListener.class).toList();
    require(leakListeners.size()==1 && serverListeners.indexOf(owner)>=0
        && serverListeners.indexOf(owner)<serverListeners.indexOf(leakListeners.get(0)));
    threadLocal=(ThreadLocalLeakPreventionListener)leakListeners.get(0);
    hostCache=new StartupHostCache(host);
    require(service.findConnectors().length==1);connector=service.findConnectors()[0];
    current();
  }
  void observe(LifecycleListener listener) {
    require(engineObserver==null);engineObserver=listener;engine.addLifecycleListener(listener);
  }
  void current() {
    require(server.getCatalinaBase().toPath().equals(custody.root)
        && server.findServices().length==1 && server.findServices()[0]==service
        && service.getContainer()==engine && engine.getService()==service
        && engine.findChildren().length==1 && engine.findChildren()[0]==host
        && host.getParent()==engine && host.getAppBaseFile().toPath().equals(custody.root.resolve("webapps"))
        && service.findConnectors().length==1 && service.findConnectors()[0]==connector
        && connector.getService()==service);
    for(var value:List.of(server,service,engine))require(((LifecycleBase)value).getThrowOnFailure());
    require(!host.getAutoDeploy() && host.getDeployOnStartup() && host.findAliases().length==0
        && "org.apache.catalina.startup.ContextConfig".equals(host.getConfigClass())
        && "org.apache.catalina.core.StandardContext".equals(host.getContextClass())
        && host.getXmlBase()==null && !host.isCopyXML() && host.getDeployIgnore()==null
        && "org.apache.catalina.valves.ErrorReportValve".equals(host.getErrorReportValveClass()));
    require(service.findExecutors().length==0 && connector.getPort()==custody.port
        && connector.getSecure() && "https".equals(connector.getScheme())
        && !connector.getAllowTrace() && connector.getProxyPort()==0
        && (connector.getProxyName()==null || connector.getProxyName().isEmpty())
        && Boolean.parseBoolean(String.valueOf(connector.getProperty("SSLEnabled"))));
    var ssl=connector.findSslHostConfigs();require(ssl.length==1
        && "REQUIRED".equals(ssl[0].getCertificateVerification().name()));
    require(connector.getClass()==Connector.class
        && "org.apache.coyote.http11.Http11NioProtocol".equals(connector.getProtocolHandlerClassName()));
    listeners(server,Set.of("org.apache.catalina.core.NamingContextListener",
        "org.apache.catalina.startup.VersionLoggerListener","org.apache.catalina.core.AprLifecycleListener",
        "org.apache.catalina.core.JreMemoryLeakPreventionListener",
        "br.com.maezo.workload.SecuredBpmPlatformBootstrap",
        "br.com.maezo.workload.SecuredGlobalResourcesLifecycleListener",
        "org.apache.catalina.core.ThreadLocalLeakPreventionListener"),null);
    require(Arrays.stream(server.findLifecycleListeners()).filter(x->x==owner).count()==1);
    listeners(service,Set.of(),null);
    listeners(engine,Set.of("org.apache.catalina.startup.EngineConfig"),engineObserver);
    hostCache.current();
    var valves=engine.getPipeline().getValves();
    require(valves.length==2 && valves[0].getClass()==TraceRefusalValve.class
        && "org.apache.catalina.core.StandardEngineValve".equals(valves[1].getClass().getName())
        && valves[1].getClass().getClassLoader()==StandardEngine.class.getClassLoader());
  }
  static void listeners(Lifecycle lifecycle,Set<String> expected,LifecycleListener extra) {
    var actual=new HashSet<String>();boolean found=false;
    for(var listener:lifecycle.findLifecycleListeners()) {
      if(listener==extra){require(!found);found=true;continue;}
      require(actual.add(listener.getClass().getName()));
      require(listener.getClass().getClassLoader()==StandardServer.class.getClassLoader()
          || listener.getClass()==SecuredBpmPlatformBootstrap.class
          || listener.getClass()==SecuredGlobalResourcesLifecycleListener.class);
    }
    require(actual.equals(expected) && (extra==null || found));
  }
  static void require(boolean value) {StartupAdmission.require(value);}
}
