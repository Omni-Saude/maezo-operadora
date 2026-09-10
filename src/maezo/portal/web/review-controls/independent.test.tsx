import {act,fireEvent,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,expect,it,vi} from 'vitest';
import {DecisionWorkspace} from '../src/DecisionWorkspace';
import {makeSubmission,readDecisionContext,readDecisionReceipt,submitDecision,validateContext,validateReceipt} from '../src/decisionClient';
import {contextFixture,admissionFixture,receiptFixture,jsonResponse} from '../src/test/decisionFixtures';

const SECRET_TEXT='SYNTHETIC_CLINICAL_REVIEW e\u0301\nwith\ttab';
afterEach(()=>{vi.unstubAllGlobals();vi.useRealTimers();});
it.each(['schema_version','task_id','process_definition_key','task_definition_key','form_key','form_source_status','allowed_actions','allowed_inputs','read_only_evidence'])('refuses transposed context coordinate %s',(key)=>{
 const c=contextFixture();const altered={...c,snapshot:{...c.snapshot,[key]:key==='allowed_actions'?['decision','assign']:key==='allowed_inputs'?['actor_id']:key==='read_only_evidence'?{}:'foreign'}};
 expect(validateContext(altered,'task-1')).toBeNull();
});
it.each(['expected_membership_revision','expected_authority_revision','binding_digest'])('refuses imprecise context pin %s',(key)=>{
 expect(validateContext({...contextFixture(),[key]:Number.MAX_SAFE_INTEGER+1},'task-1')).toBeNull();
});
it('all six 5000-digit pins and exact Unicode survive simultaneous submission',()=>{
 const c=contextFixture('escalation'),huge='9'.repeat(5000);
 c.expected_authority_revision=c.expected_membership_revision=huge;
 c.snapshot.process_definition_version=c.snapshot.form_version=c.snapshot.task_revision=c.snapshot.evidence_revision=huge;
 const b=makeSubmission(c,{kind:'escalation',resultado:'resolvido_humano',notas_resolucao:SECRET_TEXT},'command-1')!;
 expect(b).not.toBeNull();const wire=JSON.parse(JSON.stringify(b));
 for(const field of ['process_definition_version','form_version','expected_task_revision','expected_evidence_revision','expected_membership_revision'])expect(wire.decision[field]).toBe(huge);
 expect(wire.expected_authority_revision).toBe(huge);expect(wire.decision.inputs.notas_resolucao).toBe(SECRET_TEXT);
});
it.each(['task_id','command_id','operation','schema_version','resulting_task_revision','audit_result_ref','engine_receipt_ref','engine_recorded_at','consumed_task_revision'])('refuses incomplete or transposed committed receipt %s',(key)=>{
 const r=receiptFixture('command-1');const value=['task_id','command_id','operation','schema_version'].includes(key)?'foreign':key==='resulting_task_revision'?'1':null;
 expect(validateReceipt({...r,[key]:value},'task-1','command-1')).toBeNull();
});
it.each(['pending','conflict'])('refuses executed proof attached to %s',(status)=>{
 expect(validateReceipt({...receiptFixture('command-1'),status},'task-1','command-1')).toBeNull();
});
it('non-202 response with an otherwise valid pending body never admits',async()=>{
 const b=makeSubmission(contextFixture(),{kind:'auth_decisao',decisao_auditor:'APROVAR'},'command-1')!;
 vi.stubGlobal('fetch',vi.fn().mockImplementation(()=>Promise.resolve(jsonResponse(admissionFixture('command-1'),200))));
 expect(await submitDecision(b,'csrf',new AbortController().signal)).toEqual({kind:'outcome-unknown'});
});
it('abort is propagated and safe dependency errors contain no upstream text',async()=>{
 const fetcher=vi.fn().mockRejectedValue(new DOMException('private','AbortError'));vi.stubGlobal('fetch',fetcher);
 await expect(readDecisionContext('task-1',new AbortController().signal)).rejects.toMatchObject({name:'AbortError'});
 fetcher.mockRejectedValueOnce(new Error(SECRET_TEXT));expect(await readDecisionReceipt('task-1','command-1',new AbortController().signal)).toEqual({kind:'dependency_unavailable'});
});
async function reviewed(){
 const user=userEvent.setup();await user.selectOptions(await screen.findByLabelText('Decisão do auditor (obrigatório)'),'APROVAR');
 await user.type(screen.getByLabelText('Justificativa clínica (conforme a decisão)'),SECRET_TEXT);
 await user.click(screen.getByRole('button',{name:'Revisar decisão'}));
 expect(screen.getByRole('heading',{name:'Revise antes de enviar'})).toHaveFocus();
 await user.click(screen.getByRole('checkbox',{name:/Revisei os dados/}));return user;
}
it('pending followup never becomes execution and protected input is removed after admission',async()=>{
 const f=vi.fn().mockResolvedValueOnce(jsonResponse(contextFixture()));vi.stubGlobal('fetch',f);
 const storage=vi.spyOn(Storage.prototype,'setItem');const logs=vi.spyOn(console,'log');const warns=vi.spyOn(console,'warn');
 render(<DecisionWorkspace taskId='task-1' csrfToken='csrf' onSessionUnavailable={vi.fn()}/>);const user=await reviewed();
 f.mockImplementationOnce((_u,o)=>Promise.resolve(jsonResponse(admissionFixture(JSON.parse(o.body).decision.command_id),202)));
 await user.click(screen.getByRole('button',{name:'Enviar decisão'}));await screen.findByText(/Decisão recebida e pendente/);
 expect(document.body.textContent).not.toContain('SYNTHETIC_CLINICAL_REVIEW');
 const body=JSON.parse(f.mock.calls[1][1].body);f.mockResolvedValueOnce(jsonResponse(receiptFixture(body.decision.command_id,'pending')));
 await user.click(screen.getByRole('button',{name:'Consultar recibo'}));await waitFor(()=>expect(f).toHaveBeenCalledTimes(3));
 expect(screen.queryByText(/Execução confirmada/)).not.toBeInTheDocument();expect(storage).not.toHaveBeenCalled();expect(logs).not.toHaveBeenCalled();expect(warns).not.toHaveBeenCalled();
 for(const [url] of f.mock.calls)expect(url).not.toContain('SYNTHETIC_CLINICAL');
});
it('retired context response cannot populate a replacement task',async()=>{
 let resolve!:(r:Response)=>void;const f=vi.fn().mockImplementationOnce(()=>new Promise<Response>(r=>{resolve=r}));vi.stubGlobal('fetch',f);
 const v=render(<DecisionWorkspace key='one' taskId='task-1' csrfToken='csrf' onSessionUnavailable={vi.fn()}/>);
 const other=contextFixture();other.snapshot.task_id='task-2';f.mockResolvedValueOnce(jsonResponse(other));
 v.rerender(<DecisionWorkspace key='two' taskId='task-2' csrfToken='csrf' onSessionUnavailable={vi.fn()}/>);
 await screen.findByRole('button',{name:'Revisar decisão'});await act(async()=>resolve(jsonResponse(contextFixture('pagto_admissibilidade'))));
 expect(screen.queryByRole('region',{name:'Evidência atual de admissibilidade'})).not.toBeInTheDocument();expect(screen.getByLabelText('Decisão do auditor (obrigatório)')).toBeVisible();
 expect(f.mock.calls[0][1].signal.aborted).toBe(true);
});
it('retired send acknowledgment cannot render in replacement task',async()=>{
 const f=vi.fn().mockResolvedValueOnce(jsonResponse(contextFixture()));vi.stubGlobal('fetch',f);const v=render(<DecisionWorkspace key='one' taskId='task-1' csrfToken='csrf' onSessionUnavailable={vi.fn()}/>);const user=await reviewed();
 let resolve!:(r:Response)=>void;f.mockImplementationOnce(()=>new Promise<Response>(r=>{resolve=r}));await user.click(screen.getByRole('button',{name:'Enviar decisão'}));const body=JSON.parse(f.mock.calls[1][1].body);
 const other=contextFixture();other.snapshot.task_id='task-2';f.mockResolvedValueOnce(jsonResponse(other));v.rerender(<DecisionWorkspace key='two' taskId='task-2' csrfToken='csrf' onSessionUnavailable={vi.fn()}/>);await screen.findByRole('button',{name:'Revisar decisão'});
 await act(async()=>resolve(jsonResponse(admissionFixture(body.decision.command_id),202)));
 expect(screen.queryByRole('heading',{name:'Acompanhamento do comando'})).not.toBeInTheDocument();expect(f.mock.calls[1][1].signal.aborted).toBe(true);
});
it('lost acknowledgment retries identical bytes after explicit confirmation without background resubmission',async()=>{
 const f=vi.fn().mockResolvedValueOnce(jsonResponse(contextFixture()));vi.stubGlobal('fetch',f);render(<DecisionWorkspace taskId='task-1' csrfToken='csrf' onSessionUnavailable={vi.fn()}/>);const user=await reviewed();f.mockRejectedValueOnce(new Error(SECRET_TEXT));await user.click(screen.getByRole('button',{name:'Enviar decisão'}));await screen.findByText(/Resultado desconhecido. O envio pode/);
 expect(f).toHaveBeenCalledTimes(2);const raw=f.mock.calls[1][1].body;expect(screen.queryByRole('button',{name:'Revisar decisão'})).not.toBeInTheDocument();f.mockImplementationOnce((_u,o)=>Promise.resolve(jsonResponse(admissionFixture(JSON.parse(o.body).decision.command_id),202)));await user.click(screen.getByRole('button',{name:'Reenviar o mesmo comando'}));await screen.findByText(/Decisão recebida e pendente/);expect(f.mock.calls[2][1].body).toBe(raw);expect(document.body.textContent).not.toContain(SECRET_TEXT);
});
