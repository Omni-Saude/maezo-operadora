package br.com.maezo.workload;

import org.apache.catalina.Lifecycle;
import org.apache.catalina.LifecycleEvent;
import org.apache.catalina.LifecycleListener;
import org.apache.catalina.core.StandardServer;
import org.apache.catalina.mbeans.GlobalResourcesLifecycleListener;

/** Retains the original positive JMX lifecycle; refusal never enters vendor traversal. */
public final class SecuredGlobalResourcesLifecycleListener implements LifecycleListener {
  private StartupNaming owner;
  private GlobalResourcesLifecycleListener delegate;
  private StandardServer server;
  private boolean startSeen;
  synchronized void bind(StartupNaming owner,StandardServer server) {
    StartupAdmission.require(this.owner==null && owner!=null && server!=null
        && server.getState()==org.apache.catalina.LifecycleState.STARTING_PREP);
    this.owner=owner;this.server=server;
  }
  @Override public synchronized void lifecycleEvent(LifecycleEvent event) {
    if(Lifecycle.START_EVENT.equals(event.getType())) {
      StartupAdmission.require(owner!=null && !startSeen && event.getSource()==server
          && owner==SecuredBpmPlatformBootstrap.naming());startSeen=true;
      if(!owner.globalStart(this,(StandardServer)event.getSource()))return;
      try {delegate=new GlobalResourcesLifecycleListener();delegate.lifecycleEvent(event);}
      catch(RuntimeException | Error failure){owner.fatal();throw failure;}
    } else if(Lifecycle.STOP_EVENT.equals(event.getType())) {
      StartupAdmission.require(owner!=null && event.getSource()==server);
      if(owner.globalStop(this,(StandardServer)event.getSource())) {
        StartupAdmission.require(delegate!=null);delegate.lifecycleEvent(event);
      }
    }
  }
}
