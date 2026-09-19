package br.com.maezo.workload;

import jakarta.servlet.ServletContextEvent;
import jakarta.servlet.ServletContextListener;
import org.cibseven.bpm.engine.rest.impl.FetchAndLockContextListener;

/** Lives exclusively in the REST loader; no handler/SPI is touched on terminal refusal. */
public final class SecuredFetchAndLockContextListener implements ServletContextListener {
  private enum Stage { NEW, REFUSED, ENTERED, DESTROYED }
  private Stage stage=Stage.NEW;
  private ServletContextEvent original;
  private FetchAndLockContextListener delegate;
  @Override public synchronized void contextInitialized(ServletContextEvent event) {
    if(stage!=Stage.NEW)throw new IllegalStateException("engine_profile_unavailable");
    original=event;
    if(!SecuredBpmPlatformBootstrap.restMayStart(event.getServletContext(),this)) {
      stage=Stage.REFUSED;return;
    }
    // The checked vendor static getter does not initialize the handler. Refuse adoption.
    if(FetchAndLockContextListener.getFetchAndLockHandler()!=null)
      throw new IllegalStateException("engine_profile_unavailable");
    SecuredBpmPlatformBootstrap.restEntered(event.getServletContext(),this);
    stage=Stage.ENTERED;delegate=new FetchAndLockContextListener();
    delegate.contextInitialized(event);
    SecuredBpmPlatformBootstrap.restCompleted(event.getServletContext(),this);
  }
  @Override public synchronized void contextDestroyed(ServletContextEvent event) {
    if(original==null || original.getServletContext()!=event.getServletContext()
        || stage==Stage.NEW || stage==Stage.DESTROYED)throw new IllegalStateException("engine_profile_unavailable");
    Stage previous=stage;stage=Stage.DESTROYED;
    // Partial vendor initialization owns a handler only after the vendor assigned it.
    if(previous==Stage.ENTERED && delegate!=null && FetchAndLockContextListener.getFetchAndLockHandler()!=null)
      delegate.contextDestroyed(event);
    SecuredBpmPlatformBootstrap.restDestroyed(event.getServletContext(),this);
  }
}
