package br.com.maezo.workload;

import java.io.IOException;
import java.lang.management.ManagementFactory;
import java.net.StandardProtocolFamily;
import java.net.UnixDomainSocketAddress;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Duration;
import java.time.Instant;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeFormatterBuilder;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.TreeMap;
import jdk.net.ExtendedSocketOptions;
import jdk.net.UnixDomainPrincipal;
import org.cibseven.bpm.engine.ProcessEngines;
import org.cibseven.bpm.engine.impl.ProcessEngineImpl;
import org.cibseven.bpm.engine.impl.cfg.ProcessEngineConfigurationImpl;
import org.cibseven.bpm.engine.impl.context.Context;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;

/**
 * TestOnly independent acquisition, contract 04673 / ADR-0053 / ADR-0060.
 * The producer supplies only context/connection locators and the response digest.
 * No producer sample, serializer, measurement helper or authority callback is used.
 * ROOT independently corroborates the kernel PID, PostgreSQL and runtime materials.
 */
public final class ProviderNativeIndependentMeasurement implements NativeMeasurementPort {
  private static final String SOURCE = "independent-fixed-jvm-jdbc-acquisition.v1";
  private static final String FRAME = "provider-native-independent-measurement-frame.v2";
  private static final String INSTRUMENT = "br.com.maezo.workload.ProviderNativeIndependentMeasurement";
  private static final String FD_ATTRIBUTES = "unix:mode,uid,gid,nlink,ino,dev,size,lastModifiedTime";
  private static final Path FD_ROOT = Path.of("/proc/self/fd");
  private static final DateTimeFormatter TIME = new DateTimeFormatterBuilder().appendInstant(6).toFormatter();
  private static final int FILE_BOUND = 32 * 1024 * 1024;
  private static final String SESSION_SQL = "SELECT current_database(),d.oid::text,"
      + "(pg_control_system()).system_identifier::text,e.oid::text,n.oid::text,"
      + "current_user::text,session_user::text,pg_backend_pid()::text,a.backend_start,"
      + "s.ssl,s.version,pg_current_xact_id_if_assigned()::text,transaction_timestamp(),clock_timestamp() "
      + "FROM pg_database d JOIN pg_namespace e ON e.nspname='cibseven' "
      + "JOIN pg_namespace n ON n.nspname='maezo_native' "
      + "JOIN pg_stat_activity a ON a.pid=pg_backend_pid() "
      + "JOIN pg_stat_ssl s ON s.pid=a.pid WHERE d.datname=current_database()";
  private static final String SCHEMAS_SQL = "SELECT nspname,oid::text,pg_get_userbyid(nspowner) "
      + "FROM pg_namespace WHERE nspname IN ('cibseven','maezo_native') ORDER BY nspname";
  private static final String ACT_SQL = "SELECT n.nspname,c.relname,c.oid::text,pg_get_userbyid(c.relowner),"
      + "c.relkind::text,c.relrowsecurity,c.relforcerowsecurity,c.relacl::text "
      + "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='cibseven' "
      + "AND c.relname IN ('act_re_procdef','act_re_deployment','act_ge_bytearray') ORDER BY n.nspname,c.relname";

  private enum State { READY, OPEN, HOLD_READY, RELEASED, ENTRY_SEALED, COMMIT_SEALED,
    STAGE_CLOSED, PRIMARY_TERMINAL, PRIMARY_CLOSED, PRIMARY_SEALED, DELIVERED, UNKNOWN }
  private final NativeMeasurementConfiguration config;
  private final Budget budget;
  private final Path base;
  private final Path boundaryFile;
  private final String boundaryDigest;
  private final Path admissionFile;
  private final Map<String, Object> admission;
  private final Map<String, String> expectedFiles;
  private final byte[] admissionRaw;
  private final IdentityHashMap<CommandContext, Integer> contexts = new IdentityHashMap<>();
  private final IdentityHashMap<Connection, Integer> connections = new IdentityHashMap<>();
  private final Set<String> executions = new HashSet<>();
  private State state = State.READY;
  private Thread operationThread;
  private Stage stage;
  private SocketChannel primary;
  private SocketChannel outcomeChannel;
  private SocketIdentity primaryIdentity;
  private SocketIdentity outcomeIdentity;
  private Selector selector;
  private int ordinal = 1;
  private int stagesCompleted;
  private long totalBytes;
  private byte[] holdRaw;
  private byte[] releaseRaw;
  private Measurement held;
  private Measurement previousCommitting;
  private String lastClosedDigest;
  private FinalOutcome finalOutcome;

  /** One fixed constructor; no connect, listener, engine command or publication here. */
  public ProviderNativeIndependentMeasurement(NativeMeasurementConfiguration configuration) {
    config = Objects.requireNonNull(configuration);
    config.timeOnly();
    budget = new Budget(config.issuedAt(), config.originalDeadline());
    try {
      require("Linux".equals(System.getProperty("os.name")));
      base = canonicalDirectory(Path.of(requiredProperty("catalina.base")));
      boundaryFile = canonicalFile(Path.of(requiredEnvironment("MAEZO_ENGINE_BOUNDARY_FILE")));
      boundaryDigest = requiredEnvironment("MAEZO_ENGINE_BOUNDARY_SHA256");
      require(boundaryDigest.matches("[a-f0-9]{64}"));
      Map<String, Object> boundary = Codec.object(Codec.parse(readFile(boundaryFile, boundaryDigest, 65536)));
      require("maezo.engine-boundary.v1".equals(boundary.get("protocol")));
      require(config.environment().equals(boundary.get("environment"))
          && config.tenant().equals(boundary.get("tenant"))
          && config.engineName().equals(boundary.get("engine_name")));
      Map<String, String> files = new TreeMap<>();
      Path found = null;
      for (Object value : Codec.array(boundary.get("files"))) {
        Map<String, Object> ref = Codec.object(value);
        require(ref.keySet().equals(Set.of("path", "sha256")));
        Path path = canonicalFile(Path.of(Codec.string(ref, "path")));
        String digest = Codec.string(ref, "sha256");
        require(digest.matches("[a-f0-9]{64}") && files.put(path.toString(), digest) == null);
        if (path.getFileName().toString().equals("provider-native-observation-admission.json")) {
          require(found == null); found = path;
        }
      }
      require(found != null && config.observationAdmissionSha256().equals(files.get(found.toString())));
      admissionFile = found;
      admissionRaw = readFile(admissionFile, config.observationAdmissionSha256(), 65536);
      admission = Codec.object(Codec.parse(admissionRaw));
      require(config.sessionNonce().equals(admission.get("nonce"))
          && config.candidateSha().equals(admission.get("candidate_sha"))
          && config.originalDeadline().equals(admission.get("original_deadline"))
          && config.expectedImageId().equals(admission.get("expected_image_id"))
          && config.supportJarSha256().equals(admission.get("support_jar_sha256"))
          && config.observationSchemaSha256().equals(admission.get("observation_schema_sha256")));
      expectedFiles = Collections.unmodifiableMap(files);
      require(config.jvmUid() > 0 && config.rootPeerUid() > 0 && config.jvmUid() != config.rootPeerUid());
      require(config.jvmGid() > 0 && config.rootPeerGid() > 0);
      require(number(Files.getAttribute(Path.of("/proc/self"), "unix:uid")) == config.jvmUid()
          && number(Files.getAttribute(Path.of("/proc/self"), "unix:gid")) == config.jvmGid());
      require(config.socketPath().equals("/run/maezo-native-qualify/" + config.sessionNonce() + "/root.sock")
          && config.outcomeSocketPath().equals("/run/maezo-native-qualify/" + config.sessionNonce() + "/outcome.sock"));
      require(config.maximumConnections() == 2 && config.maximumTerminalRecords() == 1
          && config.maximumTotalFrames() == 65 && config.maximumEndpoints() == 18
          && config.maximumStageSequence() == 8 && config.maximumFrameBytes() == 32768
          && config.maximumTotalBytes() == 2097152);
      origin(); toolchain();
      clockGuard();
    } catch (IOException | RuntimeException failure) {
      throw unavailable(failure);
    }
  }

