"""NOMES DE MEDICAMENTOS COMUNS — VOCABULARIO para a checagem da saude gerativa (fase 3 de 18/10/2026).

O QUE E'. Uma lista CURADA e PEQUENA de nomes de principios ativos (Denominacao Comum Brasileira) e de nomes
comerciais muito comuns no Brasil, so' para a CHECAGEM do texto redigido pegar "tome/use/de <remedio>", dose
esquecida e combinacao de remedios. NUNCA vira resposta, nunca vai ao modelo, nunca e' conteudo clinico.

FONTE (classe "so' vocabulario / mapeamento" da lista fechada, `spec/conhecimento/saude/FONTES.md` §3): dados
abertos de medicamentos da ANVISA (`dados.anvisa.gov.br`, "Medicamentos registrados no Brasil", colunas de
principio ativo e nome do produto) e a lista de Denominacoes Comuns Brasileiras (DCB) da ANVISA. Curadoria
manual em 18/10/2026 (Claude, a pedido do dono): so' os nomes de uso comum em atencao primaria e
automedicacao.
A conferencia linha a linha contra o arquivo CSV dos dados abertos fica para a revisao de 6 meses
(`revisar_ate` abaixo). Nome que falta so' deixa de ser pego pela checagem — as regras de verbo ("tome",
"pode tomar", "use o dobro", dose numerica) continuam valendo sem o nome.

Nomes NORMALIZADOS (minusculas, sem acento), um por entrada; variantes de grafia comuns entram juntas.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Final

FONTE: Final[str] = "ANVISA — dados abertos de medicamentos registrados e lista DCB (vocabulario)"
COLETADO_EM: Final[date] = date(2026, 10, 18)
REVISAR_ATE: Final[date] = date(2027, 4, 18)

PRINCIPIOS_ATIVOS: Final[frozenset[str]] = frozenset(
    {
        *("acetaminofeno", "paracetamol", "dipirona", "metamizol", "ibuprofeno", "naproxeno", "diclofenaco"),
        *(
            "cetoprofeno",
            "nimesulida",
            "acido acetilsalicilico",
            "aas",
            "piroxicam",
            "meloxicam",
            "celecoxibe",
        ),
        *("tramadol", "codeina", "morfina", "ciclobenzaprina", "orfenadrina", "carisoprodol", "escopolamina"),
        *("butilbrometo de escopolamina", "dimenidrinato", "metoclopramida", "bromoprida", "ondansetrona"),
        *(
            "domperidona",
            "omeprazol",
            "pantoprazol",
            "esomeprazol",
            "lansoprazol",
            "ranitidina",
            "simeticona",
        ),
        *("loperamida", "bisacodil", "lactulose", "hidroxido de aluminio", "hidroxido de magnesio"),
        *("loratadina", "desloratadina", "cetirizina", "levocetirizina", "fexofenadina", "dexclorfeniramina"),
        *("prometazina", "hidroxizina", "amoxicilina", "clavulanato", "azitromicina", "claritromicina"),
        *("cefalexina", "ceftriaxona", "ciprofloxacino", "levofloxacino", "norfloxacino", "nitrofurantoina"),
        *("fosfomicina", "sulfametoxazol", "trimetoprima", "metronidazol", "doxiciclina", "penicilina"),
        *("benzetacil", "fluconazol", "nistatina", "cetoconazol", "miconazol", "aciclovir", "valaciclovir"),
        *(
            "oseltamivir",
            "ivermectina",
            "albendazol",
            "mebendazol",
            "nitazoxanida",
            "prednisona",
            "prednisolona",
        ),
        *("dexametasona", "betametasona", "hidrocortisona", "budesonida", "salbutamol", "fenoterol"),
        *("ipratropio", "formoterol", "salmeterol", "montelucaste", "losartana", "captopril", "enalapril"),
        *("valsartana", "olmesartana", "anlodipino", "nifedipino", "hidroclorotiazida", "clortalidona"),
        *(
            "furosemida",
            "espironolactona",
            "atenolol",
            "propranolol",
            "metoprolol",
            "carvedilol",
            "bisoprolol",
        ),
        *("metformina", "glibenclamida", "gliclazida", "glimepirida", "insulina", "sitagliptina"),
        *("dapagliflozina", "empagliflozina", "semaglutida", "liraglutida", "sinvastatina", "atorvastatina"),
        *("rosuvastatina", "levotiroxina", "varfarina", "rivaroxabana", "apixabana", "clopidogrel"),
        *("fluoxetina", "sertralina", "escitalopram", "citalopram", "paroxetina", "venlafaxina"),
        *("desvenlafaxina", "duloxetina", "amitriptilina", "nortriptilina", "bupropiona", "trazodona"),
        *("clonazepam", "diazepam", "alprazolam", "lorazepam", "bromazepam", "zolpidem", "quetiapina"),
        *(
            "risperidona",
            "olanzapina",
            "haloperidol",
            "litio",
            "carbamazepina",
            "valproato",
            "acido valproico",
        ),
        *("lamotrigina", "topiramato", "gabapentina", "pregabalina", "fenitoina", "fenobarbital"),
        *("metilfenidato", "sumatriptana", "naratriptana", "anticoncepcional", "levonorgestrel"),
        *("etinilestradiol", "drospirenona", "desogestrel", "medroxiprogesterona", "progesterona"),
        *("estradiol", "sildenafila", "tadalafila", "finasterida", "tansulosina", "acido folico"),
        *("sulfato ferroso", "vitamina d", "colecalciferol", "vitamina b12", "cianocobalamina"),
        *("alendronato", "colchicina", "alopurinol", "hidroxicloroquina", "cloroquina", "metotrexato"),
        *("isotretinoina", "minoxidil", "permetrina", "neomicina", "mupirocina", "clotrimazol"),
        *("nafazolina", "oximetazolina", "soro fisiologico", "ambroxol", "acetilcisteina", "bromexina"),
        *("guaifenesina", "dextrometorfano", "sais de reidratacao oral", "soro de reidratacao"),
    }
)
NOMES_COMERCIAIS: Final[frozenset[str]] = frozenset(
    {
        *("tylenol", "novalgina", "anador", "dorflex", "buscopan", "neosaldina", "advil", "alivium"),
        *("cataflam", "voltaren", "nisulid", "aspirina", "melhoral", "torsilax", "dramin", "plasil"),
        *("engov", "estomazil", "luftal", "dipirona sodica", "rivotril", "frontal", "lexotan"),
        *("allegra", "claritin", "polaramine", "decadron", "predsim", "aerolin", "berotec"),
        *("glifage", "puran", "zoloft", "prozac", "lexapro", "viagra", "cialis", "amoxil", "zitromax"),
        *("tamiflu", "annita", "benegrip", "resfenol", "cimegripe", "coristina", "vick", "sorine"),
    }
)
NOMES: Final[frozenset[str]] = PRINCIPIOS_ATIVOS | NOMES_COMERCIAIS

#: Um nome da lista, inteiro, no texto NORMALIZADO (os mais longos primeiro: "acido valproico" antes de
#: "acido").
PADRAO_DE_NOME: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w])(?:" + "|".join(re.escape(n) for n in sorted(NOMES, key=len, reverse=True)) + r")(?![\w])"
)


def nomes_no_texto(plano: str) -> tuple[str, ...]:
    """Os nomes da lista que aparecem no texto JA NORMALIZADO, na ordem, sem repetir."""
    vistos: dict[str, None] = {}
    for m in PADRAO_DE_NOME.finditer(plano):
        vistos.setdefault(m.group(0), None)
    return tuple(vistos)


__all__ = [
    "COLETADO_EM",
    "FONTE",
    "NOMES",
    "NOMES_COMERCIAIS",
    "PADRAO_DE_NOME",
    "PRINCIPIOS_ATIVOS",
    "REVISAR_ATE",
    "nomes_no_texto",
]
