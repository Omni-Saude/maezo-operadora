#!/usr/bin/env node
/**
 * render_bpmn.mjs — Harness de render REAL bpmn-js + Playwright (Chromium headless).
 *
 * Para cada arquivo .bpmn (arquivo único ou diretório varrido recursivamente):
 *   1. Importa com bpmn-js (importXML), capturando TODOS os warnings
 *      (evento 'import.done' + retorno de importXML + erros de parse do moddle).
 *   2. Exporta SVG (modeler.saveSVG)      -> <out>/svg/<relpath>.svg
 *   3. Exporta PNG em 2x (canvas toDataURL, deviceScaleFactor=2) -> <out>/png/<relpath>.png
 *   4. Registra em <out>/render-report.json: { arquivo, warnings, ok, svg_bytes, png_bytes }
 *
 * Regra: qualquer warning de importação ou erro = falha do arquivo (ok:false).
 * Exit code: 0 se TODOS ok; 1 se qualquer arquivo falhar ou argumentos inválidos.
 *
 * Uso:
 *   node tools/render_bpmn.mjs <arquivo.bpmn>|<dir> --out <dir-render>
 *   (--out opcional; default: ./render no cwd)
 *
 * Sem rede: página about:blank + script UMD bpmn-js injetado do node_modules local.
 */

import { createRequire } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const SCALE = 2;          // resolução 2x do PNG
const VIEWPORT = { width: 1600, height: 1000 };

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------
function parseArgs(argv) {
  const args = { input: null, out: 'render' };
  const rest = [];
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--out') {
      args.out = argv[++i];
      if (!args.out) throw new Error('--out requer um diretório');
    } else {
      rest.push(argv[i]);
    }
  }
  if (rest.length !== 1) {
    throw new Error('uso: node render_bpmn.mjs <arquivo.bpmn>|<dir> [--out <dir-render>]');
  }
  args.input = path.resolve(rest[0]);
  args.out = path.resolve(args.out);
  return args;
}

function collectBpmnFiles(input) {
  const st = fs.statSync(input);
  if (st.isFile()) {
    if (!input.toLowerCase().endsWith('.bpmn')) {
      throw new Error(`arquivo não é .bpmn: ${input}`);
    }
    return [input];
  }
  const found = [];
  const walk = (dir) => {
    for (const name of fs.readdirSync(dir).sort()) {
      if (name === 'node_modules' || name.startsWith('.')) continue;
      const full = path.join(dir, name);
      const s = fs.statSync(full);
      if (s.isDirectory()) walk(full);
      else if (name.toLowerCase().endsWith('.bpmn')) found.push(full);
    }
  };
  walk(input);
  return found.sort();
}

// ---------------------------------------------------------------------------
// Render via Playwright + bpmn-js (UMD do node_modules, sem rede)
// ---------------------------------------------------------------------------
const PAGE_RUNTIME = `
(function () {
  const SCALE = ${SCALE};

  function warningToString(w) {
    if (!w) return String(w);
    if (w.message) return w.message;
    return String(w);
  }

  // SVG (string) -> PNG dataURL em ${SCALE}x via <canvas>.toDataURL('image/png')
  async function rasterizePng(svg) {
    const doc = new DOMParser().parseFromString(svg, 'image/svg+xml');
    const el = doc.documentElement;
    if (el.getElementsByTagName('parsererror').length) {
      throw new Error('SVG exportado é XML inválido');
    }
    let w = parseFloat(el.getAttribute('width'));
    let h = parseFloat(el.getAttribute('height'));
    if ((!isFinite(w) || w <= 0 || !isFinite(h) || h <= 0) && el.getAttribute('viewBox')) {
      const vb = el.getAttribute('viewBox').split(/[\\s,]+/).map(Number);
      if (vb.length === 4 && isFinite(vb[2]) && isFinite(vb[3])) { w = vb[2]; h = vb[3]; }
    }
    if (!isFinite(w) || w <= 0) w = 1000;
    if (!isFinite(h) || h <= 0) h = 700;
    el.setAttribute('width', String(w));
    el.setAttribute('height', String(h));
    const svgNorm = new XMLSerializer().serializeToString(el);

    const img = new Image();
    await new Promise((resolve, reject) => {
      img.onload = resolve;
      img.onerror = () => reject(new Error('falha ao rasterizar SVG em <img>'));
      img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svgNorm);
    });

    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.ceil(w * SCALE));
    canvas.height = Math.max(1, Math.ceil(h * SCALE));
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('canvas 2d indisponível');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    const url = canvas.toDataURL('image/png');
    if (!url || !url.startsWith('data:image/png;base64,')) {
      throw new Error('toDataURL não produziu PNG');
    }
    return { dataUrl: url, width: canvas.width, height: canvas.height };
  }

  // Importa um BPMN; captura warnings do 'import.done', do retorno de importXML
  // e qualquer erro (inclui parse errors do moddle). Depois exporta SVG+PNG.
  window.__renderBpmn = async function (xml) {
    const container = document.createElement('div');
    container.id = 'canvas';
    container.style.cssText = 'width:${VIEWPORT.width}px;height:${VIEWPORT.height}px;position:absolute;left:0;top:0;';
    document.body.appendChild(container);

    const viewer = new BpmnJS({ container: container });
    const warnings = [];
    let doneError = null;
    viewer.on('import.done', (e) => {
      if (e && Array.isArray(e.warnings)) warnings.push(...e.warnings);
      if (e && e.error) doneError = e.error;
    });

    let importError = null;
    let svg = null;
    try {
      const res = await viewer.importXML(xml);
      if (res && Array.isArray(res.warnings)) warnings.push(...res.warnings);
      const out = await viewer.saveSVG();
      svg = out.svg;
    } catch (err) {
      importError = err;
    } finally {
      try { viewer.destroy(); } catch (_) {}
      container.remove();
    }

    const error = importError || doneError;
    if (error) {
      return {
        ok: false,
        warnings: warnings.map(warningToString).concat([String(error.message || error)]),
        svg: null,
        png: null
      };
    }

    const png = await rasterizePng(svg);
    const warningList = warnings.map(warningToString);
    return { ok: warningList.length === 0, warnings: warningList, svg: svg, png: png };
  };
})();
`;