  @Override public void openStage(int sequence, String executionUuid, CommandContext context, Connection enlisted) {
    try {
      if (operationThread == null) operationThread = Thread.currentThread();
      guard();
      require((state == State.READY || state == State.STAGE_CLOSED) && sequence == stagesCompleted
          && sequence >= 0 && sequence <= 8 && executionUuid != null
          && executionUuid.matches("[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}")
          && executions.add(executionUuid) && !contexts.containsKey(context));
      require(context != null && enlisted != null);
      contexts.put(context, sequence + 1);
      if (!connections.containsKey(enlisted)) connections.put(enlisted, connections.size() + 1);
      stage = new Stage(sequence, executionUuid, context, enlisted, contexts.get(context), connections.get(enlisted));
      actualObjects();
      // Installed before MAIN appends/iterates its COMMITTING listener. The callback is pure.
      context.getTransactionContext().addTransactionListener(TransactionState.COMMITTED, ignored -> {
        require(Thread.currentThread() == operationThread && stage != null && stage.context == ignored);
        stage.committed = true; stage.committedAt = time(Instant.now());
      });
      state = State.OPEN;
      if (sequence == 0) {
        require(primary == null && selector == null);
        selector = Selector.open();
        primaryIdentity = socketIdentity(config.socketPath());
        primary = connect(primaryIdentity);
        emit("HELLO", map("instrument_origin", origin(), "jvm_toolchain", toolchain()));
      }
      require(ordinal == 2 + 7 * sequence);
      guard();
    } catch (IOException | SQLException | RuntimeException | Error failure) { throw fail(failure); }
  }

  @Override public void hold(Endpoint endpoint) {
    try {
      guard();
      require((endpoint == Endpoint.ENTRY && state == State.OPEN)
          || (endpoint == Endpoint.COMMITTING && state == State.ENTRY_SEALED));
      stage.endpoint = Objects.requireNonNull(endpoint);
      held = measure();
      if (stage.entry != null) stableStage(stage.entry, held);
      else if (previousCommitting != null) {
        stableInstallation(previousCommitting, held);
        require(!parseTime(Codec.string(previousCommitting.session, "observed_at"))
            .isAfter(parseTime(Codec.string(held.session, "observed_at"))));
        if (previousCommitting.session.get("backend_pid").equals(held.session.get("backend_pid")))
          require(previousCommitting.session.get("backend_start").equals(held.session.get("backend_start")));
        Object previousXid = previousCommitting.session.get("transaction_id_if_assigned");
        Object nextXid = held.session.get("transaction_id_if_assigned");
        require(previousXid == null || nextXid == null || !previousXid.equals(nextXid));
      }
      if (endpoint == Endpoint.ENTRY) stage.entry = held;
      state = State.HOLD_READY;
      guard();
    } catch (IOException | SQLException | RuntimeException | Error failure) { throw fail(failure); }
  }

  @Override public void awaitRootRelease() {
    try {
      guard(); require(state == State.HOLD_READY);
      holdRaw = emit("HOLD", endpointFields(map("measurement", held.document)));
      releaseRaw = receiveRelease();
      Map<String, Object> release = Codec.object(Codec.canonicalParse(releaseRaw));
      NativeMeasurementConfiguration.validate("RELEASE", release);
      require(Codec.integer(release, "event_ordinal") == ordinal
          && config.sessionNonce().equals(release.get("session_nonce"))
          && config.requestSha256().equals(release.get("request_sha256"))
          && config.digest().equals(release.get("config_sha256"))
          && config.originalDeadline().equals(release.get("original_deadline"))
          && Codec.integer(release, "stage_sequence") == stage.sequence
          && stage.execution.equals(release.get("server_stage_execution_uuid"))
          && stage.endpoint.name().equals(release.get("endpoint"))
          && sha(holdRaw).equals(release.get("hold_raw_sha256")));
      Instant releasedAt = parseTime(Codec.string(release, "emitted_at"));
      require(!releasedAt.isBefore(parseTime(Codec.string(Codec.object(Codec.parse(holdRaw)), "emitted_at")).minusSeconds(5))
          && !releasedAt.isAfter(Instant.now().plusSeconds(5))
          && releasedAt.isBefore(Instant.parse(config.originalDeadline())));
      ordinal++; require(ordinal <= 65);
      state = State.RELEASED; actualObjects(); guard();
    } catch (IOException | SQLException | RuntimeException | Error failure) { throw fail(failure); }
  }

  @Override public void seal() {
    try {
      guard(); require(state == State.RELEASED);
      Measurement fresh = measure(); stableEndpoint(held, fresh);
      emit("SEAL", endpointFields(map("hold_raw_sha256", sha(holdRaw),
          "release_raw_sha256", sha(releaseRaw), "measurement", fresh.document,
          "stable_tuple_equal_after_wait", true, "same_ctx_connection_after_wait", true)));
      // A real post-write read is a guard, not a second published SEAL sample.
      stableEndpoint(fresh, measure());
      if (stage.endpoint == Endpoint.ENTRY) state = State.ENTRY_SEALED;
      else { stage.committing = fresh; state = State.COMMIT_SEALED; }
      held = null; holdRaw = null; releaseRaw = null;
      guard();
    } catch (IOException | SQLException | RuntimeException | Error failure) { throw fail(failure); }
  }

  @Override public void stageClosed() {
    try {
      guard(); require(state == State.COMMIT_SEALED && stage.committed && stage.committing != null
          && Context.getCommandContext() == null);
      String returned = time(Instant.now()), closed = time(Instant.now());
      require(!parseTime(stage.committedAt).isAfter(parseTime(returned)) && !parseTime(returned).isAfter(parseTime(closed)));
      byte[] raw = emit("STAGE_CLOSED", map("stage_sequence", (long) stage.sequence,
          "server_stage_execution_uuid", stage.execution,
          "source", "independent-transaction-listener-and-executor-close-acquisition",
          "committed_callback_seen", stage.committed, "executor_returned_successfully", true,
          "context_closed", Context.getCommandContext() == null,
          "committed_marker_at", stage.committedAt, "executor_return_at", returned,
          "context_closed_at", closed, "ctx_reference_ordinal", (long) stage.contextOrdinal,
          "connection_reference_ordinal", (long) stage.connectionOrdinal));
      lastClosedDigest = sha(raw); previousCommitting = stage.committing; stagesCompleted++; state = State.STAGE_CLOSED;
      require(Context.getCommandContext() == null);
      // No query is issued on the retired connection after the executor returned.
      guard();
    } catch (IOException | RuntimeException | Error failure) { throw fail(failure); }
  }

