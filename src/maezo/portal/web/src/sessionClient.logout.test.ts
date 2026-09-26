import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { postLogout, trustedIdpLogoutUrl } from "./sessionClient";

const PORTAL = "https://portal-maezo-dev.austa.com.br";
const IDP = "https://amh-maezo-bpm-dev.auth.sa-east-1.amazoncognito.com";
const good = `${IDP}/logout?client_id=61gml104sr5nc8jskrptstua0u&logout_uri=${encodeURIComponent(`${PORTAL}/`)}`;

describe("trustedIdpLogoutUrl", () => {
  it("aceita exatamente o /logout do Hosted UI do Cognito voltando para este portal", () => {
    expect(trustedIdpLogoutUrl(good, PORTAL)).toBe(good);
  });

  it.each([
    ["não-string", 42],
    ["origem estranha", good.replace(IDP, "https://attacker.test")],
    ["sufixo enganoso", good.replace(IDP, `${IDP}.attacker.test`)],
    ["subdomínio falso", good.replace(IDP, "https://x.auth.sa-east-1.amazoncognito.com.evil.test")],
    ["http", good.replace("https://", "http://")],
    ["porta", good.replace(".amazoncognito.com", ".amazoncognito.com:8443")],
    ["credenciais", good.replace("https://", "https://u:p@")],
    ["caminho", good.replace("/logout?", "/oauth2/authorize?")],
    ["fragmento", `${good}#x`],
    ["parâmetro extra", `${good}&redirect_uri=https://attacker.test/`],
    ["logout_uri alheio", good.replace(encodeURIComponent(`${PORTAL}/`), encodeURIComponent("https://attacker.test/"))],
    ["client_id inválido", good.replace("61gml104sr5nc8jskrptstua0u", "../x")],
    ["javascript:", "javascript:alert(1)"],
  ])("recusa %s", (_label, raw) => {
    expect(trustedIdpLogoutUrl(raw, PORTAL)).toBeNull();
  });
});

describe("postLogout", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it("confirma no 200 e só devolve URL confiável para a origem deste documento", async () => {
    const own = `${IDP}/logout?client_id=abc&logout_uri=${encodeURIComponent(`${window.location.origin}/`)}`;
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ schema_version: 1, idp_logout_url: own }), { status: 200 }),
    );
    expect(await postLogout("csrf", new AbortController().signal)).toEqual({
      kind: "confirmed",
      idpLogoutUrl: own,
    });
  });

  it("confirma o logout local mesmo com URL hostil, sem navegar para ela", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ idp_logout_url: "https://attacker.test/logout" }), { status: 200 }),
    );
    expect(await postLogout("csrf", new AbortController().signal)).toEqual({
      kind: "confirmed",
      idpLogoutUrl: null,
    });
  });

  it("não confirma fora do 200", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response(null, { status: 204 }));
    expect(await postLogout("csrf", new AbortController().signal)).toEqual({ kind: "unconfirmed" });
  });
});
