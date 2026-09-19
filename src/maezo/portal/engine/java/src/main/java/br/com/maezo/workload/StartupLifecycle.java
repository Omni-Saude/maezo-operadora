package br.com.maezo.workload;

import jakarta.servlet.FilterConfig;
import jakarta.servlet.ServletContext;
import java.util.IdentityHashMap;
import java.util.Map;
import java.util.Set;
import org.apache.catalina.Context;
import org.apache.catalina.Host;
import org.apache.catalina.Lifecycle;
import org.apache.catalina.LifecycleEvent;
import org.apache.catalina.LifecycleState;
import org.apache.catalina.core.StandardContext;
import org.cibseven.bpm.engine.impl.cfg.ProcessEngineConfigurationImpl;

/** Object-bound, synchronized startup provenance; never a caller-settable admission flag. */
final class StartupLifecycle {
  enum Stage { NEW, PREFLIGHT_ACCEPTED, STARTING, STARTED, PREFLIGHT_REFUSED, FATAL, STOPPED }
  private Stage stage=Stage.NEW;
  private boolean fatalSeen;
  private final Host host;
  private final Set<String> expectedPaths;
  private final Map<Context,ContextRecord> contexts=new IdentityHashMap<>();
  private ProcessEngineConfigurationImpl configuration;

  private static final class ContextRecord {
    final Context context;
    boolean beforeStart, filterRefused, failedBeforeStop, stopped;
    FilterConfig successfulFilter;
    Object restOwner;
    boolean restEntered, restCompleted, restDestroyed;
    ContextRecord(Context context) {this.context=context;}
  }

  StartupLifecycle(Host host,Set<String> expectedPaths) {
    this.host=host;this.expectedPaths=Set.copyOf(expectedPaths);
  }
  synchronized Stage stage() {return stage;}
  synchronized void refusePolicy() {require(stage==Stage.NEW);stage=Stage.PREFLIGHT_REFUSED;}
  synchronized void acceptPolicy() {require(stage==Stage.NEW);stage=Stage.PREFLIGHT_ACCEPTED;}
  synchronized void vendorStarting() {require(stage==Stage.PREFLIGHT_ACCEPTED);stage=Stage.STARTING;}
  synchronized void claimConfiguration(ProcessEngineConfigurationImpl value) {
    require(stage==Stage.STARTING && value!=null && configuration==null);configuration=value;
  }
  synchronized void vendorStarted() {
    require(stage==Stage.STARTING && configuration!=null);stage=Stage.STARTED;
  }
  synchronized boolean isConfiguration(ProcessEngineConfigurationImpl value) {return configuration==value;}
  synchronized void fatal() {fatalSeen=true;stage=Stage.FATAL;}
  synchronized boolean failedBeforeStop() {return fatalSeen && (stage==Stage.FATAL || stage==Stage.STOPPED);}
  synchronized void stopped() {
    require(stage==Stage.STARTED || stage==Stage.PREFLIGHT_REFUSED || stage==Stage.FATAL);
    stage=Stage.STOPPED;
  }

  synchronized void enroll(Context context) {
    require(context.getClass()==StandardContext.class && context.getParent()==host
        && expectedPaths.contains(context.getPath()) && !contexts.containsKey(context)
        && contexts.keySet().stream().noneMatch(old->old.getPath().equals(context.getPath()))
        && (context.getState()==LifecycleState.NEW || context.getState()==LifecycleState.INITIALIZED));
    contexts.put(context,new ContextRecord(context));
    context.addLifecycleListener(this::contextEvent);
  }

  private synchronized void contextEvent(LifecycleEvent event) {
    require(event.getSource() instanceof Context);
    var record=contexts.get((Context)event.getSource());require(record!=null);
    switch(event.getType()) {
      case Lifecycle.BEFORE_START_EVENT -> {
        require(!record.beforeStart && (stage==Stage.STARTED || stage==Stage.PREFLIGHT_REFUSED));
        record.beforeStart=true;
      }
      case Lifecycle.BEFORE_STOP_EVENT -> {
        if(stage==Stage.PREFLIGHT_REFUSED) {
          require(record.beforeStart && record.filterRefused
              && record.context.getState()==LifecycleState.FAILED);
          record.failedBeforeStop=true;
        }
      }
      case Lifecycle.AFTER_STOP_EVENT -> {
        if(stage==Stage.PREFLIGHT_REFUSED) {
          require(record.failedBeforeStop && record.context.getState()==LifecycleState.STOPPED);
          record.stopped=true;
        }
      }
      default -> { }
    }
  }

  synchronized void requireFilterStart(FilterConfig filter) {
    var record=record(filter.getServletContext());
    require(record.beforeStart && "maezo-boundary".equals(filter.getFilterName()));
    if(stage==Stage.PREFLIGHT_REFUSED) {
      require(!record.filterRefused);record.filterRefused=true;throw Refused.unavailable();
    }
    require(stage==Stage.STARTED);
  }

