"""A cerca que reprova um plano do Terraform que apaga o portal sem ninguem ter pedido.

Cada teste aqui nasceu de uma pergunta que eu precisei responder para acreditar na cerca:
ela pega o acidente de verdade? ela deixa o uso normal passar? ela fica cega se o
endereco mudar de forma? Um "OK" dela so' vale se as tres respostas forem sim.
"""

from __future__ import annotations

import json

import pytest
from scripts.ci.checar_plano_sem_destroy_do_portal import destruicoes, main


def _plano(*mudancas: dict) -> dict:
    return {"format_version": "1.2", "resource_changes": list(mudancas)}


def _mudanca(endereco: str, *acoes: str) -> dict:
    return {"address": endereco, "type": endereco.split(".")[-2], "change": {"actions": list(acoes)}}


def test_o_acidente_de_verdade_reprova():
    """O caso que motivou tudo: apply de um checkout sem `portal.auto.tfvars`.

    Com `var.portal` nulo o `for_each` fica vazio e o Terraform planeja apagar o portal
    inteiro. O que precisa aparecer aqui e' o security group, porque o id dele esta'
    referenciado no state de OUTRO repositorio.
    """
    plano = _plano(
        _mudanca('aws_security_group.portal["this"]', "delete"),
        _mudanca('aws_security_group.portal_ingress["this"]', "delete"),
        _mudanca('aws_lb.portal["this"]', "delete"),
        _mudanca('aws_iam_role.portal_task["this"]', "delete"),
    )
    achados = destruicoes(plano)
    assert [endereco for endereco, _ in achados] == [
        'aws_security_group.portal["this"]',
        'aws_security_group.portal_ingress["this"]',
        'aws_lb.portal["this"]',
        'aws_iam_role.portal_task["this"]',
    ]


def test_o_uso_normal_passa():
    """Trocar a imagem e desligar o portal sao movimento de rotina, e tem de passar.

    Este e' o teste que impede a cerca de virar pedra no caminho: a task definition e'
    SUBSTITUIDA a cada digest novo, e `portal_enabled=false` escala o servico a zero —
    `service-portal.tf:291`, que e' `desired_count`, nao destruicao. Uma cerca que
    reprova o uso correto e' desligada pelo time na terceira vez.
    """
    plano = _plano(
        _mudanca('aws_ecs_task_definition.portal["this"]', "create", "delete"),
        _mudanca('aws_ecs_service.portal["this"]', "update"),
        _mudanca("aws_security_group.outra_coisa", "delete"),
    )
    assert destruicoes(plano) == []


def test_substituicao_de_recurso_duravel_conta_como_destruicao():
    """Substituir um SG tambem invalida o id que o outro state guarda.

    `create`+`delete` parece inofensivo porque "continua existindo um SG". Nao continua
    existindo o MESMO: a regra de ingress do Aurora, em `amh-data-platform`, aponta para
    um id que deixou de existir. Por isso a cerca olha a presenca de `delete` e nao a
    forma da lista.
    """
    assert destruicoes(_plano(_mudanca('aws_security_group.portal["this"]', "create", "delete")))
    assert destruicoes(_plano(_mudanca('aws_security_group.portal["this"]', "delete", "create")))


def test_endereco_dentro_de_modulo_indexado_continua_sendo_visto():
    """Se o portal virar modulo, a cerca nao pode emudecer.

    O indice pode estar no NOME DO MODULO — `module.portal["dev"]` — e um recorte
    ingenuo no primeiro `[` devolveria `module.portal`, que nao casa com nada da lista.
    A cerca passaria a dizer "nenhum destroy" para um plano que apaga o portal, que e'
    o pior defeito possivel numa cerca: ficar verde sem olhar.
    """
    plano = _plano(_mudanca('module.portal["dev"].aws_lb.portal[0]', "delete"))
    assert [e for e, _ in destruicoes(plano)] == ['module.portal["dev"].aws_lb.portal[0]']


def test_plano_vazio_passa():
    assert destruicoes(_plano()) == []
    assert destruicoes({}) == []


def test_saida_do_processo_reprova_e_explica(tmp_path, capsys):
    caminho = tmp_path / "plano.json"
    caminho.write_text(json.dumps(_plano(_mudanca('aws_lb_target_group.portal["this"]', "delete"))))
    assert main([str(caminho)]) == 1
    erro = capsys.readouterr().err
    assert 'aws_lb_target_group.portal["this"]' in erro
    # A mensagem tem de levar a pessoa a' CAUSA, nao so' dizer que reprovou.
    assert "portal.auto.tfvars" in erro


def test_destruir_de_proposito_e_possivel_mas_precisa_ser_escrito(tmp_path):
    caminho = tmp_path / "plano.json"
    caminho.write_text(json.dumps(_plano(_mudanca('aws_lb.portal["this"]', "delete"))))
    assert main([str(caminho)]) == 1
    assert main([str(caminho), "--permitir-destruir"]) == 0


@pytest.mark.parametrize("conteudo", ["", "{nao e json"])
def test_plano_ilegivel_reprova_em_vez_de_passar(tmp_path, conteudo):
    """Nao saber nao e' o mesmo que estar tudo bem.

    Um arquivo truncado, um `terraform show` que falhou e gravou nada: se isso virasse
    "nenhum destroy encontrado", a cerca passaria exatamente quando menos se sabe.
    """
    caminho = tmp_path / "plano.json"
    caminho.write_text(conteudo)
    assert main([str(caminho)]) == 2


def test_arquivo_ausente_reprova(tmp_path):
    assert main([str(tmp_path / "nao-existe.json")]) == 2
