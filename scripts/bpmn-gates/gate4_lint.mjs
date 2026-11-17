// Gate 4 — bpmnlint com a configuracao versionada `.bpmnlintrc` (raiz do repo).
//
// Mesma sequencia do CLI `bpmnlint` (bin/bpmnlint.js, lintDiagram): parse com bpmn-moddle, warnings
// de import viram categoria `error` (como o CLI faz), depois `Linter.lint` com `NodeResolver`.
// A diferenca e' so' a saida: uma linha JSON por arquivo, para o `bpmn_gates.py` aplicar a
// baseline e decidir. Uso: node gate4_lint.mjs <.bpmnlintrc> <arquivo.bpmn>...
// Codigo de saida: 0 se leu tudo; 2 em erro de uso/leitura/config.
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';

const require = createRequire(import.meta.url);
const { Linter } = require('bpmnlint');
const NodeResolver = require('bpmnlint/lib/resolver/node-resolver');
const { BpmnModdle } = await import('bpmn-moddle');

const [configPath, ...files] = process.argv.slice(2);
if (!configPath || files.length === 0) {
  process.stderr.write('uso: node gate4_lint.mjs <.bpmnlintrc> <arquivo.bpmn>...\n');
  process.exit(2);
}

let config;
try {
  config = JSON.parse(await readFile(configPath, 'utf8'));
} catch (error) {
  process.stderr.write(`gate4: nao li a configuracao ${configPath}: ${error && error.message}\n`);
  process.exit(2);
}
if (config.moddleExtensions) {
  process.stderr.write('gate4: moddleExtensions na .bpmnlintrc nao e suportado por este harness\n');
  process.exit(2);
}

try {
  for (const file of files) {
    const xml = await readFile(file, 'utf8');
    const reports = [];
    let parsed;
    try {
      parsed = await new BpmnModdle().fromXML(xml, 'bpmn:Definitions');
    } catch (error) {
      reports.push({ id: '', rule: '<parse>', category: 'error', message: `Parse error: ${error.message}` });
      process.stdout.write(`${JSON.stringify({ file, reports })}\n`);
      continue;
    }
    for (const w of parsed.warnings || []) {
      reports.push({
        id: (w.element && w.element.id) || '',
        rule: '<import>',
        category: 'error',
        message: `Import warning: ${String(w.message).split(/\n/)[0]}`,
      });
    }
    // Um Linter POR ARQUIVO, como o CLI (bin/bpmnlint.js): regras como no-duplicate-sequence-flows
    // guardam estado por id, e um Linter compartilhado acusa duplicata ENTRE arquivos (medido aqui).
    const linter = new Linter({ config, resolver: new NodeResolver() });
    const results = await linter.lint(parsed.rootElement);
    for (const [rule, items] of Object.entries(results)) {
      for (const item of items) {
        reports.push({ id: item.id || '', rule, category: item.category, message: item.message });
      }
    }
    process.stdout.write(`${JSON.stringify({ file, reports })}\n`);
  }
} catch (error) {
  process.stderr.write(`gate4: falha: ${error && error.stack}\n`);
  process.exit(2);
}
