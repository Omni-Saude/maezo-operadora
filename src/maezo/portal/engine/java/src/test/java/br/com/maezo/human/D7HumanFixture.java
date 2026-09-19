package br.com.maezo.human;

import java.util.*;
import org.cibseven.bpm.engine.ProcessEngine;
import org.cibseven.bpm.engine.task.Task;

/** Test-only typed reuse of the unchanged D5 real-engine fixture; no fabricated receipts. */
public final class D7HumanFixture {
  private final AtomicEngineIT fixture = new AtomicEngineIT();
  public D7HumanFixture() {
    fixture.keys = new TestKeys();
    var trust = fixture.keys.config();
    trust.put("engine_name", "default");
    fixture.plugin = new HumanCommandPlugin(new Trust(trust));
  }
  public HumanCommandPlugin plugin() { return fixture.plugin; }
  public void attach(ProcessEngine engine, String url, String user, String password, String schema) throws Exception {
    fixture.engine=engine; fixture.url=url; fixture.user=user; fixture.password=password; fixture.schema=schema;
    byte[] xml;
    try(var in=getClass().getResourceAsStream("/synthetic-human.bpmn")) { xml=Objects.requireNonNull(in).readAllBytes(); }
    var deployment=engine.getRepositoryService().createDeployment().tenantId("tenant-test")
        .addInputStream("synthetic-human.bpmn",new java.io.ByteArrayInputStream(xml)).deploy();
    fixture.processId=engine.getRepositoryService().createProcessDefinitionQuery().deploymentId(deployment.getId()).singleResult().getId();
    fixture.processDigest=Jcs.digest(xml);
    fixture.principal("human-test",true,List.of("synthetic-reviewers"));
  }
  public Task claimed() throws Exception { return fixture.claimed(); }
  public Map<String,Object> command(Task task,String operation) throws Exception {
    return fixture.command(task,operation,"d7-"+UUID.randomUUID());
  }
  public byte[] send(Map<String,Object> command) { return fixture.send(command); }
}
