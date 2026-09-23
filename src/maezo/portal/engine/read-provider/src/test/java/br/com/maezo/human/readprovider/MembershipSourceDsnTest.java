package br.com.maezo.human.readprovider;

import static org.junit.jupiter.api.Assertions.*;

import br.com.maezo.human.Rejected;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** The observer's DSN file, its custody and the connection properties the provider fixes. */
class MembershipSourceDsnTest {
  @TempDir Path dir;

  static MembershipSourceObserver.Dsn dsn(String text) {
    return MembershipSourceObserver.dsn(text.getBytes(StandardCharsets.UTF_8));
  }

  static void refused(String text) {
    Rejected r = assertThrows(Rejected.class, () -> dsn(text), text);
    assertEquals(503, r.status);
  }

  @Test
  void parsesTheObserverDsn() {
    var d = dsn("postgresql://portal_read_source_amh:s%40cr%2Fet@db.internal:6432/maezo");
    assertEquals("portal_read_source_amh", d.login());
    assertEquals("s@cr/et", d.password());
    assertEquals("db.internal", d.host());
    assertEquals(6432, d.port());
    assertEquals("maezo", d.database());
    assertEquals(5432, dsn("postgresql://u:p@h/db\n").port());
    assertEquals("MembershipSourceDsn[redacted]", d.toString());
  }

  @Test
  void refusesEverythingElse() {
    for (String bad : List.of("", "postgresql://u@h/db", "postgresql://u:@h/db",
             "postgres://u:p@h/db", "postgresql+asyncpg://u:p@h/db", "postgresql://u:p@h/db?x=1",
             "postgresql://u:p@h/db?sslmode=disable", "postgresql://u:p@h:0/db",
             "postgresql://u:p@h:65536/db", "postgresql://U:p@h/db", "postgresql://u:p@h/",
             "postgresql://u:p@h..x/db", "postgresql://u:p@-h/db", "postgresql://u:p@[::1]/db",
             "postgresql://u:p@h/db\n\n", " postgresql://u:p@h/db", "postgresql://u:p@h/db ",
             "postgresql://u:p%4@h/db", "postgresql://u:p%zz@h/db", "postgresql://u:%0a@h/db",
             "postgresql://u:%ff@h/db", "postgresql://u:p@h/db#x", "postgresql://u:p@h/db/x"))
      refused(bad);
    assertThrows(Rejected.class,
        () -> MembershipSourceObserver.dsn(new byte[] {'p', (byte) 0xC3, (byte) 0x28}));
  }

  @Test
  void theDsnFileMustBeOwnedByTheProcessAndReadOnlyToIt() throws Exception {
    Path file = dir.resolve("membership-source.dsn");
    Files.writeString(file, "postgresql://u:p@h/db");
    for (String mode : List.of("rw-------", "r--r-----", "r-----r--", "r-x------")) {
      Files.setPosixFilePermissions(file, PosixFilePermissions.fromString(mode));
      assertThrows(Rejected.class, () -> MembershipSourceObserver.readSecret(file), mode);
    }
    Files.setPosixFilePermissions(file, PosixFilePermissions.fromString("r--------"));
    assertArrayEquals("postgresql://u:p@h/db".getBytes(StandardCharsets.UTF_8),
        MembershipSourceObserver.readSecret(file));
    Path link = dir.resolve("link.dsn");
    Files.createSymbolicLink(link, file);
    assertThrows(Rejected.class, () -> MembershipSourceObserver.readSecret(link));
    assertThrows(Rejected.class, () -> MembershipSourceObserver.readSecret(dir));
    assertThrows(Rejected.class, () -> MembershipSourceObserver.readSecret(dir.resolve("x")));
    Path big = dir.resolve("big.dsn");
    Files.writeString(big, "x".repeat(MembershipSourceObserver.MAX_DSN + 1));
    Files.setPosixFilePermissions(big, PosixFilePermissions.fromString("r--------"));
    assertThrows(Rejected.class, () -> MembershipSourceObserver.readSecret(big));
  }

  @Test
  void theCaFileMustBeARegularNonEmptyFile() throws Exception {
    Path ca = dir.resolve("ca.pem");
    Files.writeString(ca, "");
    assertThrows(Rejected.class, () -> MembershipSourceObserver.requireCa(ca));
    Files.writeString(ca, "-----BEGIN CERTIFICATE-----\n");
    MembershipSourceObserver.requireCa(ca);
    Path link = dir.resolve("ca-link.pem");
    Files.createSymbolicLink(link, ca);
    assertThrows(Rejected.class, () -> MembershipSourceObserver.requireCa(link));
    assertThrows(Rejected.class, () -> MembershipSourceObserver.requireCa(dir));
  }

  @Test
  void theConnectionIsVerifyFullAgainstThePinnedCaAndReadOnly() {
    var observer = new MembershipSourceObserver(Map.of("dsn_file", "/run/m/source.dsn",
        "ca_file", "/run/m/source-ca.pem", "source_schema", "amh", "publisher_ref", "publisher"));
    var p = observer.properties(dsn("postgresql://u:p@h/db"), 5);
    assertEquals("verify-full", p.getProperty("sslmode"));
    assertEquals("/run/m/source-ca.pem", p.getProperty("sslrootcert"));
    assertEquals("true", p.getProperty("ssl"));
    assertEquals("org.postgresql.ssl.LibPQFactory", p.getProperty("sslfactory"));
    assertEquals("true", p.getProperty("readOnly"));
    assertEquals("5", p.getProperty("connectTimeout"));
    assertEquals("u", p.getProperty("user"));
    assertThrows(Rejected.class, () -> new MembershipSourceObserver(Map.of("dsn_file", "x",
        "ca_file", "/c", "source_schema", "amh", "publisher_ref", "p")));
    assertThrows(Rejected.class, () -> new MembershipSourceObserver(Map.of("dsn_file", "/d",
        "ca_file", "/c", "source_schema", "amh_", "publisher_ref", "p", "x", "y")));
  }
}