  @Override public void complete(String rawResponseSha256) {
    try {
      guard(); require(state == State.STAGE_CLOSED && stagesCompleted >= 1 && stagesCompleted <= 9
          && ordinal == 2 + 7 * stagesCompleted && rawResponseSha256 != null
          && rawResponseSha256.matches("[a-f0-9]{64}") && Context.getCommandContext() == null);
      // This method is reached only after the fixed MAIN HTTP write/flush/close callsite.
      String httpClosedAt = time(Instant.now());
      byte[] terminal = emit("TERMINAL", map("technical_result", "PRIMARY_HTTP_COMPLETE_PENDING_UDS_CLOSE_GUARD",
          "stages_completed", (long) stagesCompleted, "response_sha256", rawResponseSha256,
          "last_stage_closed_raw_sha256", lastClosedDigest, "http_body_write_flush_close_completed", true));
      state = State.PRIMARY_TERMINAL;
      guard(); socketGuard(primary, primaryIdentity); primary.close();
      String udsClosedAt = time(Instant.now());
      require(!primary.isOpen()); state = State.PRIMARY_CLOSED;
      guard(); require(primaryIdentity.equals(socketIdentity(config.socketPath())));
      String guardedAt = time(Instant.now());
      guard(); String sealedAt = time(Instant.now());
      require(!parseTime(httpClosedAt).isAfter(parseTime(udsClosedAt))
          && !parseTime(udsClosedAt).isAfter(parseTime(guardedAt))
          && !parseTime(guardedAt).isAfter(parseTime(sealedAt))
          && parseTime(sealedAt).isBefore(Instant.parse(config.originalDeadline())));
      FinalOutcome own = new FinalOutcome(config.sessionNonce(), config.digest(), config.requestSha256(), rawResponseSha256,
          sha(terminal), lastClosedDigest, RuntimeObservationAdmission.BOOT, config.instrumentClassSha256(),
          config.candidateSha(), stagesCompleted, httpClosedAt, udsClosedAt, guardedAt, sealedAt, config.originalDeadline());
      byte[] raw = Codec.encode(own.document()); NativeMeasurementConfiguration.validate("FinalOutcome", own.document());
      guard(); finalOutcome = own; state = State.PRIMARY_SEALED;
      require(sealedPrimaryOutcome() == own);
      outcomeIdentity = socketIdentity(config.outcomeSocketPath());
      require(outcomeIdentity.inode != primaryIdentity.inode);
      outcomeChannel = connect(outcomeIdentity); guard();
      writeFrame(outcomeChannel, outcomeIdentity, raw);
      guard(); socketGuard(outcomeChannel, outcomeIdentity); outcomeChannel.close();
      guard(); require(!outcomeChannel.isOpen() && outcomeIdentity.equals(socketIdentity(config.outcomeSocketPath())));
      selector.close(); require(!selector.isOpen()); guard(); state = State.DELIVERED;
      // The immutable historical primary seal makes no claim about the remote delivery tail.
    } catch (IOException | RuntimeException | Error failure) { throw fail(failure); }
  }

  @Override public void abort() {
    if (state == State.UNKNOWN) { closeResources(); return; }
    state = State.UNKNOWN;
    // Never release a nonce, publish a new outcome, rollback/commit a caller TX or retry.
    closeResources();
  }

  private FinalOutcome sealedPrimaryOutcome() { require(state == State.PRIMARY_SEALED && finalOutcome != null); return finalOutcome; }
  private void closeResources() {
    closeAll(List.of(new Close(primary), new Close(outcomeChannel), new Close(selector)));
  }
  private static void closeAll(List<java.io.Closeable> resources) {
    Throwable failed = null;
    for (java.io.Closeable value : resources) {
      try { value.close(); }
      catch (IOException | RuntimeException | Error failure) {
        if (failed == null) failed = failure; else suppress(failed, failure);
      }
    }
    if (failed != null) throw unavailable(failed);
  }
  private record Close(java.io.Closeable value) implements java.io.Closeable {
    @Override public void close() throws IOException { if (value != null) value.close(); }
  }
  private RuntimeException fail(Throwable failure) {
    try { abort(); } catch (RuntimeException | Error cleanup) { suppress(failure, cleanup); }
    return unavailable(failure);
  }
  /** Cleanup never consults admission, publishes an outcome, or replaces the acquisition's primary fault. */
  private static void closeUntransferred(AutoCloseable resource, Throwable failure) {
    try { resource.close(); }
    catch (Exception | Error cleanup) { suppress(failure, cleanup); }
  }
  private static void suppress(Throwable primary, Throwable cleanup) {
    if (primary != cleanup) primary.addSuppressed(cleanup);
  }
  private static RuntimeException unavailable(Throwable failure) { return new IllegalStateException("Independent measurement unavailable", failure); }
  private static void require(boolean condition) { if (!condition) throw new IllegalStateException("Independent measurement refused"); }
  private void clockGuard() { budget.check(); config.timeOnly(); budget.check(); }
  private void guard() {
    require(state != State.UNKNOWN && (operationThread == null || Thread.currentThread() == operationThread));
    clockGuard(); config.current(); clockGuard();
  }

  private void actualObjects() throws SQLException {
    clockGuard();
    require(stage != null && Thread.currentThread() == operationThread
        && Context.getCommandContext() == stage.context
        && stage.context.getDbSqlSession().getSqlSession().getConnection() == stage.connection);
    ProcessEngineConfigurationImpl observed = Context.getProcessEngineConfiguration();
    var registry = ProcessEngines.getProcessEngines();
    require(registry.size() == 1 && registry.containsKey(config.engineName()));
    require(registry.get(config.engineName()) instanceof ProcessEngineImpl);
    ProcessEngineImpl engine = (ProcessEngineImpl) registry.get(config.engineName());
    require(engine.getProcessEngineConfiguration() == observed
        && stage.context.getProcessEngineConfiguration() == observed && engine.getName().equals(config.engineName()));
    if (stage.configuration == null) stage.configuration = observed;
    require(stage.configuration == observed && !stage.connection.isClosed() && !stage.connection.getAutoCommit()
        && stage.connection.getTransactionIsolation() == Connection.TRANSACTION_READ_COMMITTED
        && "PostgreSQL".equals(stage.connection.getMetaData().getDatabaseProductName()));
    clockGuard();
  }

  private Measurement measure() throws SQLException, IOException {
    guard(); actualObjects();
    long startedMono = System.nanoTime(); String started = time(Instant.now());
    Map<String, Object> db; Map<String, Object> session;
    try (PreparedStatement statement = statement(SESSION_SQL); ResultSet row = statement.executeQuery()) {
      clockGuard(); require(row.next());
      db = map("database_name", row.getString(1), "database_oid", row.getString(2), "system_identifier", row.getString(3),
          "engine_schema_name", "cibseven", "engine_schema_oid", row.getString(4),
          "native_schema_name", "maezo_native", "native_schema_oid", row.getString(5),
          "current_user", row.getString(6), "session_user", row.getString(7),
          "witness_projection", "provider-native-physical-db-join.v2");
      session = map("backend_pid", row.getString(8), "backend_start", sqlTime(row, 9),
          "backend_ssl", row.getBoolean(10), "backend_tls_version", row.getString(11),
          "transaction_id_if_assigned", row.getString(12), "transaction_started_at", sqlTime(row, 13),
          "observed_at", sqlTime(row, 14), "transaction_id_source", "pg_current_xact_id_if_assigned",
          "connection_source", "actual-commandcontext-dbsqlsession-connection", "context_connection_identity", "enlisted-object-identity-checked",
          "auto_commit", stage.connection.getAutoCommit(), "transaction_isolation", "READ_COMMITTED",
          "transaction_started_at_source", "postgresql-transaction_timestamp-on-enlisted-connection",
          "observed_at_source", "postgresql-clock_timestamp-on-enlisted-connection");
      require(!row.next()); clockGuard();
    }
    db.put("catalogue_projection_sha256", catalogue());
    NativeMeasurementConfiguration.validate("StableDatabaseProjection", db);
    NativeMeasurementConfiguration.validate("SessionTransactionWitness", session);
    require(db.equals(Codec.object(Codec.object(admission.get("expected_database")).get("stable_projection"))));
    require(!parseTime(Codec.string(session, "backend_start")).isAfter(parseTime(Codec.string(session, "transaction_started_at")))
        && !parseTime(Codec.string(session, "transaction_started_at")).isAfter(parseTime(Codec.string(session, "observed_at")))
        && parseTime(Codec.string(session, "observed_at")).isBefore(Instant.parse(config.originalDeadline())));
    Map<String, Object> install = installation();
    Map<String, Object> liveOrigin = origin(), liveToolchain = toolchain();
    actualObjects(); guard(); String finished = time(Instant.now()); long elapsed = System.nanoTime() - startedMono;
    require(elapsed >= 0 && !parseTime(started).isAfter(parseTime(finished))
        && Math.abs(Duration.between(parseTime(started), parseTime(finished)).toNanos() - elapsed) <= 1000000L);
    Map<String, Object> document = map("source", SOURCE, "started_at", started, "finished_at", finished,
        "monotonic_elapsed_ns", Long.toString(elapsed), "ctx_reference_ordinal", (long) stage.contextOrdinal,
        "connection_reference_ordinal", (long) stage.connectionOrdinal, "same_actual_context", true,
        "same_actual_enlisted_connection", true, "same_registry_engine_config", true,
        "stable_database_projection", db, "runtime_installation_projection", install,
        "session_transaction", session, "jvm_toolchain", liveToolchain, "instrument_origin", liveOrigin);
    NativeMeasurementConfiguration.validate("DirectMeasurement", document);
    return new Measurement(Codec.object(Codec.parse(Codec.encode(document))), db, session, install, liveOrigin, liveToolchain);
  }

