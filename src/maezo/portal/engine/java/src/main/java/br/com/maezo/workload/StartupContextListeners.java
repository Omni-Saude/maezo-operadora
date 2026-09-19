package br.com.maezo.workload;

import org.apache.catalina.*;
import org.apache.catalina.core.*;

/** Exact additions made by the admitted Tomcat owners, not a listener-name allowlist. */
final class StartupContextListeners implements LifecycleListener {
  private final StandardContext context;
  private final ThreadLocalLeakPreventionListener threadLocal;
  private final LifecycleListener[] prefix;
  private final Runnable before,configured;
  private boolean beforeSeen,configuredSeen;

  StartupContextListeners(StandardContext context,ThreadLocalLeakPreventionListener threadLocal,
      Runnable before,Runnable configured) {
    this.context=context;this.threadLocal=threadLocal;this.before=before;this.configured=configured;
    require(context.getState()==LifecycleState.NEW && context.getNamingContextListener()==null);
    require(threadLocal.getClass()==ThreadLocalLeakPreventionListener.class);
    prefix=context.findLifecycleListeners();
    for(var listener:prefix)require(listener!=threadLocal);
    context.addLifecycleListener(this);
  }

  @Override public void lifecycleEvent(LifecycleEvent event) {
    require(event.getSource()==context);
    if(Lifecycle.BEFORE_START_EVENT.equals(event.getType())) {
      require(!beforeSeen && !configuredSeen && context.getState()==LifecycleState.STARTING_PREP);
      exact(false);before.run();beforeSeen=true;
    } else if(Lifecycle.CONFIGURE_START_EVENT.equals(event.getType())) {
      require(beforeSeen && !configuredSeen && context.getState()==LifecycleState.STARTING_PREP);
      exact(true);configured.run();configuredSeen=true;
    }
  }

  private void exact(boolean namingAdded) {
    var actual=context.findLifecycleListeners();
    require(actual.length==prefix.length+(namingAdded?3:2));
    for(int i=0;i<prefix.length;i++)require(actual[i]==prefix[i]);
    require(actual[prefix.length]==this && actual[prefix.length+1]==threadLocal);
    if(namingAdded) {
      var naming=context.getNamingContextListener();
      require(naming!=null && naming.getClass()==NamingContextListener.class
          && naming.getClass().getClassLoader()==StandardContext.class.getClassLoader()
          && actual[prefix.length+2]==naming);
    }
  }
  private static void require(boolean value) {StartupAdmission.require(value);}
}
