package br.com.maezo.human;

/**
 * The AUTH business key `AUTH-{tenant}-{numero_guia_tiss}` (owner decision #16, D-K / T1.11).
 * Mirrors `maezo.tools.process_business_keys.auth_business_key`: an empty component, or one with whitespace or a
 * control character, is refused instead of collapsing into a key shared by distinct requests.
 * Parity is proven by the shared vector `auth-business-key-vectors.json` (Java test and Python test read it).
 */
final class AuthBusinessKeys {
  static final String PREFIX="AUTH-";
  private AuthBusinessKeys() {}
  static String auth(String tenant,String guide){return PREFIX+component(tenant)+"-"+component(guide);}
  private static String component(String value) {
    if(value==null||value.isEmpty())throw Rejected.invalid();
    // Python: ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F (isspace also covers U+001C..U+001F, U+0085, Zs, U+2028/9).
    if(value.codePoints().anyMatch(c->c<0x20||c==0x7F||c==0x85||Character.isWhitespace(c)||Character.isSpaceChar(c)))
      throw Rejected.invalid();
    return value;
  }
}
