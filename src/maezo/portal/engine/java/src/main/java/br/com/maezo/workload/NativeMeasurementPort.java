package br.com.maezo.workload;

import java.sql.Connection;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;

/** Fixed TestOnly instrumentation boundary; no caller-selected implementation or authority. */
interface NativeMeasurementPort {
  enum Endpoint { ENTRY, COMMITTING }
  void openStage(int sequence, String executionUuid, CommandContext ctx, Connection enlisted);
  void hold(Endpoint endpoint);
  void awaitRootRelease();
  void seal();
  void stageClosed();
  void complete(String rawResponseSha256);
  void abort();
}
