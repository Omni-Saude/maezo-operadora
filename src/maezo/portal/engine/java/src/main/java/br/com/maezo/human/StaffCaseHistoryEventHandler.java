package br.com.maezo.human;

import java.util.List;
import java.util.Objects;
import org.cibseven.bpm.engine.impl.context.Context;
import org.cibseven.bpm.engine.impl.history.event.*;
import org.cibseven.bpm.engine.impl.history.handler.HistoryEventHandler;

/** Explicitly installed SC1 revision observer; preserves the actual existing history writer.
 * This is not the later timeline feed, DMN snapshot producer or a history backfill.
 */
public final class StaffCaseHistoryEventHandler implements HistoryEventHandler {
  private final HistoryEventHandler existing;
  private final StaffCaseInstallation.Configuration configuration;
  public StaffCaseHistoryEventHandler(HistoryEventHandler existing,StaffCaseInstallation.Configuration configuration){
    this.existing=Objects.requireNonNull(existing);this.configuration=Objects.requireNonNull(configuration);
    if(existing instanceof StaffCaseHistoryEventHandler)throw PortalReadModels.unavailable();
  }
  @Override public void handleEvent(HistoryEvent event){existing.handleEvent(event);observe(event);}
  @Override public void handleEvents(List<HistoryEvent> events){existing.handleEvents(events);for(var event:events)observe(event);}
  private void observe(HistoryEvent event){
    if(!(event instanceof HistoricTaskInstanceEventEntity)&&!(event instanceof HistoricProcessInstanceEventEntity))return;
    String instance=event.getProcessInstanceId();if(instance==null)return;
    var context=Context.getCommandContext();if(context==null)throw PortalReadModels.unavailable();
    var store=new AuthStore(context,configuration.authScope(),configuration.maximumSeconds());
    // Actual task/process history transitions are observed, not reconstructed from absence.
    // Claim initialization and occurrence transitions have their own same-TX ROOT hooks.
    new StaffCaseEventStore(store).advance(instance);
  }
}