  private PreparedStatement statement(String sql) throws SQLException {
    clockGuard(); actualObjects(); PreparedStatement statement = stage.connection.prepareStatement(sql);
    boolean transferred = false; Throwable failed = null;
    try { statement.setQueryTimeout(budget.querySeconds()); clockGuard(); transferred = true; return statement; }
    catch (SQLException | RuntimeException | Error failure) { failed = failure; throw failure; }
    finally { if (!transferred) closeUntransferred(statement, failed); }
  }
  private String catalogue() throws SQLException {
    List<Object> schemas = new ArrayList<>(), relations = new ArrayList<>();
    try (PreparedStatement statement = statement(SCHEMAS_SQL); ResultSet row = statement.executeQuery()) {
      while (row.next()) { clockGuard(); require(schemas.size() < 2);
        schemas.add(map("name", row.getString(1), "oid", row.getString(2), "owner", row.getString(3))); }
      clockGuard();
    }
    require(schemas.size() == 2);
    try (PreparedStatement statement = statement(ACT_SQL); ResultSet row = statement.executeQuery()) {
      while (row.next()) { clockGuard(); require(relations.size() < 3);
        relations.add(map("schema", row.getString(1), "name", row.getString(2), "oid", row.getString(3),
            "owner", row.getString(4), "kind", row.getString(5), "rls", row.getBoolean(6),
            "force_rls", row.getBoolean(7), "acl", row.getString(8))); }
      clockGuard();
    }
    require(relations.size() == 3);
    for (Object value : relations) {
      Map<String, Object> row = Codec.object(value);
      require("r".equals(row.get("kind")) && Boolean.FALSE.equals(row.get("rls")) && Boolean.FALSE.equals(row.get("force_rls")));
      try (PreparedStatement statement = statement("SELECT to_regclass(?)::oid::text")) {
        statement.setString(1, Codec.string(row, "name"));
        try (ResultSet resolved = statement.executeQuery()) {
          require(resolved.next() && row.get("oid").equals(resolved.getString(1)) && !resolved.next()); clockGuard();
        }
      }
    }
    String prefix = stage.configuration.getDatabaseTablePrefix();
    require(prefix != null && (prefix.isEmpty() || prefix.equals("cibseven.")));
    return sha(Codec.encode(map("schema", "provider-native-act-catalogue.v1", "schemas", schemas, "relations", relations)));
  }

  private Map<String, Object> installation() throws IOException {
    guard();
    Map<String, Object> pins = new TreeMap<>();
    Map<String, String> names = Map.of("descriptor_sha256", "conf/bpm-platform.xml", "native_jar_sha256", "lib/maezo-human-command.jar",
        "support_jar_sha256", "lib/provider-auth-test-support.jar", "rest_spi_jar_sha256", "webapps/engine-rest/WEB-INF/lib/maezo-rest-spi.jar");
    for (var pair : names.entrySet()) {
      Path path = base.resolve(pair.getValue()); String expected = Codec.string(admission, pair.getKey());
      require(expected.equals(expectedFiles.get(path.toString())));
      pins.put(pair.getKey(), sha(readFile(path, expected, FILE_BOUND)));
    }
    String variant = Codec.string(admission, "startup_variant"); require(Set.of("phase-a", "phase-b").contains(variant));
    Path mainJar = base.resolve("lib/maezo-human-command.jar"), supportJar = base.resolve("lib/provider-auth-test-support.jar");
    byte[] vendorRaw = jarEntry(mainJar, "provider-native/" + variant + "/secured-startup-vendor.sha256", 1048576);
    byte[] descriptors = jarEntry(supportJar, "provider-native/" + variant + "/secured-startup-descriptors.sha256", 1048576);
    pins.put("startup_vendor_inventory_sha256", sha(vendorRaw));
    pins.put("startup_descriptor_inventory_sha256", sha(descriptors));
    require(pins.get("startup_vendor_inventory_sha256").equals(admission.get("startup_vendor_inventory_sha256"))
        && pins.get("startup_descriptor_inventory_sha256").equals(admission.get("startup_descriptor_inventory_sha256")));
    byte[] observationSchema = classResource(RuntimeObservationAdmission.class, "provider-native-observation-schema-v1.json", mainJar, 1048576);
    pins.put("observation_schema_sha256", sha(observationSchema));
    require(pins.get("observation_schema_sha256").equals(config.observationSchemaSha256()));
    byte[] protocolSchema = classResource(NativeMeasurementConfiguration.class,
        "provider-native-independent-measurement-schema-v1.json", mainJar, 1048576);
    require(sha(protocolSchema).equals(config.protocolSchemaSha256()));
    Map<String, String> vendor = inventory(vendorRaw);
    verifyInventory(vendor); verifyInventory(inventory(descriptors));
    List<Object> origins = new ArrayList<>();
    Class<?> auth;
    try { auth = Class.forName("br.com.maezo.human.AuthValues", false, getClass().getClassLoader()); }
    catch (ClassNotFoundException failure) { throw unavailable(failure); }
    List<Class<?>> types = List.of(WorkloadPlugin.class, ProcessEngineImpl.class, RuntimeDefinitionObservation.class, auth);
    for (Class<?> type : types.stream().sorted(java.util.Comparator.comparing(Class::getName)).toList()) {
      require(type.getClassLoader() == getClass().getClassLoader());
      Path jar = codeSource(type); require(jar.getParent().equals(base.resolve("lib")) && jar.getFileName().toString().matches("[A-Za-z0-9._-]+\\.jar"));
      String relative = base.relativize(jar).toString();
      String expected = jar.equals(mainJar) ? Codec.string(admission, "native_jar_sha256") : vendor.get(relative);
      require(expected != null && expected.equals(expectedFiles.get(jar.toString())));
      String digest = sha(readFile(jar, expected, FILE_BOUND));
      byte[] bytes = classResource(type, type.getName().replace('.', '/') + ".class", jar, FILE_BOUND);
      require(classMajor(bytes) <= 61);
      origins.add(map("class_name", type.getName(), "jar_relative_path", relative,
          "jar_sha256", digest, "class_sha256", sha(bytes), "loader_group", "engine-common"));
    }
    // VERSION is a compile-time constant in the provided dependency. Read the exact
    // public metadata field of the actual admitted loader, never the inlined pin.
    try { require("2.1.0".equals(org.cibseven.bpm.engine.ProcessEngine.class.getField("VERSION").get(null))); }
    catch (ReflectiveOperationException failure) { throw unavailable(failure); }
    var registry = ProcessEngines.getProcessEngines();
    Map<String, Object> process = map("boot_uuid", RuntimeObservationAdmission.BOOT, "pid_namespace", ProcessHandle.current().pid(),
        "start_time_epoch_ms", ManagementFactory.getRuntimeMXBean().getStartTime(),
        "engine_registry_size", (long) registry.size(), "engine_registry_name", stage.configuration.getProcessEngineName());
    Map<String, Object> result = map("engine_name", stage.configuration.getProcessEngineName(),
        "authorization_enabled", stage.configuration.isAuthorizationEnabled(), "tenant_check_enabled", stage.configuration.isTenantCheckEnabled(),
        "schema_update", stage.configuration.getDatabaseSchemaUpdate(), "jvm_process", process,
        "class_provenance", origins, "mounted_pins", pins);
    NativeMeasurementConfiguration.validate("RuntimeInstallationProjection", result); guard(); return result;
  }

