package br.com.maezo.workload;

import java.lang.reflect.Field;
import java.util.*;
import org.apache.catalina.*;
import org.apache.catalina.core.*;
import org.apache.catalina.startup.ContextConfig;

/** Capture the real ContextConfig cache allocation only after its configuration callback. */
final class StartupHostCache {
  private final StandardHost host;
  private final LifecycleListener hostConfig;
  private final Field cacheField;
  private final Class<?> cleanerClass,entryClass;
  private final Set<StandardContext> configured=Collections.newSetFromMap(new IdentityHashMap<>());
  private LifecycleListener cleaner;
  private Object entry;
  private StandardContext pending;

  StartupHostCache(StandardHost host) {
    this.host=host;
    require(host.getClass()==StandardHost.class && host.getStartStopThreads()==1);
    var listeners=host.findLifecycleListeners();
    require(listeners.length==1 && listeners[0].getClass().getName().equals("org.apache.catalina.startup.HostConfig")
        && listeners[0].getClass().getClassLoader()==StandardHost.class.getClassLoader());
    hostConfig=listeners[0];
    try {
      cacheField=ContextConfig.class.getDeclaredField("hostWebXmlCache");cacheField.setAccessible(true);
      cleanerClass=Class.forName("org.apache.catalina.startup.ContextConfig$HostWebXmlCacheCleaner",false,
          ContextConfig.class.getClassLoader());
      entryClass=Class.forName("org.apache.catalina.startup.ContextConfig$DefaultWebXmlCacheEntry",false,
          ContextConfig.class.getClassLoader());
    } catch(ReflectiveOperationException | RuntimeException failure) {throw Refused.unavailable();}
    current();
  }

  synchronized void current() {
    require(host.getStartStopThreads()==1 && pending==null);
    var actual=host.findLifecycleListeners();
    require(actual.length==(cleaner==null?1:2) && actual[0]==hostConfig);
    if(cleaner!=null)require(actual[1]==cleaner);
    require(cacheEntry()==entry);
  }

  synchronized void beforeContext(StandardContext context) {
    current();
    require(context.getParent()==host && context.getClass()==StandardContext.class
        && context.getState()==LifecycleState.STARTING_PREP && !configured.contains(context));
    pending=context;
  }

  synchronized void configuredContext(StandardContext context) {
    require(pending==context && context.getParent()==host && context.getConfigured()
        && context.getState()==LifecycleState.STARTING_PREP && host.getStartStopThreads()==1);
    var actual=host.findLifecycleListeners();var cache=cacheEntry();
    require(actual.length==2 && actual[0]==hostConfig && actual[1].getClass()==cleanerClass
        && cache!=null && cache.getClass()==entryClass);
    if(cleaner==null) {require(entry==null);cleaner=actual[1];entry=cache;}
    else require(actual[1]==cleaner && cache==entry);
    require(configured.add(context));pending=null;current();
  }

  private Object cacheEntry() {
    try {
      var cache=cacheField.get(null);require(cache instanceof Map<?,?>);
      return ((Map<?,?>)cache).get(host);
    } catch(IllegalAccessException | RuntimeException failure) {throw Refused.unavailable();}
  }
  private static void require(boolean value) {StartupAdmission.require(value);}
}
