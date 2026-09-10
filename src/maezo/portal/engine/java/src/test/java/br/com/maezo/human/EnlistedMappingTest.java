package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;
import java.lang.reflect.Proxy;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.*;
import org.apache.ibatis.exceptions.PersistenceException;
import org.apache.ibatis.executor.*;
import org.apache.ibatis.mapping.*;
import org.apache.ibatis.session.Configuration;
import org.apache.ibatis.session.defaults.DefaultSqlSession;
import org.apache.ibatis.transaction.jdbc.JdbcTransaction;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/** Independent real MyBatis kernel checks. Recording JDBC is not an integration database. */
class EnlistedMappingTest {
  @Test void mapperCannotCacheDmlAndInstallationIsStable() {
    var config = new Configuration();
    EnlistedWrites.install(config);
    var first = config.getMappedStatement(EnlistedWrites.ID);
    EnlistedWrites.install(config);
    assertSame(first, config.getMappedStatement(EnlistedWrites.ID));
    assertTrue(first.isDirtySelect());
    assertTrue(first.isFlushCacheRequired());
    assertFalse(first.isUseCache());
    assertEquals(StatementType.PREPARED, first.getStatementType());
    assertEquals(SqlCommandType.SELECT, first.getSqlCommandType());
  }

  @Test void valuesRemainBoundIncludingNullAndSqlLookingText() {
    var config = new Configuration();
    EnlistedWrites.install(config);
    String sql = "UPDATE synthetic SET value_=?,active_=?,missing_=? WHERE id_=?";
    Object[] args = {"'); DROP TABLE synthetic; --", true, null, 12L};
    var bound = config.getMappedStatement(EnlistedWrites.ID).getBoundSql(new EnlistedWrites.Write(sql, args));
    assertEquals(sql + " RETURNING 1", bound.getSql());
    assertEquals(4, bound.getParameterMappings().size());
    for (int i = 0; i < args.length; i++) {
      assertEquals("p" + i, bound.getParameterMappings().get(i).getProperty());
      assertEquals(args[i], bound.getAdditionalParameter("p" + i));
    }
  }

  @ParameterizedTest @ValueSource(booleans={false,true})
  void queryIsImmediateAndFailureStillMarksSessionForRollback(boolean batch) {
    var events = new ArrayList<String>();
    var connection = (Connection) Proxy.newProxyInstance(Connection.class.getClassLoader(),
        new Class<?>[]{Connection.class}, (proxy, method, args) -> {
          switch (method.getName()) {
            case "getAutoCommit": return false;
            case "prepareStatement": events.add("prepare:" + args[0]); throw new SQLException("EIR_RECORDING_JDBC_NO_DATABASE");
            case "setAutoCommit": events.add("setAutoCommit:" + args[0]); return null;
            case "commit": case "rollback": case "close": events.add(method.getName()); return null;
            case "toString": return "independent-recording-jdbc";
            default: throw new UnsupportedOperationException(method.getName());
          }
        });
    var config = new Configuration();
    EnlistedWrites.install(config);
    var tx = new JdbcTransaction(connection);
    Executor executor = batch ? new BatchExecutor(config, tx) : new SimpleExecutor(config, tx);
    var session = new DefaultSqlSession(config, executor, false);
    var write = new EnlistedWrites.Write("UPDATE synthetic SET value_=?", new Object[]{"bound"});
    var failure = assertThrows(PersistenceException.class, () -> session.selectList(EnlistedWrites.ID, write));
    assertTrue(failure.getMessage().contains("EIR_RECORDING_JDBC_NO_DATABASE"));
    assertEquals(List.of("prepare:UPDATE synthetic SET value_=? RETURNING 1"), events);
    session.rollback();
    assertEquals(List.of("prepare:UPDATE synthetic SET value_=? RETURNING 1", "rollback"), events);
    session.close();
    assertEquals(List.of("prepare:UPDATE synthetic SET value_=? RETURNING 1", "rollback", "setAutoCommit:true", "close"), events);
  }
}