  private Map<String, Object> origin() throws IOException {
    clockGuard();
    Path jar = base.resolve("lib/provider-auth-test-support.jar");
    require(codeSource(getClass()).equals(jar) && getClass().getClassLoader() == NativeMeasurementPort.class.getClassLoader());
    byte[] jarBytes = readFile(jar, config.supportJarSha256(), FILE_BOUND);
    byte[] bytes = classResource(getClass(), INSTRUMENT.replace('.', '/') + ".class", jar, FILE_BOUND);
    require(sha(bytes).equals(config.instrumentClassSha256()) && classMajor(bytes) == 61);
    Map<String, Object> origin = map("source", SOURCE, "instrument_class", getClass().getName(),
        "jar_relative_path", base.relativize(jar).toString(), "jar_sha256", sha(jarBytes), "class_sha256", sha(bytes),
        "loader_relation", "same-actual-engine-common-loader", "major_class_version", (long) classMajor(bytes));
    NativeMeasurementConfiguration.validate("InstrumentOrigin", origin); clockGuard(); return origin;
  }
  private Map<String, Object> toolchain() {
    clockGuard(); var bean = ManagementFactory.getRuntimeMXBean();
    Map<String, Object> result = map("source", "live-jvm-management-and-class-readback",
        "java_runtime_version", Runtime.version().toString(), "java_vm_name", bean.getVmName(),
        "java_vm_vendor", bean.getVmVendor(), "java_vm_version", bean.getVmVersion(),
        "runtime_feature", (long) Runtime.version().feature(), "process_pid_namespace", ProcessHandle.current().pid(),
        "runtime_mxbean_start_epoch_ms", bean.getStartTime(), "boot_uuid", RuntimeObservationAdmission.BOOT, "class_major", 61L);
    // Runtime.version is acquired directly; preserve its raw printable version without rewriting.
    require(Objects.equals(System.getProperty("java.runtime.version"), Runtime.version().toString()));
    NativeMeasurementConfiguration.validate("JvmToolchain", result); clockGuard(); return result;
  }
  private void verifyInventory(Map<String, String> entries) throws IOException {
    // Boundary pins are a CLOSED OVERLAY: an entry the policy additionally pins must
    // agree, but the boundary is not required to pin every inventoried file (the
    // policy's custody reader budgets 1MiB for non-own files). Every entry is still
    // digest-verified against the live tree through the 32MiB bound below.
    for (var entry : entries.entrySet()) {
      Path path = base.resolve(entry.getKey()); require(path.startsWith(base));
      String pinned = expectedFiles.get(path.toString());
      require(pinned == null || pinned.equals(entry.getValue()));
      readFile(path, entry.getValue(), FILE_BOUND);
    }
  }
  private static Map<String, String> inventory(byte[] raw) {
    String text = new String(raw, StandardCharsets.US_ASCII); require(Arrays.equals(raw, text.getBytes(StandardCharsets.US_ASCII)));
    require(text.endsWith("\n") && !text.contains("\r")); Map<String, String> result = new LinkedHashMap<>();
    for (String line : text.substring(0, text.length() - 1).split("\n", -1)) {
      require(line.matches("[a-f0-9]{64} [A-Za-z0-9_./$@-]+") && !line.substring(65).contains(".."));
      String name = line.substring(65); Path path = Path.of(name);
      require(!path.isAbsolute() && path.toString().equals(name) && path.equals(path.normalize())
          && !name.contains("//") && result.put(name, line.substring(0, 64)) == null && result.size() <= 20000);
    }
    require(!result.isEmpty()); return result;
  }
  private Path codeSource(Class<?> type) throws IOException {
    try { require(type.getProtectionDomain().getCodeSource() != null); return canonicalFile(Path.of(type.getProtectionDomain().getCodeSource().getLocation().toURI())); }
    catch (java.net.URISyntaxException failure) { throw unavailable(failure); }
  }
  private byte[] classResource(Class<?> type, String name, Path jar, int bound) throws IOException {
    clockGuard(); require(type.getClassLoader() == getClass().getClassLoader());
    var urls = type.getClassLoader().getResources(name); require(urls.hasMoreElements());
    var resource = urls.nextElement(); require(!urls.hasMoreElements());
    require(resource.toExternalForm().equals("jar:" + jar.toUri().toURL().toExternalForm() + "!/" + name));
    byte[] expected = jarEntry(jar, name, bound), actual;
    try (var in = resource.openStream()) { actual = in.readNBytes(bound + 1); clockGuard(); }
    clockGuard(); require(actual.length > 0 && actual.length <= bound && Arrays.equals(actual, expected)); return actual;
  }
  private byte[] jarEntry(Path path, String name, int bound) throws IOException {
    clockGuard(); String expected = path.equals(base.resolve("lib/provider-auth-test-support.jar")) ? config.supportJarSha256()
        : expectedFiles == null ? Codec.string(admission, "native_jar_sha256") : expectedFiles.get(path.toString());
    require(expected != null);
    // Decode only bytes acquired through our owned FD. Reopening a JarFile by
    // pathname would separate entry acquisition from that descriptor custody.
    byte[] archive = readFile(path, expected, FILE_BOUND), result = null;
    Set<String> names = new HashSet<>();
    try (var jar = new java.util.zip.ZipInputStream(new java.io.ByteArrayInputStream(archive))) {
      java.util.zip.ZipEntry entry;
      while ((entry = jar.getNextEntry()) != null) {
        clockGuard(); require(names.add(entry.getName()) && names.size() <= 20000);
        if (entry.getName().equals(name)) {
          require(!entry.isDirectory() && result == null);
          var own = new java.io.ByteArrayOutputStream(); byte[] block = new byte[8192]; int n;
          while ((n = jar.read(block)) != -1) {
            clockGuard(); require(own.size() + (long) n <= bound); own.write(block, 0, n); clockGuard();
          }
          result = own.toByteArray(); require(result.length > 0);
        } else {
          // closeEntry implicitly drains; explicit bounded-buffer reads let the
          // original paired deadline guard every decompression step instead.
          byte[] block = new byte[8192];
          while (jar.read(block) != -1) { clockGuard(); }
        }
        jar.closeEntry(); clockGuard();
      }
    }
    require(result != null); readFile(path, expected, FILE_BOUND); clockGuard(); return result;
  }
  private static int classMajor(byte[] bytes) {
    require(bytes.length >= 8 && ByteBuffer.wrap(bytes).getInt() == 0xcafebabe); return Short.toUnsignedInt(ByteBuffer.wrap(bytes, 6, 2).getShort());
  }

