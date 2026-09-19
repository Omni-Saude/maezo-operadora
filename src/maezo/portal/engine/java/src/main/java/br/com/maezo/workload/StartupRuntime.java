package br.com.maezo.workload;

import java.util.*;
import org.cibseven.bpm.container.RuntimeContainerDelegate;
import org.cibseven.bpm.container.impl.RuntimeContainerDelegateImpl;
import org.cibseven.bpm.container.impl.jmx.MBeanServiceContainer;
import org.cibseven.bpm.container.impl.spi.ServiceTypes;
import org.cibseven.bpm.engine.ProcessEngines;
import org.cibseven.bpm.engine.impl.ProcessEngineImpl;
import org.cibseven.bpm.engine.impl.util.CompositeCondition;

/** Non-initializing native registry reads; never ProcessEngines.init/getDefaultEngine. */
final class StartupRuntime {
  static void empty() {
    try {
      StartupAdmission.require(ProcessEngines.getProcessEngines().isEmpty() && !WorkloadPlugin.installed());
      var delegate=RuntimeContainerDelegate.INSTANCE.get();
      StartupAdmission.require(delegate.getClass()==RuntimeContainerDelegateImpl.class);
      var services=((RuntimeContainerDelegateImpl)delegate).getServiceContainer();
      StartupAdmission.require(services.getClass()==MBeanServiceContainer.class);
      for(var type:ServiceTypes.values())StartupAdmission.require(services.getServiceNames(type).isEmpty());
      // Exact pinned private fields are read only. Public APIs omit unregistered map entries
      // and the native condition consumer list; inability to inspect is fatal, never empty.
      var serviceField=MBeanServiceContainer.class.getDeclaredField("servicesByName");serviceField.setAccessible(true);
      StartupAdmission.require(((Map<?,?>)serviceField.get(services)).isEmpty());
      var conditionField=CompositeCondition.class.getDeclaredField("conditions");conditionField.setAccessible(true);
      StartupAdmission.require(((List<?>)conditionField.get(ProcessEngineImpl.EXT_TASK_CONDITIONS)).isEmpty());
      for(var entry:Thread.getAllStackTraces().entrySet())if(entry.getKey().isAlive())
        for(var frame:entry.getValue()) {
          String type=frame.getClassName();
          StartupAdmission.require(!type.startsWith("org.cibseven.bpm.engine.impl.jobexecutor.")
              && !type.startsWith("org.cibseven.bpm.engine.impl.metrics.")
              && !type.startsWith("org.cibseven.bpm.engine.rest.impl.FetchAndLockHandlerImpl"));
        }
    } catch(ReflectiveOperationException | ClassCastException e) {throw Refused.unavailable();}
  }
}
