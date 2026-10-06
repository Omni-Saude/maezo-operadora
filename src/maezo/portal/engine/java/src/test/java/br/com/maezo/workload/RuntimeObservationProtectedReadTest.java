package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.*;
import java.nio.channels.FileChannel;
import java.util.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import br.com.maezo.human.Jcs;

/** Pure file-custody tests; host capability is explicit and never runtime acceptance. */
class RuntimeObservationProtectedReadTest {
  @TempDir Path temporary;
  private Path protectedFile(String name)throws Exception {
    Files.setPosixFilePermissions(temporary,PosixFilePermissions.fromString("rwx------"));
    Path path=temporary.resolve(name);Files.write(path,new byte[]{1,2,3});
    Files.setPosixFilePermissions(path,PosixFilePermissions.fromString("rw-------"));return path.toRealPath();
  }
  @Test void unavailableLinuxDescriptorCustodyRefusesRatherThanPathOnlySuccess()throws Exception {
    assumeFalse(Files.isDirectory(Path.of("/proc/self/fd")),"Linux descriptor tests require their actual kernel surface");
    Path path=protectedFile("pinned.json");
    assertThrows(Refused.class,()->RuntimeObservationAdmission.readProtected(path,Jcs.digest(new byte[]{1,2,3}),64));
  }
  private static void linux() {
    assumeTrue(Files.isDirectory(Path.of("/proc/self/fd")),"Actual Linux opened-FD custody is unavailable on this host");
  }
  @Test void actualLinuxProtectedReadClosesDescriptorAndReturnsPinnedBytes()throws Exception {
    linux();Path path=protectedFile("valid.json");var before=RuntimeObservationAdmission.descriptorCensus();
    assertArrayEquals(new byte[]{1,2,3},RuntimeObservationAdmission.readProtected(path,Jcs.digest(new byte[]{1,2,3}),64));
    assertEquals(before,RuntimeObservationAdmission.descriptorCensus());
  }
  @Test void wrongOpenedInodeRefusesEvenWithSameBytesAndAnExternalMatchingDescriptor()throws Exception {
    linux();Path wanted=protectedFile("wanted.json"),substituted=protectedFile("substituted.json");
    var expected=Files.readAttributes(wanted,"unix:mode,uid,nlink,ino,dev,size,lastModifiedTime",LinkOption.NOFOLLOW_LINKS);
    try(var external=FileChannel.open(wanted,StandardOpenOption.READ)) {
      var before=RuntimeObservationAdmission.descriptorCensus();
      try(var opened=FileChannel.open(substituted,StandardOpenOption.READ)) {
        var actual=RuntimeObservationAdmission.openedDescriptor(before,RuntimeObservationAdmission.descriptorCensus());
        assertNotEquals(expected.get("ino"),RuntimeObservationAdmission.fdAttributes(actual).get("ino"));
        assertThrows(Refused.class,()->RuntimeObservationAdmission.verifyOpenedDescriptor(opened,actual,expected));
      }
    }
  }
  @Test void anotherDescriptorToSameInodeCannotImpersonateOpenedChannel()throws Exception {
    linux();Path wanted=protectedFile("offset.json");
    try(var external=FileChannel.open(wanted,StandardOpenOption.READ)) {
      var before=RuntimeObservationAdmission.descriptorCensus();
      try(var opened=FileChannel.open(wanted,StandardOpenOption.READ)) {
        var actual=RuntimeObservationAdmission.openedDescriptor(before,RuntimeObservationAdmission.descriptorCensus());
        var expected=RuntimeObservationAdmission.fdAttributes(actual);
        assertThrows(Refused.class,()->RuntimeObservationAdmission.verifyOpenedDescriptor(external,actual,expected));
      }
    }
  }
  @Test void unrelatedConcurrentDescriptorMakesAttributionAmbiguous()throws Exception {
    linux();Path wanted=protectedFile("wanted.json"),unrelated=protectedFile("unrelated.json");
    var before=RuntimeObservationAdmission.descriptorCensus();
    try(var opened=FileChannel.open(wanted,StandardOpenOption.READ);var other=FileChannel.open(unrelated,StandardOpenOption.READ)) {
      assertThrows(Refused.class,()->RuntimeObservationAdmission.openedDescriptor(before,RuntimeObservationAdmission.descriptorCensus()));
    }
  }
  @ParameterizedTest @ValueSource(strings={"uid","mode","nlink","dev","ino","size","lastModifiedTime"})
  void openedDescriptorCannotDivergeOnAnyCustodyFact(String key)throws Exception {
    linux();Path path=protectedFile("facts.json");var before=RuntimeObservationAdmission.descriptorCensus();
    try(var opened=FileChannel.open(path,StandardOpenOption.READ)) {
      var actual=RuntimeObservationAdmission.openedDescriptor(before,RuntimeObservationAdmission.descriptorCensus());
      var expected=new HashMap<>(RuntimeObservationAdmission.fdAttributes(actual));
      if(key.equals("lastModifiedTime"))expected.put(key,java.nio.file.attribute.FileTime.fromMillis(1));
      else expected.put(key,((Number)expected.get(key)).longValue()+1);
      assertThrows(Refused.class,()->RuntimeObservationAdmission.verifyOpenedDescriptor(opened,actual,expected));
    }
  }
  @Test void actualUnsafeModeAndMultipleLinksRefuse()throws Exception {
    linux();Path path=protectedFile("custody.json");String digest=Jcs.digest(new byte[]{1,2,3});
    Files.setPosixFilePermissions(path,PosixFilePermissions.fromString("rw-r-----"));
    assertThrows(Refused.class,()->RuntimeObservationAdmission.readProtected(path,digest,64));
    Files.setPosixFilePermissions(path,PosixFilePermissions.fromString("rw-------"));
    Files.createLink(temporary.resolve("second-link.json"),path);
    assertThrows(Refused.class,()->RuntimeObservationAdmission.readProtected(path,digest,64));
  }
  @Test void boundedContentAndPinnedDigestRemainRequired()throws Exception {
    linux();Path path=protectedFile("bounds.json");
    assertThrows(Refused.class,()->RuntimeObservationAdmission.readProtected(path,Jcs.digest(new byte[]{1,2,3}),2));
    assertThrows(Refused.class,()->RuntimeObservationAdmission.readProtected(path,"0".repeat(64),64));
  }

