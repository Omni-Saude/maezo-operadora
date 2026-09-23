package br.com.maezo.human.readprovider;

import static org.junit.jupiter.api.Assertions.*;

import br.com.maezo.human.Jcs;
import br.com.maezo.human.Rejected;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/**
 * The Java projection of a {@code portal_memberships} row (T1.7b) against the vector the Python
 * publisher path produced ({@code tests/fixtures/portal_read/jcs-membership-vector.json}, read also
 * by {@code tests/unit/portal/test_jcs_membership_vector.py}): same JCS bytes, same SHA-256, and the
 * same rows refused.
 */
class MembershipProjectionTest {
  static Map<String, Object> vector() throws Exception {
    String file = System.getProperty("maezo.jcs.vector");
    assertNotNull(file, "the pom passes the shared vector path (maezo.jcs.vector)");
    return Fields.object(SourceJson.parse(Files.readAllBytes(Path.of(file))));
  }

  static List<Map<String, Object>> cases(String key) throws Exception {
    List<Map<String, Object>> out = new ArrayList<>();
    for (Object o : Fields.list(vector().get(key))) out.add(Fields.object(o));
    return out;
  }

  static Map<String, Object> project(Map<String, Object> c) {
    return MembershipSourceObserver.projection((String) c.get("tenant"),
        SourceJson.parse(((String) c.get("row_payload")).getBytes(StandardCharsets.UTF_8)));
  }

  @Test
  void javaProjectionIsByteForByteThePythonOne() throws Exception {
    var accepted = cases("cases");
    assertTrue(accepted.size() >= 8, "the vector covers the accepted variants");
    for (var c : accepted) {
      byte[] raw = Jcs.canonical(project(c));
      assertEquals(c.get("projection_jcs"), new String(raw, StandardCharsets.UTF_8),
          (String) c.get("name"));
      assertEquals(c.get("projection_sha256"), Jcs.digest(raw), (String) c.get("name"));
      assertEquals(c.get("projection_sha256"), Fields.digest(project(c)), (String) c.get("name"));
    }
  }

  @Test
  void javaRefusesEveryRowPythonRefuses() throws Exception {
    var refused = cases("refused");
    assertTrue(refused.size() >= 10, "the vector covers the refused variants");
    for (var c : refused) {
      Rejected r = assertThrows(Rejected.class, () -> project(c), (String) c.get("name"));
      assertEquals(503, r.status);
    }
  }

  static final String ROW = "{\"tenant\":\"amh\",\"issuer\":\"https://issuer.example\","
      + "\"subject\":\"s-1\",\"principal_ref\":\"p-1\",\"revision\":3,\"audience\":\"staff\","
      + "\"memberships\":[{\"membership_ref\":\"m\",\"roles\":[\"r\"],\"groups\":[\"g\"]}],"
      + "\"subject_bindings\":[],\"reviewed_until\":\"2026-10-01T00:00:00Z\",\"revoked\":false}";

  static Map<String, Object> project(String row) {
    return MembershipSourceObserver.projection("amh",
        SourceJson.parse(row.getBytes(StandardCharsets.UTF_8)));
  }

  static void refused(String row) {
    Rejected r = assertThrows(Rejected.class, () -> project(row), row);
    assertEquals(503, r.status);
  }

  @Test
  void projectionCarriesExactlyTheWiredFields() {
    var p = project(ROW);
    assertEquals(Map.of("principal_ref", "p-1", "issuer", "https://issuer.example", "subject",
        "s-1", "membership_revision", "3", "audience", "staff", "memberships",
        List.of(Map.of("membership_ref", "m", "roles", List.of("r"), "groups", List.of("g"))),
        "subject_bindings", List.of(), "state", "active", "reviewed_until",
        "2026-10-01T00:00:00.000000Z"), p);
    assertEquals("revoked", project(ROW.replace("\"revoked\":false", "\"revoked\":true"))
        .get("state"));
  }

  @Test
  void refusesWhatTheEngineWouldRefuseOrThatIsNotAMembershipRecord() {
    for (String bad : List.of(ROW.replace("\"revoked\":false", "\"revoked\":\"false\""),
             ROW.replace("\"revoked\":false", "\"revoked\":null"),
             ROW.replace("\"revision\":3", "\"revision\":true"),
             ROW.replace("\"revision\":3", "\"revision\":9223372036854775807"),
             ROW.replace("\"subject\":\"s-1\"", "\"subject\":\"s 1\""),
             ROW.replace("\"principal_ref\":\"p-1\"", "\"principal_ref\":\"\""),
             ROW.replace("\"issuer\":\"https://issuer.example\"", "\"issuer\":\"\""),
             ROW.replace("\"issuer\":\"https://issuer.example\"", "\"issuer\":\"a\\u0007\""),
             ROW.replace("\"audience\":\"staff\"", "\"audience\":\"admin\""),
             ROW.replace("\"roles\":[\"r\"]", "\"roles\":[\"r\",\"r\"]"),
             ROW.replace("\"groups\":[\"g\"]", "\"groups\":[1]"),
             ROW.replace("\"roles\":[\"r\"],", "\"roles\":[\"r\"],\"x\":[],"),
             ROW.replace("}],\"subject_bindings\"",
                 "},{\"membership_ref\":\"m\",\"roles\":[\"r\"],\"groups\":[\"g\"]}],"
                     + "\"subject_bindings\""),
             ROW.replace("\"subject_bindings\":[]",
                 "\"subject_bindings\":[{\"kind\":\"other\",\"resource_ref\":\"x\"}]"),
             ROW.replace("\"subject_bindings\":[]", "\"subject_bindings\":{}"),
             ROW.replace(",\"revision\":3", ""),
             ROW.replace("2026-10-01T00:00:00Z", "2026-10-01T00:00:00.1234567Z"),
             ROW.replace("2026-10-01T00:00:00Z", "2026-10-01 00:00:00Z"),
             ROW.replace("2026-10-01T00:00:00Z", "2026-10-01T00:00:00+0300"),
             ROW.replace("2026-10-01T00:00:00Z", "2026-13-01T00:00:00Z"),
             ROW.replace("2026-10-01T00:00:00Z", "2026-10-01T00:00:00")))
      refused(bad);
    project(ROW); // the unbroken control
  }

  @Test
  void aStaffMayCarryAnyBindingButOthersOnlyTheirOwnKind() {
    String staff = ROW.replace("\"subject_bindings\":[]",
        "\"subject_bindings\":[{\"kind\":\"provider\",\"resource_ref\":\"x\"},"
            + "{\"kind\":\"beneficiary\",\"resource_ref\":\"y\"}]");
    assertEquals(2, ((List<?>) project(staff).get("subject_bindings")).size());
    refused(staff.replace("\"audience\":\"staff\"", "\"audience\":\"provider\""));
    project(staff.replace("\"audience\":\"staff\"", "\"audience\":\"provider\"")
                .replace(",{\"kind\":\"beneficiary\",\"resource_ref\":\"y\"}", ""));
  }
}
