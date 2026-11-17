// Gancho de resolucao ESM: `bpmn-js/lib/import/*.js` importa caminhos relativos SEM extensao
// (`./Util`), o que o bundler resolve e o Node puro nao. Este gancho so' tenta `<especificador>.js`
// para especificador RELATIVO que falhou com ERR_MODULE_NOT_FOUND — nada mais muda na resolucao.
export async function resolve(specifier, context, nextResolve) {
  try {
    return await nextResolve(specifier, context);
  } catch (error) {
    const relative = specifier.startsWith('./') || specifier.startsWith('../');
    if (error && error.code === 'ERR_MODULE_NOT_FOUND' && relative && !specifier.endsWith('.js')) {
      return nextResolve(`${specifier}.js`, context);
    }
    throw error;
  }
}
