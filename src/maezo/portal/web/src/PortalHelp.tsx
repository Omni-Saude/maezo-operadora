import type { PortalAudience } from "./caseExperienceModels";

// F-159 (R-089, WCAG 3.2.6 Consistent Help / Nielsen H10): one help mechanism, rendered by
// the application shell at the END of every portal state (signed out, loading, errors and
// each audience's session), so it keeps the same relative order everywhere. It is a
// self-help option plus a pointer to the human channel each audience already has; it names
// no phone number, address or schedule, because those are operator data this portal does
// not hold.

const humanChannel: Readonly<Record<PortalAudience, string>> = {
  staff: "Para dúvidas sobre tarefas, filas ou permissões, procure a coordenação da sua equipe.",
  beneficiary: "Para falar com uma pessoa, use os canais oficiais de atendimento da sua operadora.",
  provider:
    "Para falar com uma pessoa, use os canais oficiais de relacionamento com prestadores da operadora.",
  vendor: "Para falar com uma pessoa, use os canais oficiais de atendimento da operadora.",
};

const generalChannel = "Para falar com uma pessoa, use os canais oficiais de atendimento da operadora.";

export function PortalHelp({ audience }: { audience?: PortalAudience }) {
  return (
    <footer className="portal-help" aria-labelledby="portal-help-heading">
      <h2 id="portal-help-heading">Ajuda</h2>
      <p>
        Cada tela informa o que aconteceu e qual é o próximo passo possível. Se a sessão expirar
        ou não for confirmada, entre novamente no portal.
      </p>
      <p>{audience === undefined ? generalChannel : humanChannel[audience]}</p>
    </footer>
  );
}
