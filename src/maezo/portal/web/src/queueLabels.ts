import { taskLabels } from "./taskLabels";

// R-090 / F-099: the queue speaks the user's language. Every label comes from the
// generated taskLabels.ts (the BPMN `name` of the bound process/userTask); nothing
// here is written by hand. A key outside that closed set is returned raw, never
// hidden and never guessed. Lookups use only the two technical keys, so no case or
// person data can reach a label.

const byTask = new Map<string, string>();
const byProcess = new Map<string, string>();
for (const row of taskLabels) {
  byTask.set(`${row.process}\u0000${row.task}`, row.taskLabel);
  byProcess.set(row.process, row.processLabel);
}

export function taskLabel(processKey: string, taskKey: string): string {
  return byTask.get(`${processKey}\u0000${taskKey}`) ?? taskKey;
}

export function processLabel(processKey: string): string {
  return byProcess.get(processKey) ?? processKey;
}

// Exact BRL rendering of an integer cents string (already validated by the read
// client as /^(0|-?[1-9][0-9]*)$/). String arithmetic only: no float rounding.
export function formatCents(cents: string): string {
  const negative = cents.startsWith("-");
  const digits = (negative ? cents.slice(1) : cents).padStart(3, "0");
  const whole = digits.slice(0, -2).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${negative ? "-" : ""}R$ ${whole},${digits.slice(-2)}`;
}
