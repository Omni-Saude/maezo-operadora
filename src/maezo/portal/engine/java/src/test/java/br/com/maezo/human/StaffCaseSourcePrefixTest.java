package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;

import org.junit.jupiter.api.Test;

/** C1 F9: identity_verifier casa por prefixo terminado em ':' ou '/'; os demais papeis, exato. */
class StaffCaseSourcePrefixTest {
  @Test void prefixoCasaPrincipalDoTenant() {
    assertTrue(StaffCaseInstallation.sourceMatches("identity_verifier", "amh:", "amh:principal-1"));
    assertTrue(StaffCaseInstallation.sourceMatches("identity_verifier", "membership/amh/", "membership/amh/p-2"));
  }

  @Test void prefixoNaoCasaVizinhoNemOProprioPrefixo() {
    assertFalse(StaffCaseInstallation.sourceMatches("identity_verifier", "amh:", "amhx:principal-1"));
    assertFalse(StaffCaseInstallation.sourceMatches("identity_verifier", "amh", "amhx"));
    assertFalse(StaffCaseInstallation.sourceMatches("identity_verifier", "amh", "amh:principal-1"));
    assertFalse(StaffCaseInstallation.sourceMatches("identity_verifier", "amh:", "amh:"));
    assertFalse(StaffCaseInstallation.sourceMatches("identity_verifier", null, "amh:p"));
  }

  @Test void outrosPapeisSeguemExatos() {
    assertTrue(StaffCaseInstallation.sourceMatches("case_issuer", "src-1", "src-1"));
    assertFalse(StaffCaseInstallation.sourceMatches("case_issuer", "src:", "src:1"));
  }
}
