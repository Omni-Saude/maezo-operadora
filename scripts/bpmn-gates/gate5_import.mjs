// Gate 5 — import BPMN como o bpmn-js importa, sem navegador.
//
// O que roda (versoes no log, de package-lock.json):
//   1. `bpmn-moddle` `fromXML` com o descritor `camunda-bpmn-moddle` (o mesmo par que o Modeler
//      usa para CIB seven / Camunda 7). Cada `warnings[]` do parse e' um finding.
//   2. `bpmn-js/lib/import/BpmnTreeWalker` — o caminhante REAL do importador do bpmn-js, com o
//      visitante emulando as excecoes de `BpmnImporter.add` (DI desconhecida, boundary sem host
//      desenhado, conexao com ponta nao desenhada). Cada `error(...)` do caminhante e' um finding,
//      exatamente como `importBpmnDiagram` empurra para `warnings`.
// O que NAO roda: o desenho no canvas (diagram-js + SVG). Isso exige navegador; fica UNVERIFIED.
//
// Saida: uma linha JSON por arquivo em stdout: {"file", "warnings": [{"element", "message"}]}.
// Codigo de saida: 0 sempre que conseguiu ler todos os arquivos; 2 em erro de uso/leitura.
// A decisao de passar/falhar (e a baseline) e' do `bpmn_gates.py`.
import { register } from 'node:module';
import { readFile } from 'node:fs/promises';

register('./esm-extensionless-hook.mjs', import.meta.url);

const { BpmnModdle } = await import('bpmn-moddle');
const { default: BpmnTreeWalker } = await import('bpmn-js/lib/import/BpmnTreeWalker.js');
const camunda = JSON.parse(
  await readFile(new URL('./node_modules/camunda-bpmn-moddle/resources/camunda.json', import.meta.url))
);

function describe(element) {
  if (!element) {
    return '<?>';
  }
  return `${element.$type || '?'}#${element.id || '?'}`;
}

function sourceAndTarget(semantic) {
  const type = semantic.$type;
  let source = semantic.sourceRef;
  let target = semantic.targetRef;
  if (type === 'bpmn:DataInputAssociation') {
    source = Array.isArray(source) ? source[0] : source;
    target = semantic.$parent;
  }
  if (type === 'bpmn:DataOutputAssociation') {
    source = semantic.$parent;
  }
  return [source, target];
}

async function importOne(file) {
  const xml = await readFile(file, 'utf8');
  const moddle = new BpmnModdle({ camunda });
  const warnings = [];
  let parsed;
  try {
    parsed = await moddle.fromXML(xml, 'bpmn:Definitions');
  } catch (error) {
    warnings.push({ element: '<parse>', message: String(error && error.message) });
    return { file, warnings };
  }
  for (const w of parsed.warnings || []) {
    warnings.push({ element: describe(w.element), message: String(w.message) });
  }
  const drawn = new Set();
  const visitor = {
    root(element) {
      drawn.add(element.id);
      return element;
    },
    element(semantic, di, parent) {
      const diType = di && di.$type;
      if (diType === 'bpmndi:BPMNShape') {
        if (semantic.$type === 'bpmn:BoundaryEvent') {
          const host = semantic.attachedToRef;
          if (!host) {
            throw new Error(`missing ${describe(semantic)}#attachedToRef`);
          }
          if (!drawn.has(host.id)) {
            throw new Error(`element ${describe(host)} referenced by ${describe(semantic)}#attachedToRef not yet drawn`);
          }
        }
      } else if (diType === 'bpmndi:BPMNEdge') {
        const [source, target] = sourceAndTarget(semantic);
        for (const [side, ref] of [['source', source], ['target', target]]) {
          if (!ref) {
            throw new Error(`${describe(semantic)}#${side} Ref not specified`);
          }
          if (!drawn.has(ref.id)) {
            throw new Error(`element ${describe(ref)} referenced by ${describe(semantic)}#${side}Ref not yet drawn`);
          }
        }
      } else {
        throw new Error(`unknown di ${describe(di)} for element ${describe(semantic)}`);
      }
      drawn.add(semantic.id);
      return parent;
    },
    error(message, context) {
      warnings.push({ element: describe(context && context.element), message: String(message) });
    },
  };
  const definitions = parsed.rootElement;
  const diagrams = definitions.diagrams || [];
  const originalConsoleError = console.error;
  console.error = () => {}; // o caminhante duplica cada erro em console.error; o finding ja' foi capturado
  try {
    if (diagrams.length === 0) {
      warnings.push({ element: describe(definitions), message: 'no diagram to display' });
    } else {
      // bpmn-js importa UM diagrama por vez; o gate importa TODOS, um walker por diagrama.
      for (const diagram of diagrams) {
        new BpmnTreeWalker(visitor).handleDefinitions(definitions, diagram);
        drawn.clear();
      }
    }
  } catch (error) {
    warnings.push({ element: describe(definitions), message: String(error && error.message) });
  } finally {
    console.error = originalConsoleError;
  }
  return { file, warnings };
}

const files = process.argv.slice(2);
if (files.length === 0) {
  process.stderr.write('uso: node gate5_import.mjs <arquivo.bpmn>...\n');
  process.exit(2);
}
try {
  for (const file of files) {
    process.stdout.write(`${JSON.stringify(await importOne(file))}\n`);
  }
} catch (error) {
  process.stderr.write(`gate5: falha de leitura: ${error && error.stack}\n`);
  process.exit(2);
}
