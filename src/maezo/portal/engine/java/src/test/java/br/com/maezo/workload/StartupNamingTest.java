package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.file.*;
import java.lang.reflect.*;
import java.sql.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
import javax.naming.*;
import org.apache.catalina.*;
import org.apache.catalina.core.*;
import org.apache.naming.NamingContext;
import org.apache.naming.NamingEntry;
import org.apache.tomcat.util.IntrospectionUtils;
import org.apache.tomcat.util.descriptor.web.ContextResource;
import org.cibseven.bpm.engine.impl.cfg.StandaloneProcessEngineConfiguration;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/** Actual Tomcat naming and original JDBC factory. Unit-only JDBC driver, no mocked engine. */
class StartupNamingTest {
  @TempDir Path root;
  private static final AtomicInteger connections=new AtomicInteger(), closes=new AtomicInteger();
  private static final String PROPERTY_SOURCE="org.apache.tomcat.util.digester.PROPERTY_SOURCE";
  private static boolean failDriver;
  public static final class Driver implements java.sql.Driver {
    public Connection connect(String url,Properties props)throws SQLException {
      if(!acceptsURL(url))return null;if(failDriver)throw new SQLException("unit-driver-failure");connections.incrementAndGet();
      return (Connection)Proxy.newProxyInstance(getClass().getClassLoader(),new Class<?>[]{Connection.class},(proxy,method,args)->{
        if(method.getName().equals("close")){closes.incrementAndGet();return null;}
        if(method.getName().equals("toString"))return "unit-jdbc-connection";
        if(method.getName().equals("getTransactionIsolation"))return Connection.TRANSACTION_READ_COMMITTED;
        if(method.getReturnType()==boolean.class)return method.getName().equals("isValid") || method.getName().equals("getAutoCommit");
        if(method.getReturnType()==int.class)return 0;
        return null;
      });
    }
    public boolean acceptsURL(String url){return "jdbc:startup-unit".equals(url);}
    public DriverPropertyInfo[] getPropertyInfo(String url,Properties props){return new DriverPropertyInfo[0];}
    public int getMajorVersion(){return 1;}public int getMinorVersion(){return 0;}public boolean jdbcCompliant(){return false;}
    public java.util.logging.Logger getParentLogger(){return java.util.logging.Logger.getGlobal();}
  }
  private record Run(StandardServer server,StartupLifecycle state,StartupNaming[] naming,List<String> events) implements AutoCloseable {
    public void close()throws Exception {
      try {server.stop();}finally {server.destroy();field(SecuredBpmPlatformBootstrap.class,null,"active",null);}
    }
  }
  private Run prepare(boolean refused,String attack)throws Exception {return prepare(refused,attack,null,null);}
  private Run prepare(boolean refused,String attack,String username)throws Exception {return prepare(refused,attack,username,new org.apache.tomcat.util.digester.EnvironmentPropertySource());}
  private Run prepare(boolean refused,String attack,String username,IntrospectionUtils.PropertySource parseSource)throws Exception {
    connections.set(0);closes.set(0);failDriver="delegate".equals(attack);
    System.setProperty("catalina.useNaming","true");
    var layout=new LayoutTest();layout.temp=root;var files=layout.files();
    String catalog="<GlobalNamingResources>"
        +"<Resource name=\"UserDatabase\" auth=\"Container\" type=\"org.apache.catalina.UserDatabase\" factory=\"org.apache.catalina.users.MemoryUserDatabaseFactory\" pathname=\"conf/tomcat-users.xml\"/>"
        +"<Resource name=\"jdbc/ProcessEngine\" auth=\"Container\" type=\"javax.sql.DataSource\" factory=\""+StartupNaming.FACTORY+"\" uniqueResourceName=\"process-engine\" driverClassName=\""+Driver.class.getName()+"\" url=\"jdbc:startup-unit\" defaultTransactionIsolation=\"READ_COMMITTED\" username=\""+(username==null?"unit":username)+"\" password=\"unit\" maxActive=\"20\" minIdle=\"5\" maxIdle=\"20\"/>";
    for(String type:List.of("ProcessEngineService","ProcessApplicationService"))catalog+="<Resource name=\"global/camunda-bpm-platform/process-engine/"+type+"!org.cibseven.bpm."+type+"\" auth=\"Container\" type=\"org.cibseven.bpm."+type+"\" factory=\"org.cibseven.bpm.container.impl.jndi."+type+"ObjectFactory\"/>";
    catalog+="</GlobalNamingResources>";
    files.compute("conf/server.xml",(key,value)->value.replace("<Server>","<Server>PLACEHOLDER"));
    files.put("conf/server.xml",files.get("conf/server.xml").replace("PLACEHOLDER",catalog));
    var policy=layout.policy(files);var custody=StartupCustody.load(root.toRealPath(),policy.path,policy.digest);
    Files.writeString(root.resolve("conf/tomcat-users.xml"),"<tomcat-users/>");
    var server=new StandardServer();server.setPort(-1);server.setCatalinaBase(root.toFile());server.setCatalinaHome(root.toFile());
    var resources=SecureLayout.parse(root.resolve("conf/server.xml")).getElementsByTagName("Resource");
    // The real digester leaves every attribute ${}-resolved; reproduce it with the real source class.
    IntrospectionUtils.PropertySource[] parse=parseSource==null?null:new IntrospectionUtils.PropertySource[]{parseSource};
    for(int i=0;i<resources.getLength();i++) {
      var resource=new ContextResource();var attrs=resources.item(i).getAttributes();
      for(int j=0;j<attrs.getLength();j++){var a=attrs.item(j);assertTrue(IntrospectionUtils.setProperty(resource,a.getNodeName(),
          parse==null?a.getNodeValue():IntrospectionUtils.replaceProperties(a.getNodeValue(),null,parse,StandardServer.class.getClassLoader())));}
      server.getGlobalNamingResources().addResource(resource);
    }
    var state=new StartupLifecycle(new StandardHost(),Set.of());var naming=new StartupNaming[1];var events=new ArrayList<String>();
    var facade=new SecuredBpmPlatformBootstrap();
    server.addLifecycleListener(event->{
      events.add(event.getType());
      try {
        if(Lifecycle.BEFORE_START_EVENT.equals(event.getType())) {
          try {naming[0]=new StartupNaming(server,custody,state);}
          catch(Refused preflight) {state.fatal();throw preflight;} // SecuredBpmPlatformBootstrap marks preflight refusal fatal
          field(SecuredBpmPlatformBootstrap.class,facade,"naming",naming[0]);
          field(SecuredBpmPlatformBootstrap.class,facade,"preflightSeen",true);
          field(SecuredBpmPlatformBootstrap.class,null,"active",facade);
          if(refused)state.refusePolicy();else state.acceptPolicy();
        }
        if(Lifecycle.CONFIGURE_START_EVENT.equals(event.getType()) && attack!=null && !attack.equals("delegate")) {
          var listener=Arrays.stream(server.findLifecycleListeners()).filter(x->x.getClass()==NamingContextListener.class).map(x->(NamingContextListener)x).findFirst().orElseThrow();
          var child=(NamingContext)StartupNaming.entry(listener.getEnvContext(),"jdbc").value;
          Object reference=StartupNaming.entry(child,"ProcessEngine").value;
          if(attack.equals("metadata"))((Reference)reference).add(new StringRefAddr("unexpected","no"));
          Object supplied=attack.equals("reference")?((Reference)reference).clone():reference;
          var name=new CompositeName(attack.equals("name")?"jdbc/ProcessEngine":"ProcessEngine");
          var context=attack.equals("context")?listener.getEnvContext():child;
          if(attack.equals("fatal-error")) {
            Name errorName=(Name)Proxy.newProxyInstance(Name.class.getClassLoader(),new Class<?>[]{Name.class},
                (proxy,method,args)->{throw new LinkageError("unit-escaping-error");});
            naming[0].resolve(reference,errorName,child,new Hashtable<>());
            fail("escaping Error must abort CONFIGURE_START");
          }
          assertThrows(Refused.class,()->naming[0].resolve(supplied,name,context,new Hashtable<>()));
          assertEquals(StartupLifecycle.Stage.FATAL,state.stage());
        }
        if(Lifecycle.STOP_EVENT.equals(event.getType()))state.stopped();
        if(Lifecycle.START_EVENT.equals(event.getType()) && !refused) {
          naming[0].beforeVendor();state.vendorStarting();state.claimConfiguration(new StandaloneProcessEngineConfiguration());state.vendorStarted();
        }
      }catch(RuntimeException e){throw e;}catch(Exception e){throw new IllegalStateException(e);}
    });
    server.addLifecycleListener(new SecuredGlobalResourcesLifecycleListener());
    return new Run(server,state,naming,events);
  }
  @Test void realNamingCatchPreservesRefusedReferenceWithoutAnyJdbcConstruction()throws Exception {
    try(var run=prepare(true,null)) {
      run.server.start();run.naming[0].verifyRefused();
      assertEquals(0,connections.get());assertEquals(LifecycleState.STARTED,run.server.getState());
      assertTrue(run.events.indexOf(Lifecycle.BEFORE_START_EVENT)<run.events.indexOf(Lifecycle.CONFIGURE_START_EVENT));
      assertTrue(run.events.indexOf(Lifecycle.CONFIGURE_START_EVENT)<run.events.indexOf(Lifecycle.START_EVENT));
    }
  }
  @Test void actualOriginalFactoryCreatesOneCachedSingletonAndPreservesOriginalStop()throws Exception {
    org.apache.tomcat.jdbc.pool.DataSource owned=null;
    try(var run=prepare(false,null)) {
      run.server.start();run.naming[0].verifyPositive();assertTrue(connections.get()>0);
      Object first=run.server.getGlobalNamingContext().lookup(StartupNaming.JDBC);
      assertEquals("org.apache.tomcat.jdbc.pool.DataSource",first.getClass().getName());
      owned=(org.apache.tomcat.jdbc.pool.DataSource)first;
      assertSame(first,run.server.getGlobalNamingContext().lookup(StartupNaming.JDBC));
      assertThrows(Refused.class,run.naming[0]::beforeVendor);
    } finally {
      // The unchanged vendor Resource has no explicit closeMethod and this JDBC class is
      // not AutoCloseable. Release the exact unit-owned pool; do not claim Tomcat closed it.
      if(owned!=null)owned.close(true);
    }
    assertEquals(connections.get(),closes.get());
  }
  @Test void envSubstitutedServerXmlMatchesAdmissionAndReachesTheVendorPool()throws Exception {
    System.setProperty(PROPERTY_SOURCE,"org.apache.tomcat.util.digester.EnvironmentPropertySource");
    org.apache.tomcat.jdbc.pool.DataSource owned=null;
    try(var run=prepare(false,null,"${MAEZO_P25_DB_USER:-unit}")) {
      run.server.start();run.naming[0].verifyPositive();assertTrue(connections.get()>0);
      owned=(org.apache.tomcat.jdbc.pool.DataSource)run.server.getGlobalNamingContext().lookup(StartupNaming.JDBC);
      assertEquals("unit",owned.getUsername());
    } finally {
      System.clearProperty(PROPERTY_SOURCE);
      if(owned!=null)owned.close(true);
    }
    assertEquals(connections.get(),closes.get());
  }
  @Test void unresolvedAdmissionExpectationRefusesTheResolvedServerXml()throws Exception {
    // Preflight refusal fires before any binding, so even the stop path refuses afterwards;
    // the boot started nothing (no pool, no JNDI bind) and the server stays FAILED, never closed.
    Run run=prepare(false,null,"${MAEZO_P25_DB_USER}",key->"unit");
    var failure=assertThrows(LifecycleException.class,run.server::start);
    assertEquals("br.com.maezo.workload.Refused",failure.getCause().getClass().getName());
    assertFalse(run.events.contains(Lifecycle.START_EVENT));
    assertEquals(StartupLifecycle.Stage.FATAL,run.state.stage());
    assertEquals(LifecycleState.FAILED,run.server.getState());
    assertEquals(0,connections.get());
  }
  @Test void escapingOwnerErrorBeforeServerStartRetainsFatalCleanupProvenance()throws Exception {
    Run run=prepare(true,"fatal-error");
    try {
      assertThrows(LifecycleException.class,run.server::start);
      assertFalse(run.events.contains(Lifecycle.START_EVENT));
      assertEquals(StartupLifecycle.Stage.FATAL,run.state.stage());assertEquals(0,connections.get());
    } finally {run.close();}
    assertTrue(run.state.failedBeforeStop());assertEquals(StartupLifecycle.Stage.STOPPED,run.state.stage());
    assertTrue(run.events.contains(Lifecycle.CONFIGURE_STOP_EVENT));
  }
  @Test void actualVendorCatchCannotConvertDelegateFailureIntoAcceptedStartup()throws Exception {
    try(var run=prepare(false,"delegate")) {
      assertThrows(LifecycleException.class,run.server::start);
      assertEquals(StartupLifecycle.Stage.FATAL,run.state.stage());assertEquals(0,connections.get());
    } finally {failDriver=false;}
    assertTrue(org.apache.tomcat.jdbc.pool.ConnectionPool.getPoolCleaners().isEmpty());
  }
  @ParameterizedTest @ValueSource(strings={"reference","context","name","metadata"})
  void reconstructedOrChangedNamingClaimsMakeOwnerFatalBeforeDelegate(String attack)throws Exception {
    try(var run=prepare(true,attack)) {
      assertThrows(LifecycleException.class,run.server::start);assertEquals(StartupLifecycle.Stage.FATAL,run.state.stage());
      assertEquals(0,connections.get());
    }
  }
  private static void field(Class<?> type,Object object,String name,Object value)throws Exception {
    var field=type.getDeclaredField(name);field.setAccessible(true);field.set(object,value);
  }
}