// Resolve um binário Chromium já instalado no cache do Playwright (evita `playwright install`).
// Preferência: chrome-headless-shell (headless nativo); fallback: Chromium/Chrome for Testing.
function resolveChromiumExecutable() {
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH ||
    path.join(os.homedir(), 'Library', 'Caches', 'ms-playwright');
  const wanted = new Set(['chrome-headless-shell', 'chrome', 'Chromium', 'Google Chrome for Testing']);
  const builds = [];
  let entries = [];
  try { entries = fs.readdirSync(root); } catch { return undefined; }
  for (const name of entries) {
    const m = name.match(/^(chromium_headless_shell|chromium)-(\d+)$/);
    if (!m) continue;
    const dir = path.join(root, name);
    let bin = undefined;
    const walk = (d, depth) => {
      if (bin || depth > 6) return;
      let names = [];
      try { names = fs.readdirSync(d); } catch { return; }
      for (const n of names) {
        const full = path.join(d, n);
        let s; try { s = fs.statSync(full); } catch { continue; }
        if (s.isFile() && wanted.has(n)) { bin = full; return; }
        if (s.isDirectory() && n !== 'resources') walk(full, depth + 1);
      }
    };
    walk(dir, 0);
    if (bin) builds.push({ kind: m[1], ver: Number(m[2]), bin });
  }
  if (builds.length === 0) return undefined;
  builds.sort((a, b) =>
    (b.ver - a.ver) ||
    (a.kind === 'chromium_headless_shell' ? -1 : 1) - (b.kind === 'chromium_headless_shell' ? -1 : 1));
  return builds[0].bin;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const files = collectBpmnFiles(args.input);
  if (files.length === 0) {
    console.error(`[render_bpmn] nenhum arquivo .bpmn encontrado em: ${args.input}`);
    process.exit(1);
  }

  const bpmnJsUmd = require.resolve('bpmn-js/dist/bpmn-viewer.production.min.js');

  const outSvgDir = path.join(args.out, 'svg');
  const outPngDir = path.join(args.out, 'png');
  fs.mkdirSync(outSvgDir, { recursive: true });
  fs.mkdirSync(outPngDir, { recursive: true });

  const executablePath = resolveChromiumExecutable();
  const browser = await chromium.launch({ headless: true, executablePath });
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: SCALE, // resolução 2x
  });
  const page = await context.newPage();
  await page.goto('about:blank');
  // bpmn-js UMD local via file:// (sem rede, sem CDN) -> window.BpmnJS
  await page.addScriptTag({ path: bpmnJsUmd, type: 'text/javascript' });
  await page.evaluate(PAGE_RUNTIME);

  const report = [];
  let failures = 0;

  for (const file of files) {
    const rel = path.relative(
      fs.statSync(args.input).isDirectory() ? args.input : path.dirname(args.input),
      file
    ).split(path.sep).join('/');
    const base = rel.replace(/\.bpmn$/i, '');
    const entry = { arquivo: rel, warnings: [], ok: false, svg_bytes: 0, png_bytes: 0 };

    try {
      const xml = fs.readFileSync(file, 'utf8');
      const res = await page.evaluate(async (xmlText) => window.__renderBpmn(xmlText), xml);

      entry.warnings = res.warnings || [];

      if (!res.ok || !res.svg || !res.png) {
        entry.ok = false;
        failures++;
        console.error(`[render_bpmn] FALHA ${rel}: ${entry.warnings.length} warning(s)/erro(s)`);
        for (const w of entry.warnings) console.error(`  - ${w}`);
      } else {
        const svgPath = path.join(outSvgDir, `${base}.svg`);
        const pngPath = path.join(outPngDir, `${base}.png`);
        fs.mkdirSync(path.dirname(svgPath), { recursive: true });
        fs.mkdirSync(path.dirname(pngPath), { recursive: true });
        fs.writeFileSync(svgPath, res.svg);
        fs.writeFileSync(pngPath, Buffer.from(res.png.dataUrl.split(',')[1], 'base64'));
        entry.svg_bytes = fs.statSync(svgPath).size;
        entry.png_bytes = fs.statSync(pngPath).size;
        entry.ok = true;
        console.log(
          `[render_bpmn] ok ${rel}  svg=${entry.svg_bytes}B png=${entry.png_bytes}B ` +
          `(${res.png.width}x${res.png.height}px @${SCALE}x)`
        );
      }
    } catch (err) {
      entry.warnings.push(String(err && err.message ? err.message : err));
      entry.ok = false;
      failures++;
      console.error(`[render_bpmn] FALHA ${rel}: ${entry.warnings[0]}`);
    }

    report.push(entry);
  }

  await browser.close();

  const reportPath = path.join(args.out, 'render-report.json');
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');

  const okCount = report.filter((r) => r.ok).length;
  console.log(
    `[render_bpmn] resumo: ${okCount} ok, ${report.length - okCount} falha(s) de ${report.length} arquivo(s)` +
    ` -> ${reportPath}`
  );

  process.exit(failures > 0 ? 1 : 0);
}

main().catch((err) => {
  console.error(`[render_bpmn] erro fatal: ${err && err.stack ? err.stack : err}`);
  process.exit(1);
});
