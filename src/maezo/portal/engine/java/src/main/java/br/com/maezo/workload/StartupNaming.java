package br.com.maezo.workload;

import java.util.*;
import javax.naming.*;
import javax.naming.spi.ObjectFactory;
import org.apache.catalina.LifecycleState;
import org.apache.catalina.core.NamingContextListener;
import org.apache.catalina.core.StandardServer;
import org.apache.catalina.deploy.NamingResourcesImpl;
import org.apache.naming.NamingContext;
import org.apache.naming.NamingEntry;
import org.apache.naming.ResourceRef;
import org.apache.tomcat.util.descriptor.web.ContextResource;
import org.w3c.dom.Element;

/** Exact installed naming owner. Reads never resolve a reference or create a resource. */
final class StartupNaming {
  static final String JDBC="jdbc/ProcessEngine";
  static final String FACTORY="br.com.maezo.workload.SecuredDataSourceFactory";
  static final String VENDOR="org.apache.tomcat.jdbc.pool.DataSourceFactory";
  private final StandardServer server;
  private final NamingResourcesImpl resources;
  private final NamingContextListener naming;
  private final SecuredGlobalResourcesLifecycleListener global;
  private final StartupLifecycle state;
  private final Map<String,ContextResource> originals=new HashMap<>();
  private final Map<String,Map<String,Object>> snapshots=new HashMap<>();
  private final Map<String,Object> jdbcReference;
  private final Map<String,Object> observedSingletons=new HashMap<>();
  private Object singleton;
  private ResourceRef refusedReference;
  private int refusals, delegateConstructions;
  private boolean globalStarted, globalDelegated, globalStopped;

