import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Repo convention: CI/dev run `uv sync` once, then everything downstream calls
// the resulting `.venv/bin/python` directly (Makefile targets do the same,
// e.g. `.venv/bin/python -m pytest ...`), instead of paying `uv run`'s
// resolve-and-reactivate overhead on every invocation.
const PYTHON = "../../../../.venv/bin/python";

function exportSchema(scriptRelPath, outFile) {
  execFileSync(PYTHON, [scriptRelPath, "--output", outFile], { stdio: "inherit" });
}

function generateClient(schemaFile, outFile) {
  execFileSync("./node_modules/.bin/openapi-typescript", [schemaFile, "-o", outFile], {
    stdio: "inherit",
  });
}

function assertMatches(generatedFile, checkedInFile, label) {
  if (!readFileSync(generatedFile).equals(readFileSync(checkedInFile))) {
    throw new Error(`${label} divergiu do gerador; execute npm run generate`);
  }
}

const temp = mkdtempSync(join(tmpdir(), "maezo-portal-openapi-"));
try {
  // Public portal API surface.
  const schema = join(temp, "openapi.json");
  const client = join(temp, "api.ts");
  exportSchema("../../../../scripts/dev/export_portal_openapi.py", schema);
  generateClient(schema, client);
  assertMatches(schema, "openapi.json", "openapi.json");
  assertMatches(client, "src/generated/api.ts", "src/generated/api.ts");

  // PHI-scoped read-only surface — mirrors the public-API check above so the
  // checked-in phiApi.ts client can never silently drift from the exporter
  // that is its source of truth (scripts/dev/export_phi_openapi.py).
  const phiSchema = join(temp, "phi-openapi.json");
  const phiClient = join(temp, "phiApi.ts");
  exportSchema("../../../../scripts/dev/export_phi_openapi.py", phiSchema);
  generateClient(phiSchema, phiClient);
  assertMatches(phiSchema, "phi-openapi.json", "phi-openapi.json");
  assertMatches(phiClient, "src/generated/phiApi.ts", "src/generated/phiApi.ts");

  process.stdout.write("OpenAPI e tipos TypeScript (publico + PHI) estao em paridade.\n");
} finally {
  rmSync(temp, { recursive: true, force: true });
}