  /** Own descriptor acquisition: before/after inode custody plus two position challenges. */
  private byte[] readFile(Path path, String expected, int bound) throws IOException {
    clockGuard(); require(expected != null && expected.matches("[a-f0-9]{64}") && bound > 0 && bound <= FILE_BOUND);
    canonicalFile(path); Map<String, Object> before = fileAttributes(path), parent = fileAttributes(path.getParent());
    long mode = number(before.get("mode")), owner = number(before.get("uid"));
    require((mode & 0170000) == 0100000 && (mode & 0022) == 0 && (mode & 0111) == 0
        && (owner == 0 || owner == config.jvmUid()) && number(before.get("nlink")) == 1
        && number(before.get("size")) > 0 && number(before.get("size")) <= bound);
    checkParents(path.getParent());
    Map<Path, Map<String, Object>> old = descriptorCensus(); Path descriptor = null; byte[] bytes;
    try (FileChannel channel = FileChannel.open(path, StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS)) {
      clockGuard(); Map<Path, Map<String, Object>> fresh = descriptorCensus();
      List<Path> matches = fresh.keySet().stream().filter(p -> !old.containsKey(p) && fresh.get(p).equals(before)).toList();
      // Concurrent same-inode opens (classloader reading the very jar under
      // verification) are legitimate JDK activity; attribution is by FD-POSITION
      // CHALLENGE, not census uniqueness. The challenge offset is end-of-file,
      // which no parallel sequential reader holds at the probe instant.
      require(!matches.isEmpty());
      channel.position(Math.max(1, channel.size() - 1));
      for (Path candidate : matches) {
        try { if (descriptorPosition(candidate) == channel.position()) { require(descriptor == null); descriptor = candidate; } }
        catch (IOException raced) { /* candidate closed between census and probe */ }
      }
      require(descriptor != null);
      channel.position(0);
      require(before.equals(fileAttributes(descriptor, true)) && channel.size() == number(before.get("size")));
      require(channel.position() == 0 && descriptorPosition(descriptor) == 0);
      channel.position(1); require(descriptorPosition(descriptor) == 1);
      channel.position(0); require(descriptorPosition(descriptor) == 0);
      ByteBuffer buffer = ByteBuffer.allocate((int) number(before.get("size")) + 1);
      while (buffer.hasRemaining()) { clockGuard(); int n = channel.read(buffer); clockGuard(); if (n == -1) break; }
      bytes = Arrays.copyOf(buffer.array(), buffer.position());
      require(bytes.length == number(before.get("size")) && channel.size() == bytes.length
          && channel.position() == descriptorPosition(descriptor) && before.equals(fileAttributes(descriptor, true))
          && before.equals(fileAttributes(path)) && parent.equals(fileAttributes(path.getParent())) && sha(bytes).equals(expected));
      clockGuard();
    }
    clockGuard(); require(descriptor != null && !Files.exists(descriptor)
        && before.equals(fileAttributes(path)) && parent.equals(fileAttributes(path.getParent())));
    return bytes;
  }
  private Map<Path, Map<String, Object>> descriptorCensus() throws IOException {
    clockGuard(); Map<Path, Map<String, Object>> result = new TreeMap<>();
    try (DirectoryStream<Path> stream = Files.newDirectoryStream(FD_ROOT)) {
      for (Path path : stream) {
        clockGuard(); require(path.getFileName().toString().matches("[0-9]+") && result.size() < 4096);
        try {
          var attributes = fileAttributes(path, true);
          // The census's own DirectoryStream FD must never be mistaken for a
          // newly opened regular material when the kernel reuses its number.
          if ((number(attributes.get("mode")) & 0170000) == 0100000) result.put(path, attributes);
        }
        catch (java.nio.file.NoSuchFileException vanished) { /* enumeration FD can close itself; never a qualifying candidate */ }
      }
    }
    clockGuard(); return result;
  }
  private long descriptorPosition(Path descriptor) throws IOException {
    require(descriptor.getParent().equals(FD_ROOT) && descriptor.getFileName().toString().matches("[0-9]+"));
    Path info = Path.of("/proc/self/fdinfo", descriptor.getFileName().toString());
    clockGuard(); String raw = Files.readString(info, StandardCharsets.US_ASCII); clockGuard(); require(raw.length() <= 4096);
    var match = java.util.regex.Pattern.compile("(?m)^pos:\\s*([0-9]+)$").matcher(raw); require(match.find());
    long result = Long.parseLong(match.group(1)); require(!match.find()); return result;
  }
  private static Map<String, Object> fileAttributes(Path path) throws IOException { return fileAttributes(path, false); }
  private static Map<String, Object> fileAttributes(Path path, boolean procDescriptor) throws IOException {
    return Files.readAttributes(path, FD_ATTRIBUTES, procDescriptor ? new LinkOption[0] : new LinkOption[]{LinkOption.NOFOLLOW_LINKS});
  }
  private void checkParents(Path path) throws IOException {
    for (Path parent = path; parent != null; parent = parent.getParent()) {
      clockGuard(); var attributes = fileAttributes(parent);
      require((number(attributes.get("mode")) & 0170000) == 0040000
          && (number(attributes.get("mode")) & 0022) == 0
          && (number(attributes.get("uid")) == 0 || number(attributes.get("uid")) == config.jvmUid()));
    }
  }
  private static Path canonicalFile(Path path) throws IOException {
    require(path.isAbsolute() && path.equals(path.normalize()) && !path.toString().contains("//") && path.equals(path.toRealPath()));
    for (Path parent = path; parent != null; parent = parent.getParent()) require(!Files.isSymbolicLink(parent)); return path;
  }
  private static Path canonicalDirectory(Path path) throws IOException { canonicalFile(path); require(Files.isDirectory(path)); return path; }

