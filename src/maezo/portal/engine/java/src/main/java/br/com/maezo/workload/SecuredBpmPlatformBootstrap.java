package br.com.maezo.workload;

import jakarta.servlet.FilterConfig;
import jakarta.servlet.ServletContext;
import org.apache.catalina.*;
import org.apache.catalina.core.*;
import org.cibseven.bpm.container.impl.tomcat.TomcatBpmPlatformBootstrap;
import org.cibseven.bpm.engine.impl.cfg.ProcessEngineConfigurationImpl;

/** ADR-0053: the secured v1 Server owner. Never continue after entering vendor deployment. */
public final class SecuredBpmPlatformBootstrap implements LifecycleListener {
  private static volatile SecuredBpmPlatformBootstrap active;
  private StartupLifecycle state;
  private StartupCustody custody;
  private StartupAdmission admission;
  private StartupTopology topology;
  private TomcatBpmPlatformBootstrap vendor;
  private StartupNaming naming;
  private boolean preflightSeen, startSeen, stopSeen;

  @Override public synchronized void lifecycleEvent(LifecycleEvent event) {
    if(Lifecycle.BEFORE_START_EVENT.equals(event.getType())) {
      require(!preflightSeen && event.getSource().getClass()==StandardServer.class
          && event.getLifecycle().getState()==LifecycleState.STARTING_PREP);preflightSeen=true;
      synchronized(SecuredBpmPlatformBootstrap.class) {require(active==null);active=this;}
      try {
        custody=StartupCustody.environment();
        topology=new StartupTopology((StandardServer)event.getSource(),custody,this);
        admission=new StartupAdmission(custody);
        state=new StartupLifecycle(topology.host,StartupAdmission.PATHS);
        topology.host.addContainerListener(this::hostEvent);
        for(var child:topology.host.findChildren())enroll(child);
        topology.observe(this::engineEvent);
        naming=new StartupNaming(topology.server,custody,state);
        StartupRuntime.empty();
        // Only this preflight admission call has a contained refusal boundary.
        try {BoundaryPolicy.environment();}
        catch(Refused refused) {
          if(refused.status!=503 || !"engine_profile_unavailable".equals(refused.code))throw refused;
          state.refusePolicy();return;
        }
        state.acceptPolicy();
      } catch(RuntimeException | Error failure) {
        if(state!=null)state.fatal();throw failure;
      }
    } else if(Lifecycle.START_EVENT.equals(event.getType())) {
      require(preflightSeen && !startSeen && event.getSource()==topology.server);startSeen=true;
      try {
        custody.current();admission.current();topology.current();naming.current();
        if(state.stage()==StartupLifecycle.Stage.PREFLIGHT_REFUSED)return;
        naming.beforeVendor();state.vendorStarting();vendor=new TomcatBpmPlatformBootstrap();
        vendor.lifecycleEvent(event);state.vendorStarted();
      } catch(RuntimeException | Error failure) {state.fatal();throw failure;}
    } else if(Lifecycle.STOP_EVENT.equals(event.getType())) {
      require(preflightSeen && !stopSeen && topology!=null && event.getSource()==topology.server
          && topology.server.getState()==LifecycleState.STOPPING
          && (startSeen || (state!=null && state.failedBeforeStop() && vendor==null)));stopSeen=true;
      if(state!=null) {
        if(vendor!=null)vendor.lifecycleEvent(event);
        state.stopped();
      }
    }
  }
  private void hostEvent(ContainerEvent event) {
    require(event.getContainer()==topology.host);
    if(Container.ADD_CHILD_EVENT.equals(event.getType())) {
      require(event.getData() instanceof Container);enroll((Container)event.getData());
    } else if(Container.REMOVE_CHILD_EVENT.equals(event.getType())) {
      require(stopSeen); // The admitted context set is immutable through the interval.
    }
  }
  private void enroll(Container child) {
    require(child.getClass()==StandardContext.class);
    var context=(StandardContext)child;
    // HostConfig already attached ContextConfig; it processes defaults at AFTER_INIT.
    // This observer runs BEFORE_START, before CONFIGURE_START constructs any SCI.
    StartupTopology.listeners(context,java.util.Set.of("org.apache.catalina.startup.ContextConfig",
        "org.apache.catalina.core.StandardHost$MemoryLeakTrackingListener"),null);
    state.enroll(context);
    new StartupContextListeners(context,topology.threadLocal,()-> {
      admission.beforeContextStart(context);topology.hostCache.beforeContext(context);
    },()->topology.hostCache.configuredContext(context));
  }
  private void engineEvent(LifecycleEvent event) {
    require(event.getSource()==topology.engine);
    if(Lifecycle.AFTER_START_EVENT.equals(event.getType())) {
      try {
        custody.current();admission.current();topology.current();
        if(state.stage()==StartupLifecycle.Stage.PREFLIGHT_REFUSED) {
          state.verifyRefusedContexts();naming.verifyRefused();StartupRuntime.empty();
        } else {naming.verifyPositive();state.verifyPositiveContexts();}
      } catch(RuntimeException | Error failure) {state.fatal();throw failure;}
    }
  }
  private static StartupLifecycle current() {
    var owner=active;require(owner!=null && owner.state!=null && owner.startSeen && !owner.stopSeen);
    return owner.state;
  }
  static void claimConfiguration(ProcessEngineConfigurationImpl configuration) {current().claimConfiguration(configuration);}
  static void filterStart(FilterConfig config) {current().requireFilterStart(config);}
  static void filterCompleted(FilterConfig config) {current().filterCompleted(config);}
  static StartupNaming naming() {
    var owner=active;require(owner!=null && owner.preflightSeen && !owner.stopSeen && owner.naming!=null);
    return owner.naming;
  }
  public static void restCompleted(ServletContext context,Object listener) {current().restCompleted(context,listener);}
  /** Only the exact enrolled REST-loader listener can use these object-authenticated bridges. */
  public static boolean restMayStart(ServletContext context,Object listener) {return current().restMayStart(context,listener);}
  public static void restEntered(ServletContext context,Object listener) {current().restEntered(context,listener);}
  public static void restDestroyed(ServletContext context,Object listener) {
    var owner=active;require(owner!=null && owner.state!=null);owner.state.restDestroyed(context,listener);
  }
  private static void require(boolean value) {StartupAdmission.require(value);}
}