  @Test void unrelatedExistingWriterContentChangeDoesNotBlockActualTargetAttribution()throws Exception {
    linux();Path writer=protectedFile("existing-writer.json"),wanted=protectedFile("attributed-target.json");
    var initial=RuntimeObservationAdmission.descriptorCensus();
    try(var existing=FileChannel.open(writer,StandardOpenOption.WRITE)) {
      var before=RuntimeObservationAdmission.descriptorCensus();
      var writerDescriptor=RuntimeObservationAdmission.openedDescriptor(initial,before);
      existing.position(existing.size());existing.write(java.nio.ByteBuffer.wrap(new byte[]{4,5,6,7}));existing.force(true);
      try(var opened=FileChannel.open(wanted,StandardOpenOption.READ)) {
        var after=RuntimeObservationAdmission.descriptorCensus();
        assertNotEquals(before.get(writerDescriptor).get("size"),after.get(writerDescriptor).get("size"));
        for(String key:List.of("dev","ino","mode","uid","nlink"))
          assertEquals(before.get(writerDescriptor).get(key),after.get(writerDescriptor).get(key),key);
        var actual=RuntimeObservationAdmission.openedDescriptor(before,after);
        assertDoesNotThrow(()->RuntimeObservationAdmission.verifyOpenedDescriptor(opened,actual,
            Files.readAttributes(wanted,"unix:mode,uid,nlink,ino,dev,size,lastModifiedTime",LinkOption.NOFOLLOW_LINKS)));
      }
    }
  }

  @ParameterizedTest @ValueSource(strings={"uid","mode","nlink","dev","ino"})
  void existingDescriptorIdentityDivergenceStillRefuses(String key)throws Exception {
    linux();Path existingPath=protectedFile("old-identity.json"),wanted=protectedFile("wanted-identity.json");
    try(var existing=FileChannel.open(existingPath,StandardOpenOption.READ)) {
      var before=RuntimeObservationAdmission.descriptorCensus();
      var expectedOld=Files.readAttributes(existingPath,"unix:dev,ino",LinkOption.NOFOLLOW_LINKS);
      var matches=before.entrySet().stream().filter(row->Objects.equals(row.getValue().get("dev"),expectedOld.get("dev"))
          && Objects.equals(row.getValue().get("ino"),expectedOld.get("ino"))).toList();
      assertEquals(1,matches.size());var existingDescriptor=matches.get(0).getKey();
      try(var opened=FileChannel.open(wanted,StandardOpenOption.READ)) {
        var after=new HashMap<>(RuntimeObservationAdmission.descriptorCensus());
        assertDoesNotThrow(()->RuntimeObservationAdmission.openedDescriptor(before,after));
        var changed=new HashMap<>(after.get(existingDescriptor));
        changed.put(key,((Number)changed.get(key)).longValue()+1);after.put(existingDescriptor,changed);
        assertThrows(Refused.class,()->RuntimeObservationAdmission.openedDescriptor(before,after));
      }
    }
  }
  @Test void existingDescriptorClosingOrReplacementStillRefuses()throws Exception {
    linux();Path oldPath=protectedFile("closed-old.json"),wanted=protectedFile("replacement.json");
    var existing=FileChannel.open(oldPath,StandardOpenOption.READ);
    try {
      var before=RuntimeObservationAdmission.descriptorCensus();existing.close();
      try(var opened=FileChannel.open(wanted,StandardOpenOption.READ)) {
        var after=RuntimeObservationAdmission.descriptorCensus();
        assertThrows(Refused.class,()->RuntimeObservationAdmission.openedDescriptor(before,after));
      }
    } finally {existing.close();}
  }
  @ParameterizedTest @ValueSource(strings={"mode","nlink"})
  void existingDescriptorActualCustodyChangeStillRefuses(String fact)throws Exception {
    linux();Path oldPath=protectedFile("changed-old.json"),wanted=protectedFile("wanted-custody.json");
    try(var existing=FileChannel.open(oldPath,StandardOpenOption.READ)) {
      var before=RuntimeObservationAdmission.descriptorCensus();
      if(fact.equals("mode"))Files.setPosixFilePermissions(oldPath,PosixFilePermissions.fromString("rw-r-----"));
      else Files.createLink(temporary.resolve("old-second-link.json"),oldPath);
      try(var opened=FileChannel.open(wanted,StandardOpenOption.READ)) {
        var after=RuntimeObservationAdmission.descriptorCensus();
        assertThrows(Refused.class,()->RuntimeObservationAdmission.openedDescriptor(before,after));
      }
    }
  }

}