  private SocketIdentity socketIdentity(String value) throws IOException {
    clockGuard(); Path path = canonicalFile(Path.of(value));
    require(value.getBytes(StandardCharsets.US_ASCII).length <= 90 && value.matches("[ -~]+"));
    var parent = fileAttributes(path.getParent()); var socket = fileAttributes(path);
    require(number(parent.get("mode")) == 0040750 && number(parent.get("uid")) == config.rootPeerUid()
        && number(parent.get("gid")) == config.rootPeerGid() && number(socket.get("mode")) == 0140660
        && number(socket.get("uid")) == config.rootPeerUid() && number(socket.get("gid")) == config.rootPeerGid());
    require(Files.getOwner(path).getName().equals(config.rootPeerUser())
        && Files.readAttributes(path, java.nio.file.attribute.PosixFileAttributes.class).group().getName().equals(config.rootPeerGroup()));
    var ancestors = path.getParent().getParent();
    for (Path p = ancestors; p != null; p = p.getParent()) {
      var a = fileAttributes(p); require((number(a.get("mode")) & 0170000) == 0040000
          && (number(a.get("mode")) & 0022) == 0 && number(a.get("uid")) == 0);
    }
    clockGuard(); return new SocketIdentity(path, number(socket.get("dev")), number(socket.get("ino")),
        number(parent.get("dev")), number(parent.get("ino")));
  }
  private SocketChannel connect(SocketIdentity identity) throws IOException {
    guard(); SocketChannel channel = SocketChannel.open(StandardProtocolFamily.UNIX);
    boolean transferred = false; Throwable failed = null;
    try {
      channel.configureBlocking(false); guard();
      boolean connected = channel.connect(UnixDomainSocketAddress.of(identity.path));
      while (!connected) { waitFor(channel, SelectionKey.OP_CONNECT); connected = channel.finishConnect(); guard(); }
      socketGuard(channel, identity); guard(); transferred = true; return channel;
    } catch (IOException | RuntimeException | Error failure) { failed = failure; throw failure; }
    finally { if (!transferred) closeUntransferred(channel, failed); }
  }
  private void socketGuard(SocketChannel channel, SocketIdentity identity) throws IOException {
    guard(); require(channel.isOpen() && channel.isConnected() && identity.equals(socketIdentity(identity.path.toString())));
    require(channel.getRemoteAddress().equals(UnixDomainSocketAddress.of(identity.path)));
    UnixDomainPrincipal peer = channel.getOption(ExtendedSocketOptions.SO_PEERCRED);
    require(peer != null && peer.user().getName().equals(config.rootPeerUser()) && peer.group().getName().equals(config.rootPeerGroup()));
    // Java's public SO_PEERCRED returns principals only. ROOT supplies the kernel PID independently.
    guard();
  }
  private void waitFor(SocketChannel channel, int operation) throws IOException {
    guard(); require(selector != null && selector.isOpen());
    SelectionKey key = channel.keyFor(selector);
    if (key == null) key = channel.register(selector, operation); else key.interestOps(operation);
    long remaining = budget.remainingMillis(); require(remaining > 0);
    selector.select(Math.min(remaining, 1000L));
    guard(); require(key.isValid()); selector.selectedKeys().clear();
  }
  private byte[] emit(String kind, Map<String, Object> extra) throws IOException {
    guard(); require(ordinal >= 1 && ordinal <= 65);
    Map<String, Object> frame = map("schema", FRAME, "event_ordinal", (long) ordinal,
        "session_nonce", config.sessionNonce(), "request_sha256", config.requestSha256(),
        "config_sha256", config.digest(), "emitted_at", time(Instant.now()), "original_deadline", config.originalDeadline(), "kind", kind);
    for (var value : extra.entrySet()) require(frame.put(value.getKey(), value.getValue()) == null);
    NativeMeasurementConfiguration.validate(kind, frame);
    byte[] raw = Codec.encode(frame); writeFrame(primary, primaryIdentity, raw); ordinal++; guard(); return raw;
  }
  private void writeFrame(SocketChannel channel, SocketIdentity identity, byte[] raw) throws IOException {
    guard(); require(raw.length > 0 && raw.length <= 32768);
    totalBytes = Math.addExact(totalBytes, 4L + raw.length); require(totalBytes <= 2097152);
    ByteBuffer output = ByteBuffer.allocate(raw.length + 4).putInt(raw.length).put(raw); output.flip();
    while (output.hasRemaining()) {
      socketGuard(channel, identity); int n = channel.write(output); guard(); require(n >= 0);
      if (n == 0) waitFor(channel, SelectionKey.OP_WRITE);
    }
    socketGuard(channel, identity);
  }
  private byte[] receiveRelease() throws IOException {
    ByteBuffer header = ByteBuffer.allocate(4); readFully(header); header.flip(); int size = header.getInt();
    require(size > 0 && size <= 32768);
    totalBytes = Math.addExact(totalBytes, size + 4L); require(totalBytes <= 2097152);
    ByteBuffer bytes = ByteBuffer.allocate(size); readFully(bytes); return bytes.array();
  }
  private void readFully(ByteBuffer bytes) throws IOException {
    while (bytes.hasRemaining()) {
      socketGuard(primary, primaryIdentity); int n = primary.read(bytes); guard(); require(n >= 0);
      if (n == 0) waitFor(primary, SelectionKey.OP_READ);
    }
    socketGuard(primary, primaryIdentity);
  }
  private Map<String, Object> endpointFields(Map<String, Object> fields) {
    Map<String, Object> values = map("stage_sequence", (long) stage.sequence,
        "server_stage_execution_uuid", stage.execution, "endpoint", stage.endpoint.name()); values.putAll(fields); return values;
  }
  private static void stableEndpoint(Measurement first, Measurement second) {
    stableStage(first, second);
    for (String field : List.of("backend_pid", "backend_start", "transaction_id_if_assigned", "transaction_started_at", "backend_ssl", "backend_tls_version"))
      require(Objects.equals(first.session.get(field), second.session.get(field)));
    require(!parseTime(Codec.string(first.session, "observed_at")).isAfter(parseTime(Codec.string(second.session, "observed_at"))));
  }
  private static void stableStage(Measurement first, Measurement second) {
    stableInstallation(first, second);
    for (String field : List.of("backend_pid", "backend_start", "transaction_started_at", "backend_ssl", "backend_tls_version"))
      require(Objects.equals(first.session.get(field), second.session.get(field)));
    Object old = first.session.get("transaction_id_if_assigned"), fresh = second.session.get("transaction_id_if_assigned");
    require(old == null || old.equals(fresh));
  }
  private static void stableInstallation(Measurement first, Measurement second) {
    require(first.database.equals(second.database) && first.installation.equals(second.installation)
        && first.origin.equals(second.origin) && first.toolchain.equals(second.toolchain));
  }
  private static String sqlTime(ResultSet row, int column) throws SQLException {
    java.time.OffsetDateTime value = row.getObject(column, java.time.OffsetDateTime.class); require(value != null); return time(value.toInstant());
  }
  private static String time(Instant value) { return TIME.format(value); }
  private static Instant parseTime(String value) { require(value.matches("[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\\.[0-9]{6}Z")); return Instant.parse(value); }
  private static long number(Object value) { require(value instanceof Number); return ((Number) value).longValue(); }
  private static String requiredEnvironment(String name) { String result = System.getenv(name); require(result != null && !result.isBlank()); return result; }
  private static String requiredProperty(String name) { String result = System.getProperty(name); require(result != null && !result.isBlank()); return result; }
  private static String sha(byte[] raw) {
    try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(raw)); }
    catch (java.security.NoSuchAlgorithmException failure) { throw new IllegalStateException(failure); }
  }
  private static Map<String, Object> map(Object... pairs) {
    require(pairs.length % 2 == 0); Map<String, Object> result = new TreeMap<>();
    for (int i = 0; i < pairs.length; i += 2) { require(pairs[i] instanceof String && !result.containsKey(pairs[i])); result.put((String) pairs[i], pairs[i + 1]); }
    return result;
  }
  private static final class Stage {
    private final int sequence, contextOrdinal, connectionOrdinal;
    private final String execution;
    private final CommandContext context;
    private final Connection connection;
    private ProcessEngineConfigurationImpl configuration;
    private Endpoint endpoint;
    private boolean committed;
    private String committedAt;
    private Measurement entry, committing;
    private Stage(int sequence, String execution, CommandContext context, Connection connection, int ctxOrdinal, int connectionOrdinal) {
      this.sequence = sequence; this.execution = execution; this.context = context; this.connection = connection;
      contextOrdinal = ctxOrdinal; this.connectionOrdinal = connectionOrdinal;
    }
  }
  private record Measurement(Map<String, Object> document, Map<String, Object> database,
      Map<String, Object> session, Map<String, Object> installation, Map<String, Object> origin, Map<String, Object> toolchain) {}
  private record SocketIdentity(Path path, long device, long inode, long parentDevice, long parentInode) {}
  private record FinalOutcome(String nonce, String configSha, String requestSha, String responseSha,
      String terminalSha, String closedSha, String boot, String classSha, String candidate, int count,
      String httpClosedAt, String udsClosedAt, String guardedAt, String sealedAt, String deadline) {
    private Map<String, Object> document() {
      return map("schema", "provider-native-independent-measurement-final-outcome.v2",
          "source", "pinned-instrument-sealed-primary-session-outcome", "session_nonce", nonce,
          "config_sha256", configSha, "request_sha256", requestSha, "response_sha256", responseSha,
          "terminal_raw_sha256", terminalSha, "last_stage_closed_raw_sha256", closedSha, "boot_uuid", boot,
          "instrument_class_sha256", classSha, "candidate_sha", candidate, "stages_completed", (long) count,
          "primary_http_closed_at", httpClosedAt, "primary_uds_closed_at", udsClosedAt,
          "primary_postclose_guard_at", guardedAt, "sealed_at", sealedAt, "original_deadline", deadline,
          "result", "PRIMARY_COMPLETE_AS_OF_SEAL",
          "certainty", "TRUSTED_JVM_PRIMARY_OPERATION_AS_OF_ONLY_NO_FUTURE_READBACK_CLEANUP_CLAIM",
          "primary_channel_id", "PRIMARY_RENDEZVOUS", "outcome_channel_id", "SEALED_PRIMARY_OUTCOME_READBACK");
    }
  }
  private static final class Budget {
    private final long initialWall = System.currentTimeMillis(), initialNano = System.nanoTime();
    private final long deadlineWall, allowance;
    private long lastWall = initialWall, lastNano = initialNano;
    private Budget(String issuedValue, String deadlineValue) {
      Instant issued = parseTime(issuedValue), deadline = parseTime(deadlineValue);
      require(!issued.isAfter(Instant.ofEpochMilli(initialWall)) && issued.isBefore(deadline)
          && Duration.between(issued, deadline).compareTo(Duration.ofMinutes(15)) <= 0);
      deadlineWall = deadline.toEpochMilli();
      allowance = Math.multiplyExact(deadlineWall - initialWall, 1000000L); require(allowance > 0); check();
    }
    private void check() {
      long wall = System.currentTimeMillis(), nano = System.nanoTime();
      require(!Thread.currentThread().isInterrupted() && wall >= lastWall && nano >= lastNano
          && wall < deadlineWall && nano - initialNano < allowance
          && Math.abs((wall - initialWall) - (nano - initialNano) / 1000000L) <= 5000);
      lastWall = wall; lastNano = nano;
    }
    private long remainingMillis() { check(); return Math.min(deadlineWall - System.currentTimeMillis(), (allowance - (System.nanoTime() - initialNano)) / 1000000L); }
    private int querySeconds() { long value = remainingMillis(); require(value > 0); return (int) Math.max(1, Math.min(900, (value + 999) / 1000)); }
  }

  /** Strict closed transport codec, independent of producer Json/Jcs and measurement maps. */
  private static final class Codec {
    private final String input;
    private int offset;
    private Codec(String input) { this.input = input; }
    private static Object canonicalParse(byte[] bytes) { Object value = parse(bytes); require(Arrays.equals(bytes, encode(value))); return value; }
    private static Object parse(byte[] bytes) {
      require(bytes.length > 0 && bytes.length <= FILE_BOUND);
      try {
        String text = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString();
        Codec parser = new Codec(text); Object result = parser.value(0); parser.whitespace(); require(parser.offset == text.length()); return result;
      } catch (java.nio.charset.CharacterCodingException failure) { throw unavailable(failure); }
    }
    private Object value(int depth) {
      require(depth <= 32); whitespace(); require(offset < input.length()); char first = input.charAt(offset);
      if (first == '{') return objectValue(depth + 1);
      if (first == '[') return arrayValue(depth + 1);
      if (first == '"') return stringValue();
      if (input.startsWith("true", offset)) { offset += 4; return Boolean.TRUE; }
      if (input.startsWith("false", offset)) { offset += 5; return Boolean.FALSE; }
      if (input.startsWith("null", offset)) { offset += 4; return null; }
      int start = offset; if (first == '-') offset++;
      require(offset < input.length() && input.charAt(offset) >= '0' && input.charAt(offset) <= '9');
      if (input.charAt(offset) == '0') offset++; else while (offset < input.length() && input.charAt(offset) >= '0' && input.charAt(offset) <= '9') offset++;
      String numeric = input.substring(start, offset); require(!numeric.equals("-0"));
      try { return Long.parseLong(numeric); } catch (NumberFormatException failure) { throw unavailable(failure); }
    }
    private Map<String, Object> objectValue(int depth) {
      offset++; Map<String, Object> result = new TreeMap<>(); whitespace(); if (take('}')) return result;
      do { whitespace(); require(offset < input.length() && input.charAt(offset) == '"'); String name = stringValue();
        require(!result.containsKey(name)); whitespace(); require(take(':')); result.put(name, value(depth)); whitespace();
        require(result.size() <= 20000); if (take('}')) return result;
      } while (take(',')); throw new IllegalStateException("Object framing refused");
    }
    private List<Object> arrayValue(int depth) {
      offset++; List<Object> result = new ArrayList<>(); whitespace(); if (take(']')) return result;
      do { result.add(value(depth)); whitespace(); require(result.size() <= 20000); if (take(']')) return result;
      } while (take(',')); throw new IllegalStateException("Array framing refused");
    }
    private String stringValue() {
      require(take('"')); StringBuilder result = new StringBuilder();
      while (offset < input.length()) {
        char c = input.charAt(offset++); if (c == '"') { String value = result.toString(); validSurrogates(value); return value; }
        require(c >= 32);
        if (c == '\\') {
          require(offset < input.length()); char escaped = input.charAt(offset++);
          c = switch (escaped) {
            case '"' -> '"'; case '\\' -> '\\'; case '/' -> '/'; case 'b' -> '\b'; case 'f' -> '\f'; case 'n' -> '\n'; case 'r' -> '\r'; case 't' -> '\t';
            case 'u' -> { require(offset + 4 <= input.length()); String hex = input.substring(offset, offset + 4); require(hex.matches("[a-fA-F0-9]{4}")); offset += 4; yield (char) Integer.parseInt(hex, 16); }
            default -> throw new IllegalStateException("String escape refused");
          };
        }
        result.append(c); require(result.length() <= FILE_BOUND);
      }
      throw new IllegalStateException("String EOF refused");
    }
    private boolean take(char c) { if (offset < input.length() && input.charAt(offset) == c) { offset++; return true; } return false; }
    private void whitespace() { while (offset < input.length() && " \t\r\n".indexOf(input.charAt(offset)) >= 0) offset++; }
    private static byte[] encode(Object value) { StringBuilder output = new StringBuilder(); encodeValue(value, output, 0); return output.toString().getBytes(StandardCharsets.US_ASCII); }
    private static void encodeValue(Object value, StringBuilder output, int depth) {
      require(depth <= 32);
      if (value == null) output.append("null");
      else if (value instanceof String text) string(text, output);
      else if (value instanceof Boolean bool) output.append(bool);
      else if (value instanceof Long || value instanceof Integer) output.append(value);
      else if (value instanceof Map<?, ?> source) {
        TreeMap<String, Object> sorted = new TreeMap<>();
        for (var entry : source.entrySet()) { require(entry.getKey() instanceof String); sorted.put((String) entry.getKey(), entry.getValue()); }
        output.append('{'); boolean comma = false;
        for (var entry : sorted.entrySet()) { if (comma) output.append(','); comma = true; string(entry.getKey(), output); output.append(':'); encodeValue(entry.getValue(), output, depth + 1); } output.append('}');
      } else if (value instanceof List<?> list) {
        output.append('['); boolean comma = false; for (Object item : list) { if (comma) output.append(','); comma = true; encodeValue(item, output, depth + 1); } output.append(']');
      } else throw new IllegalStateException("Non JSON primitive refused");
      require(output.length() <= FILE_BOUND);
    }
    private static void string(String text, StringBuilder output) {
      validSurrogates(text); output.append('"');
      for (int i = 0; i < text.length(); i++) {
        char c = text.charAt(i);
        switch (c) {
          case '"' -> output.append("\\\""); case '\\' -> output.append("\\\\"); case '\b' -> output.append("\\b");
          case '\f' -> output.append("\\f"); case '\n' -> output.append("\\n"); case '\r' -> output.append("\\r"); case '\t' -> output.append("\\t");
          default -> { if (c < 32 || c > 126) { output.append("\\u"); String hex = Integer.toHexString(c); output.append("0".repeat(4 - hex.length())).append(hex); } else output.append(c); }
        }
      }
      output.append('"');
    }
    private static void validSurrogates(String text) {
      for (int i = 0; i < text.length(); i++) { char c = text.charAt(i);
        if (Character.isHighSurrogate(c)) { require(i + 1 < text.length() && Character.isLowSurrogate(text.charAt(++i))); }
        else require(!Character.isLowSurrogate(c)); }
    }
    @SuppressWarnings("unchecked") private static Map<String, Object> object(Object value) { require(value instanceof Map<?, ?>); return (Map<String, Object>) value; }
    @SuppressWarnings("unchecked") private static List<Object> array(Object value) { require(value instanceof List<?>); return (List<Object>) value; }
    private static String string(Map<String, Object> value, String name) { require(value.get(name) instanceof String); return (String) value.get(name); }
    private static long integer(Map<String, Object> value, String name) { require(value.get(name) instanceof Long || value.get(name) instanceof Integer); return ((Number) value.get(name)).longValue(); }
  }
}
