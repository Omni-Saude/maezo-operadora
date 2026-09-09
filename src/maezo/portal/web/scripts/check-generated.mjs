import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const temp = mkdtempSync(join(tmpdir(), "maezo-portal-openapi-"));
try {
  const schema = join(temp, "openapi.json");
  const client = join(temp, "api.ts");
  execFileSync(
    "uv",
    ["run", "python", "../../../../scripts/dev/export_portal_openapi.py", "--output", schema],
    { stdio: "inherit" },
  );
  execFileSync("./node_modules/.bin/openapi-typescript", [schema, "-o", client], {
    stdio: "inherit",
  });
  if (!readFileSync(schema).equals(readFileSync("openapi.json"))) {
    throw new Error("openapi.json divergiu do app FastAPI; execute npm run generate");
  }
  if (!readFileSync(client).equals(readFileSync("src/generated/api.ts"))) {
    throw new Error("src/generated/api.ts divergiu do OpenAPI; execute npm run generate");
  }
  process.stdout.write("OpenAPI e tipos TypeScript estão em paridade.\n");
} finally {
  rmSync(temp, { recursive: true, force: true });
}
