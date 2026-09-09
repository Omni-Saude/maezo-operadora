// @vitest-environment node

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const productionSources = ["src/App.tsx", "src/main.tsx", "src/sessionClient.ts"]
  .map((path) => readFileSync(path, "utf8"))
  .join("\n");

describe("fronteira do navegador", () => {
  it.each([
    "localStorage",
    "sessionStorage",
    "indexedDB",
    "serviceWorker",
    "navigator.storage",
    "caches.open",
    "sendBeacon",
    "analytics",
    "Authorization",
    "Bearer ",
    "console.",
  ])("não inclui %s no código de produção", (forbidden) => {
    expect(productionSources).not.toContain(forbidden);
  });

  it("mantém chamadas da sessão em same-origin e no-store", () => {
    expect(productionSources.match(/credentials: "same-origin"/g)).toHaveLength(2);
    expect(productionSources.match(/cache: "no-store"/g)).toHaveLength(2);
  });
});