  StartupNaming(StandardServer server,StartupCustody custody,StartupLifecycle state) {
    this.server=server;this.state=state;resources=server.getGlobalNamingResources();
    require(resources.getClass()==NamingResourcesImpl.class && resources.getContainer()==server);
    NamingContextListener n=null;SecuredGlobalResourcesLifecycleListener g=null;
    for(var listener:server.findLifecycleListeners()) {
      if(listener.getClass()==NamingContextListener.class){require(n==null);n=(NamingContextListener)listener;}
      if(listener.getClass()==SecuredGlobalResourcesLifecycleListener.class){require(g==null);g=(SecuredGlobalResourcesLifecycleListener)listener;}
    }
    require(n!=null && g!=null && n.getClass().getClassLoader()==StandardServer.class.getClassLoader());
    naming=n;global=g;
    var xml=SecureLayout.parse(custody.root.resolve("conf/server.xml"));
    var catalogs=xml.getElementsByTagName("GlobalNamingResources");require(catalogs.getLength()==1);
    var nodes=catalogs.item(0).getChildNodes();var expected=new HashMap<String,Map<String,Object>>();
    for(int i=0;i<nodes.getLength();i++)if(nodes.item(i) instanceof Element e) {
      require("Resource".equals(e.getTagName()));
      var attrs=new HashMap<String,Object>();
      attrs.put("scope","Shareable");attrs.put("singleton","true");
      var all=e.getAttributes();for(int j=0;j<all.getLength();j++)attrs.put(all.item(j).getNodeName(),all.item(j).getNodeValue());
      require(expected.put(e.getAttribute("name"),Map.copyOf(attrs))==null);
    }
    require(expected.keySet().equals(Set.of(JDBC,"UserDatabase",
        "global/camunda-bpm-platform/process-engine/ProcessEngineService!org.cibseven.bpm.ProcessEngineService",
        "global/camunda-bpm-platform/process-engine/ProcessApplicationService!org.cibseven.bpm.ProcessApplicationService")));
    for(var resource:resources.findResources()) {
      require(resource.getClass()==ContextResource.class && originals.put(resource.getName(),resource)==null);
      var snapshot=snapshot(resource,false);require(snapshot.equals(expected.get(resource.getName())));
      require(resource.getSingleton() && "Container".equals(resource.getAuth())
          && resource.getLookupName()==null && resource.getCloseMethod()==null);
      snapshots.put(resource.getName(),snapshot);
      String factory=(String)resource.getProperty("factory");
      if(JDBC.equals(resource.getName())) {
        require("javax.sql.DataSource".equals(resource.getType()) && FACTORY.equals(factory));
        require(properties(resource).keySet().equals(Set.of("factory","uniqueResourceName","driverClassName",
            "url","defaultTransactionIsolation","username","password","maxActive","minIdle","maxIdle")));
      } else if("UserDatabase".equals(resource.getName()))require("org.apache.catalina.users.MemoryUserDatabaseFactory".equals(factory)
          && "org.apache.catalina.UserDatabase".equals(resource.getType()));
      else require(("org.cibseven.bpm.container.impl.jndi."+resource.getType().substring(resource.getType().lastIndexOf('.')+1)+"ObjectFactory").equals(factory));
    }
    require(originals.keySet().equals(expected.keySet()));
    jdbcReference=reference(expectedReference(originals.get(JDBC)));
    current();global.bind(this,server);
  }
  synchronized void current() {
    require(server.getGlobalNamingResources()==resources && resources.findResources().length==originals.size());
    require(Arrays.stream(server.findLifecycleListeners()).filter(x->x==naming).count()==1
        && Arrays.stream(server.findLifecycleListeners()).filter(x->x==global).count()==1);
    for(var resource:resources.findResources())require(originals.get(resource.getName())==resource
        && snapshots.get(resource.getName()).equals(snapshot(resource,singleton!=null && JDBC.equals(resource.getName()))));
    try {
      for(String method:List.of("findEjbs","findEnvironments","findLocalEjbs","findMessageDestinationRefs",
          "findResourceEnvRefs","findResourceLinks","findServices"))require(((Object[])NamingResourcesImpl.class.getMethod(method).invoke(resources)).length==0);
      require(resources.getTransaction()==null);
      if(naming.getEnvContext()!=null)verifyCatalog(naming.getEnvContext(),"",server.getState()!=LifecycleState.STARTING_PREP);
    } catch(ReflectiveOperationException e){throw Refused.unavailable();}
  }
  synchronized void beforeVendor() {
    current();require(state.stage()==StartupLifecycle.Stage.PREFLIGHT_ACCEPTED && singleton!=null && delegateConstructions==1);
    var entry=jdbcEntry();require(entry.type==NamingEntry.ENTRY && entry.value==singleton);
  }
  synchronized Object resolve(Object obj,Name name,javax.naming.Context context,Hashtable<?,?> environment) throws Exception {
    try {
      current();require(server.getState()==LifecycleState.STARTING_PREP);
      require(name!=null && name.size()==1 && "ProcessEngine".equals(name.get(0)));
      var entry=jdbcEntry();require(entry.type==NamingEntry.REFERENCE && entry.value==obj
          && context==jdbcContext() && obj!=null && obj.getClass()==ResourceRef.class
          && reference((ResourceRef)obj).equals(jdbcReference));
      require(singleton==null && delegateConstructions==0);
      if(state.stage()==StartupLifecycle.Stage.PREFLIGHT_REFUSED) {
        require(refusedReference==null || refusedReference==obj);refusedReference=(ResourceRef)obj;refusals++;
        throw new ExpectedRefusal();
      }
      require(state.stage()==StartupLifecycle.Stage.PREFLIGHT_ACCEPTED);
      // Construct only after authentic owner, exact binding and positive preflight.
      Class<?> type=Class.forName(VENDOR,false,StandardServer.class.getClassLoader());
      require(type.getClassLoader()==StandardServer.class.getClassLoader());
      delegateConstructions++;
      ObjectFactory delegate=(ObjectFactory)type.getConstructor().newInstance();
      // The original JDBC factory ignores the dispatch 'factory' address; preserve every argument.
      singleton=delegate.getObjectInstance(obj,name,context,environment);
      require(singleton!=null && singleton.getClass().getName().equals("org.apache.tomcat.jdbc.pool.DataSource")
          && singleton.getClass().getClassLoader()==StandardServer.class.getClassLoader());
      return singleton;
    } catch(ExpectedRefusal expected) {throw expected;}
    catch(Exception | Error failure) {state.fatal();throw failure;}
  }
  synchronized boolean globalStart(Object wrapper,StandardServer source) {
    try {
      require(wrapper==global && source==server && !globalStarted && server.getState()==LifecycleState.STARTING);
      globalStarted=true;current();
      if(state.stage()==StartupLifecycle.Stage.PREFLIGHT_REFUSED)return false;
      require(state.stage()==StartupLifecycle.Stage.STARTED && singleton!=null);
      globalDelegated=true;return true;
    } catch(RuntimeException | Error failure){state.fatal();throw failure;}
  }
  synchronized boolean globalStop(Object wrapper,StandardServer source) {
    require(wrapper==global && source==server && !globalStopped
        && (globalStarted || state.failedBeforeStop())
        && server.getState()==LifecycleState.STOPPING);globalStopped=true;
    return globalDelegated;
  }
  synchronized void fatal() {state.fatal();}
  synchronized void verifyRefused() {
    current();require(state.stage()==StartupLifecycle.Stage.PREFLIGHT_REFUSED && globalStarted
        && !globalDelegated && !globalStopped && delegateConstructions==0 && singleton==null
        && refusals>=1 && refusedReference!=null);
    var entry=jdbcEntry();require(entry.type==NamingEntry.REFERENCE && entry.value==refusedReference
        && reference(refusedReference).equals(jdbcReference));
    requireNoPool();
  }
  synchronized void verifyPositive() {
    current();require(state.stage()==StartupLifecycle.Stage.STARTED && globalStarted && globalDelegated
        && !globalStopped && delegateConstructions==1 && singleton!=null && refusals==0);
    var entry=jdbcEntry();require(entry.type==NamingEntry.ENTRY && entry.value==singleton);
  }
  private void verifyCatalog(Object context,String prefix,boolean complete) {
    var bindings=bindings(context);var expected=new HashSet<String>();
    for(String path:originals.keySet())if(path.startsWith(prefix))expected.add(path.substring(prefix.length()).split("/",2)[0]);
    require(expected.containsAll(bindings.keySet()) && (!complete || expected.equals(bindings.keySet())));
    for(var item:bindings.entrySet()) {
      require(item.getKey() instanceof String && item.getValue()!=null && item.getValue().getClass()==NamingEntry.class);
      String path=prefix+item.getKey();var entry=(NamingEntry)item.getValue();require(item.getKey().equals(entry.name));
      var resource=originals.get(path);
      if(resource==null) {
        require(entry.type==NamingEntry.CONTEXT);verifyCatalog(entry.value,path+"/",complete);continue;
      }
      if(entry.type==NamingEntry.REFERENCE) {
        require(entry.value!=null && entry.value.getClass()==ResourceRef.class);
        var ref=(ResourceRef)entry.value;
        require(ref.getClassName().equals(resource.getType()) && referenceAny(ref).equals(referenceAny(expectedReference(resource))));
        require(!observedSingletons.containsKey(path));
      } else {
        require(entry.type==NamingEntry.ENTRY && entry.value!=null);
        try {require(Class.forName(resource.getType(),false,StandardServer.class.getClassLoader()).isInstance(entry.value));}
        catch(ClassNotFoundException e){throw Refused.unavailable();}
        if(JDBC.equals(path))require(singleton!=null && entry.value==singleton);
        Object previous=observedSingletons.putIfAbsent(path,entry.value);require(previous==null || previous==entry.value);
      }
    }
  }
  private static Map<?,?> bindings(Object context) {
    try {
      require(context!=null && context.getClass()==NamingContext.class);
      var field=NamingContext.class.getDeclaredField("bindings");field.setAccessible(true);
      return (Map<?,?>)field.get(context);
    }catch(ReflectiveOperationException | ClassCastException e){throw Refused.unavailable();}
  }
  private NamingContext jdbcContext() {
    require(naming.getEnvContext()!=null && naming.getEnvContext().getClass()==NamingContext.class);
    var root=entry(naming.getEnvContext(),"jdbc");
    require(root.type==NamingEntry.CONTEXT && root.value!=null && root.value.getClass()==NamingContext.class);
    return (NamingContext)root.value;
  }
  private NamingEntry jdbcEntry(){return entry(jdbcContext(),"ProcessEngine");}
  static NamingEntry entry(Object context,String key) {
    try {
      require(context!=null && context.getClass()==NamingContext.class);
      var bindings=bindings(context);Object result=bindings.get(key);
      require(result!=null && result.getClass()==NamingEntry.class);return (NamingEntry)result;
    }catch(ClassCastException e){throw Refused.unavailable();}
  }
  private static Map<String,Object> properties(ContextResource resource) {
    var values=new HashMap<String,Object>();var names=resource.listProperties();
    while(names.hasNext()){String key=names.next();require(values.put(key,resource.getProperty(key))==null);}
    return Map.copyOf(values);
  }
  private static Map<String,Object> snapshot(ContextResource resource,boolean autoClose) {
    var map=new HashMap<>(properties(resource));
    map.put("name",resource.getName());map.put("type",resource.getType());map.put("auth",resource.getAuth());
    map.put("scope",resource.getScope());map.put("singleton",Boolean.toString(resource.getSingleton()));
    if(resource.getDescription()!=null)map.put("description",resource.getDescription());
    require(resource.getLookupName()==null);
    require(resource.getCloseMethod()==null || (autoClose && "close".equals(resource.getCloseMethod())));
    return Map.copyOf(map);
  }
  private static ResourceRef expectedReference(ContextResource resource) {
    var reference=new ResourceRef(resource.getType(),resource.getDescription(),resource.getScope(),resource.getAuth(),resource.getSingleton());
    properties(resource).forEach((key,value)->reference.add(new StringRefAddr(key,(String)value)));return reference;
  }
  private static Map<String,Object> reference(ResourceRef reference) {
    require("javax.sql.DataSource".equals(reference.getClassName()));return referenceAny(reference);
  }
  private static Map<String,Object> referenceAny(ResourceRef reference) {
    require(reference.getFactoryClassName().equals("org.apache.naming.factory.ResourceFactory") && reference.getFactoryClassLocation()==null);
    var result=new HashMap<String,Object>();var all=reference.getAll();
    while(all.hasMoreElements()) {var addr=all.nextElement();require(addr.getClass()==StringRefAddr.class
        && result.put(addr.getType(),addr.getContent())==null);}
    return Map.copyOf(result);
  }
  private static void requireNoPool() {
    for(var stack:Thread.getAllStackTraces().entrySet())if(stack.getKey().isAlive())
      for(var frame:stack.getValue())require(!frame.getClassName().startsWith("org.apache.tomcat.jdbc.pool."));
    try {
      var pool=Class.forName("org.apache.tomcat.jdbc.pool.ConnectionPool",false,StandardServer.class.getClassLoader());
      var cleaners=pool.getDeclaredField("cleaners");cleaners.setAccessible(true);
      require(((Collection<?>)cleaners.get(null)).isEmpty());
      var timer=pool.getDeclaredField("poolCleanTimer");timer.setAccessible(true);require(timer.get(null)==null);
      require(java.lang.management.ManagementFactory.getPlatformMBeanServer()
          .queryNames(new javax.management.ObjectName("tomcat.jdbc:*"),null).isEmpty());
    }catch(ReflectiveOperationException | javax.management.MalformedObjectNameException | ClassCastException e){throw Refused.unavailable();}
  }
  private static final class ExpectedRefusal extends NamingException {
    ExpectedRefusal(){super("engine_profile_unavailable");}
  }
  private static void require(boolean value){StartupAdmission.require(value);}
}