  synchronized void filterCompleted(FilterConfig filter) {
    var record=record(filter.getServletContext());
    require(stage==Stage.STARTED && record.beforeStart && !record.filterRefused
        && record.successfulFilter==null && "maezo-boundary".equals(filter.getFilterName()));
    record.successfulFilter=filter;
  }

  synchronized boolean restMayStart(ServletContext servletContext,Object owner) {
    var record=record(servletContext);
    require(record.beforeStart && "/engine-rest".equals(record.context.getPath())
        && owner!=null && record.restOwner==null
        && owner.getClass().getName().equals("br.com.maezo.workload.SecuredFetchAndLockContextListener")
        && owner.getClass().getClassLoader()==record.context.getLoader().getClassLoader());
    require(stage==Stage.STARTED || stage==Stage.PREFLIGHT_REFUSED);
    record.restOwner=owner;
    return stage==Stage.STARTED;
  }

  synchronized void restEntered(ServletContext servletContext,Object owner) {
    var record=record(servletContext);
    require(stage==Stage.STARTED && record.restOwner==owner && !record.restEntered && !record.restDestroyed);
    record.restEntered=true;
  }

  synchronized void restCompleted(ServletContext context,Object owner) {
    var record=record(context);
    require(stage==Stage.STARTED && record.restOwner==owner && record.restEntered
        && !record.restCompleted && !record.restDestroyed);record.restCompleted=true;
  }

  synchronized void restDestroyed(ServletContext servletContext,Object owner) {
    var record=record(servletContext);
    require(record.restOwner==owner && !record.restDestroyed);record.restDestroyed=true;
  }

  synchronized void verifyRefusedContexts() {
    require(stage==Stage.PREFLIGHT_REFUSED && configuration==null
        && contexts.size()==expectedPaths.size());
    for(var record:contexts.values()) {
      require(record.beforeStart && record.filterRefused && record.failedBeforeStop && record.stopped
          && record.context.getState()==LifecycleState.STOPPED && !record.context.getState().isAvailable());
      StandardContext context=(StandardContext)record.context;
      for(var filter:context.findFilterDefs())require(context.findFilterConfig(filter.getFilterName())==null);
      if("/engine-rest".equals(context.getPath()))require(record.restOwner!=null
          && !record.restEntered && record.restDestroyed);
    }
  }

  synchronized void verifyPositiveContexts() {
    require(stage==Stage.STARTED && configuration!=null);
    verifyContextCompletion();
    WorkloadPlugin.running().verifyStartup(configuration);
  }

  /** Context completion only; engine and policy identity remain the caller's responsibility. */
  synchronized void verifyContextCompletion() {
    require(contexts.size()==expectedPaths.size());
    require(host.findChildren().length==contexts.size());
    for(var child:host.findChildren())require(contexts.containsKey(child)
        && child.getParent()==host && expectedPaths.contains(((Context)child).getPath()));
    for(var record:contexts.values()) {
      var context=(StandardContext)record.context;
      require(record.beforeStart && !record.filterRefused && !record.stopped
          && context.getState()==LifecycleState.STARTED && context.getState().isAvailable()
          && record.successfulFilter!=null
          && context.findFilterConfig("maezo-boundary")==record.successfulFilter);
      for(var filter:context.findFilterDefs())require(context.findFilterConfig(filter.getFilterName())!=null);
      for(var child:context.findChildren()) {
        require(child.getClass()==org.apache.catalina.core.StandardWrapper.class);
        var wrapper=(org.apache.catalina.core.StandardWrapper)child;
        if(wrapper.getLoadOnStartup()<0)continue;
        try {
          var initialized=org.apache.catalina.core.StandardWrapper.class.getDeclaredField("instanceInitialized");
          initialized.setAccessible(true);
          require(wrapper.getServlet()!=null && initialized.getBoolean(wrapper) && wrapper.getAvailable()==0 && wrapper.isEnabled()
              && wrapper.getState()==LifecycleState.STARTED);
        } catch(ReflectiveOperationException e) {throw Refused.unavailable();}
      }
      if("/engine-rest".equals(context.getPath()))require(record.restOwner!=null
          && record.restEntered && record.restCompleted && !record.restDestroyed);
    }
  }

  private ContextRecord record(ServletContext servletContext) {
    require(servletContext!=null);
    ContextRecord found=null;
    for(var candidate:contexts.values())if(candidate.context.getServletContext()==servletContext) {
      require(found==null);found=candidate;
    }
    require(found!=null);return found;
  }
  private static void require(boolean value) {if(!value)throw Refused.unavailable();}
}
