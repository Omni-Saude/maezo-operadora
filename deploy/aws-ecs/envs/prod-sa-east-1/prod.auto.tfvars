// Configuracao NAO secreta de PROD (conta 441395443640). Versionada: o proximo apply da main nao pode
// desfazer um valor que so' existiu no terminal de quem aplicou.
//
// O QUE NUNCA ENTRA AQUI (por regra, mesma do dev):
//   * a ATESTACAO PHI: `phi_zone_provider`, `phi_model_id`, `phi_vendor_dpa_ref`, `helena_zona_phi`
//     -> so' por `-var`, no apply, por quem tem autoridade para atestar (variables.tf, sem default);
//   * os digests das imagens da aplicacao/engine -> `-var` no apply, resolvidos do ECR do dev;
//   * qualquer numero de telefone, hash de conversa ou allowlist do time (as do dev NAO valem aqui).
//
// CUTOVER = um PR que muda os dois interruptores abaixo (README, "Cutover"). Nunca `-var`.

// Interruptor geral: false = todo servico com 0 tasks e nenhuma rotina agendada.
servicos_habilitados = false

// Tunel Cloudflare de prod (webhook da Meta, cockpit): false = nenhuma entrada publica.
borda_publica_habilitada = false

// Destinatario dos alarmes de prod: decidir no cutover (o check do plan avisa se ficar vazio com servicos ligados).
staff_alerts_email = ""
