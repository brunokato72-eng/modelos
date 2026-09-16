import tempfile
import unittest
from pathlib import Path
from unittest import mock

from caderno_financeiro import config, db, ia, upx_sync

CONTA_CORRENTE = {
    "account_id": "acc-checking",
    "category": "checking",
    "display_label": "Conta Corrente",
    "institution_name": "Nubank",
}
CONTA_CREDITO = {
    "account_id": "acc-credit",
    "category": "credit_card",
    "display_label": "platinum",
    "institution_name": "Nubank",
}


def transacao_bruta(
    transaction_id="tx-1",
    account_id="acc-checking",
    data="2026-09-10",
    valor=24.9,
    descricao="UBER TRIP",
    direction="outflow",
    pendente=False,
):
    return {
        "transaction_id": transaction_id,
        "account_id": account_id,
        "posted_date": data,
        "description": descricao,
        "amount": {"amount": valor, "currency_code": "BRL", "formatted": f"R$ {valor}"},
        "direction": direction,
        "is_pending": pendente,
    }


def mock_ferramentas(contas=None, paginas_transacoes=None, investimentos=None):
    """Simula ia.chamar_ferramenta_unica despachando pelo nome da ferramenta
    (a assinatura real é `chamar_ferramenta_unica(ferramenta, argumentos, *,
    modelo=None, timeout=...)`). `paginas_transacoes`: lista de respostas já
    no formato bruto ({"transactions": [...], "pagination": {...}}),
    consumidas em ordem a cada chamada de FERRAMENTA_TRANSACOES — permite
    simular paginação de verdade."""
    paginas = list(paginas_transacoes or [])

    def _chamada(ferramenta, argumentos, *, modelo=None, timeout=None):
        if ferramenta == upx_sync.FERRAMENTA_CONTAS:
            return {"accounts": contas if contas is not None else [CONTA_CORRENTE, CONTA_CREDITO]}
        if ferramenta == upx_sync.FERRAMENTA_TRANSACOES:
            if not paginas:
                return {"transactions": [], "pagination": {"has_more": False}}
            return paginas.pop(0)
        if ferramenta == upx_sync.FERRAMENTA_INVESTIMENTOS:
            return {"investments": investimentos or []}
        raise AssertionError(f"ferramenta inesperada: {ferramenta}")

    return _chamada


def pagina(transacoes, has_more=False, next_cursor=None):
    paginacao = {"has_more": has_more}
    if next_cursor:
        paginacao["next_cursor"] = next_cursor
    return {"transactions": transacoes, "pagination": paginacao}


class TestSincronizar(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def test_transacao_com_confianca_alta_e_gravada_direto(self):
        transacoes = [pagina([transacao_bruta(transaction_id="tx-1", valor=24.9, descricao="UBER TRIP")])]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=mock_ferramentas(paginas_transacoes=transacoes)), \
             mock.patch.object(ia, "categorizar_transacoes", return_value=[{"categoria": "Transporte", "confianca": 0.95}]):
            resultado = upx_sync.sincronizar(conexao)
            lancamentos = db.listar(conexao)

        self.assertEqual(resultado["transacoesNovas"], 1)
        self.assertEqual(resultado["paraRevisao"], 0)
        self.assertEqual(len(lancamentos), 1)
        lanc = lancamentos[0]
        self.assertEqual(lanc["categoria"], "Transporte")
        self.assertEqual(lanc["valor"], 24.9)
        self.assertEqual(lanc["tipo"], "Despesa")
        self.assertEqual(lanc["origem"], "upx")
        self.assertEqual(lanc["origemId"], "tx-1")
        self.assertEqual(lanc["revisaoPendente"], 0)
        self.assertEqual(lanc["conta"], "Conta Corrente")

    def test_transacao_com_confianca_baixa_vai_pra_fila_de_revisao(self):
        transacoes = [pagina([transacao_bruta(transaction_id="tx-2", valor=50.0, descricao="PAG*ALGUEM")])]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=mock_ferramentas(paginas_transacoes=transacoes)), \
             mock.patch.object(ia, "categorizar_transacoes", return_value=[{"categoria": "Pessoal", "confianca": 0.2}]):
            resultado = upx_sync.sincronizar(conexao)
            lancamentos = db.listar(conexao)

        self.assertEqual(resultado["paraRevisao"], 1)
        self.assertEqual(lancamentos[0]["revisaoPendente"], 1)

    def test_conta_credito_vira_cartao_de_credito(self):
        transacoes = [pagina([transacao_bruta(
            transaction_id="tx-3", account_id="acc-credit", valor=39.9, descricao="NETFLIX",
        )])]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=mock_ferramentas(paginas_transacoes=transacoes)), \
             mock.patch.object(ia, "categorizar_transacoes", return_value=[{"categoria": "Assinaturas", "confianca": 0.9}]):
            resultado = upx_sync.sincronizar(conexao)
            lancamentos = db.listar(conexao)

        self.assertEqual(lancamentos[0]["formaPagamento"], config.FORMA_PADRAO)
        self.assertEqual(lancamentos[0]["conta"], "platinum")

    def test_descricao_com_pix_mapeia_forma_pix(self):
        transacoes = [pagina([transacao_bruta(transaction_id="tx-4", valor=30.0, descricao="PIX ENVIADO JOAO")])]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=mock_ferramentas(paginas_transacoes=transacoes)), \
             mock.patch.object(ia, "categorizar_transacoes", return_value=[{"categoria": "Outros", "confianca": 0.4}]):
            resultado = upx_sync.sincronizar(conexao)
            lancamentos = db.listar(conexao)

        self.assertEqual(lancamentos[0]["formaPagamento"], "Pix")

    def test_receita_quando_direction_e_inflow(self):
        transacoes = [pagina([transacao_bruta(
            transaction_id="tx-r1", valor=3000.0, descricao="SALARIO", direction="inflow",
        )])]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=mock_ferramentas(paginas_transacoes=transacoes)), \
             mock.patch.object(ia, "categorizar_transacoes", return_value=[{"categoria": "Salário", "confianca": 0.9}]):
            resultado = upx_sync.sincronizar(conexao)
            lancamentos = db.listar(conexao)

        self.assertEqual(resultado["transacoesNovas"], 1)
        self.assertEqual(lancamentos[0]["tipo"], config.TIPO_RECEITA)

    def test_pendente_em_conta_comum_e_descartada(self):
        transacoes = [pagina([
            transacao_bruta(transaction_id="tx-p1", account_id="acc-checking", pendente=True),
            transacao_bruta(transaction_id="tx-p2", account_id="acc-checking", pendente=False),
        ])]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=mock_ferramentas(paginas_transacoes=transacoes)), \
             mock.patch.object(ia, "categorizar_transacoes", return_value=[{"categoria": "Outros", "confianca": 0.9}]):
            resultado = upx_sync.sincronizar(conexao)

        self.assertEqual(resultado["transacoesNovas"], 1)

    def test_pendente_em_cartao_de_credito_e_mantida(self):
        transacoes = [pagina([
            transacao_bruta(transaction_id="tx-p3", account_id="acc-credit", pendente=True),
        ])]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=mock_ferramentas(paginas_transacoes=transacoes)), \
             mock.patch.object(ia, "categorizar_transacoes", return_value=[{"categoria": "Outros", "confianca": 0.9}]):
            resultado = upx_sync.sincronizar(conexao)

        self.assertEqual(resultado["transacoesNovas"], 1)

    def test_pagamento_de_fatura_e_descartado(self):
        """'Pagamento de fatura' (saída da conta corrente pra quitar o
        cartão) duplicaria o gasto já contado nas compras individuais que
        geraram a fatura — não deve virar lançamento."""
        transacoes = [pagina([
            transacao_bruta(transaction_id="tx-fat-1", descricao="Pagamento de fatura", valor=3000.0),
            transacao_bruta(transaction_id="tx-normal-1", descricao="Uber"),
        ])]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=mock_ferramentas(paginas_transacoes=transacoes)), \
             mock.patch.object(ia, "categorizar_transacoes", return_value=[{"categoria": "Outros", "confianca": 0.9}]):
            resultado = upx_sync.sincronizar(conexao)
            lancamentos = db.listar(conexao)

        self.assertEqual(resultado["transacoesNovas"], 1)
        self.assertEqual(lancamentos[0]["descricao"], "Uber")

    def test_pagamento_recebido_no_cartao_de_credito_e_descartado(self):
        """'Pagamento recebido' na própria conta do cartão de crédito é a
        entrada que quita o saldo devedor — a outra perna de 'Pagamento de
        fatura'. Infla a receita se não for descartada."""
        transacoes = [pagina([
            transacao_bruta(
                transaction_id="tx-rec-1", account_id="acc-credit",
                descricao="Pagamento recebido", valor=3000.0, direction="inflow",
            ),
        ])]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=mock_ferramentas(paginas_transacoes=transacoes)), \
             mock.patch.object(ia, "categorizar_transacoes", return_value=[{"categoria": "Outros", "confianca": 0.9}]):
            resultado = upx_sync.sincronizar(conexao)

        self.assertEqual(resultado["transacoesNovas"], 0)

    def test_pagamento_recebido_fora_do_cartao_de_credito_e_mantido(self):
        """'Pagamento recebido' numa conta que não é cartão de crédito não é
        a quitação de fatura — é uma receita legítima, mantém."""
        transacoes = [pagina([
            transacao_bruta(
                transaction_id="tx-rec-2", account_id="acc-checking",
                descricao="Pagamento recebido", valor=500.0, direction="inflow",
            ),
        ])]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=mock_ferramentas(paginas_transacoes=transacoes)), \
             mock.patch.object(ia, "categorizar_transacoes", return_value=[{"categoria": "Reembolso", "confianca": 0.9}]):
            resultado = upx_sync.sincronizar(conexao)

        self.assertEqual(resultado["transacoesNovas"], 1)

    def test_pagina_todas_as_paginas_ate_has_more_ser_falso(self):
        """Reproduz o cenário que antes travava o modelo (várias páginas) —
        agora é o Python que segue o cursor, uma chamada isolada por página."""
        transacoes = [
            pagina([transacao_bruta(transaction_id="tx-a")], has_more=True, next_cursor="cursor-2"),
            pagina([transacao_bruta(transaction_id="tx-b")], has_more=True, next_cursor="cursor-3"),
            pagina([transacao_bruta(transaction_id="tx-c")], has_more=False),
        ]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=mock_ferramentas(paginas_transacoes=transacoes)), \
             mock.patch.object(ia, "categorizar_transacoes", return_value=[
                 {"categoria": "Outros", "confianca": 0.9},
                 {"categoria": "Outros", "confianca": 0.9},
                 {"categoria": "Outros", "confianca": 0.9},
             ]):
            resultado = upx_sync.sincronizar(conexao)

        self.assertEqual(resultado["transacoesNovas"], 3)

    def test_transacao_ja_importada_nao_e_reimportada(self):
        transacoes = [pagina([transacao_bruta(transaction_id="tx-5", valor=10.0, descricao="UBER")])]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=mock_ferramentas(paginas_transacoes=transacoes)), \
             mock.patch.object(ia, "categorizar_transacoes", return_value=[{"categoria": "Transporte", "confianca": 0.9}]):
            upx_sync.sincronizar(conexao)

        transacoes_2 = [pagina([transacao_bruta(transaction_id="tx-5", valor=10.0, descricao="UBER")])]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=mock_ferramentas(paginas_transacoes=transacoes_2)), \
             mock.patch.object(ia, "categorizar_transacoes") as ia_mock:
            resultado = upx_sync.sincronizar(conexao)
            total = db.contar(conexao)

        self.assertEqual(resultado["transacoesNovas"], 0)
        self.assertEqual(total, 1)
        ia_mock.assert_not_called()

    def test_transacao_que_bate_com_lancamento_manual_nao_e_duplicada(self):
        """Se o usuário já tinha digitado esse gasto na mão (mesmo valor/data,
        ±1 dia), a sincronização não deve importar de novo como uma segunda
        transação — só contar como duplicata."""
        with db.banco(self.banco) as conexao:
            db.inserir(conexao, [{
                "id": "manual-1", "data": "2026-09-10", "tipo": "Despesa", "categoria": "Mercado",
                "valor": 89.9, "valorTotal": 89.9, "parcelaAtual": 1, "totalParcelas": 1,
                "formaPagamento": "Pix", "conta": "Nubank", "descricao": "compras",
                "criadoEm": "2026-09-10T09:00:00", "grupoParcelamento": None,
            }])
        transacoes = [pagina([transacao_bruta(
            transaction_id="tx-7", valor=89.9, descricao="SUPERMERCADO XYZ",
        )])]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=mock_ferramentas(paginas_transacoes=transacoes)), \
             mock.patch.object(ia, "categorizar_transacoes") as ia_mock:
            resultado = upx_sync.sincronizar(conexao)
            total = db.contar(conexao)

        self.assertEqual(resultado["transacoesNovas"], 0)
        self.assertEqual(resultado["duplicadas"], 1)
        self.assertEqual(total, 1)  # continua só o lançamento manual, não duplicou
        ia_mock.assert_not_called()

    def test_transacao_sem_id_e_ignorada_sem_quebrar(self):
        bruta_sem_id = transacao_bruta(descricao="sem id")
        del bruta_sem_id["transaction_id"]
        transacoes = [pagina([bruta_sem_id, transacao_bruta(transaction_id="tx-6", valor=20.0, descricao="com id")])]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=mock_ferramentas(paginas_transacoes=transacoes)), \
             mock.patch.object(ia, "categorizar_transacoes", return_value=[{"categoria": "Outros", "confianca": 0.9}]):
            resultado = upx_sync.sincronizar(conexao)

        self.assertEqual(resultado["transacoesNovas"], 1)

    def test_sempre_usa_janela_fixa_de_meses_por_padrao(self):
        """Não existe mais um "desde a última sincronização" que avança — isso
        faria uma transação pendente numa sincronização (e que só assenta dias
        depois) nunca mais ser vista. A busca sempre olha os últimos
        `JANELA_MESES` meses; quem evita duplicar é o dedup por origem_id."""
        chamadas = []

        def _espiao(ferramenta, argumentos, *, modelo=None, timeout=None):
            if ferramenta == upx_sync.FERRAMENTA_TRANSACOES:
                chamadas.append(argumentos)
            return mock_ferramentas()(ferramenta, argumentos, modelo=modelo, timeout=timeout)

        with mock.patch.object(upx_sync, "hoje_iso", return_value="2026-09-15"), \
             db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=_espiao):
            upx_sync.sincronizar(conexao)

        desde_esperado = upx_sync.somar_meses("2026-09-15", -upx_sync.JANELA_MESES)
        self.assertEqual(chamadas[0]["from"], desde_esperado)
        self.assertEqual(chamadas[0]["to"], "2026-09-15")

    def test_nao_grava_mais_data_de_ultima_sincronizacao(self):
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=mock_ferramentas()):
            upx_sync.sincronizar(conexao)
            self.assertIsNone(db.ler_config(conexao, "upx_ultima_sincronizacao"))

    def test_desde_e_ate_sobrescrevem_a_janela_padrao(self):
        """Pra uma carga única de histórico antigo (ou incluindo parcelas
        futuras), dá pra passar desde/ate explícitos em vez da janela padrão
        de JANELA_MESES até hoje."""
        chamadas = []

        def _espiao(ferramenta, argumentos, *, modelo=None, timeout=None):
            if ferramenta == upx_sync.FERRAMENTA_TRANSACOES:
                chamadas.append(argumentos)
            return mock_ferramentas()(ferramenta, argumentos, modelo=modelo, timeout=timeout)

        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=_espiao):
            upx_sync.sincronizar(conexao, desde="2020-01-01", ate="2027-12-31")

        self.assertEqual(chamadas[0]["from"], "2020-01-01")
        self.assertEqual(chamadas[0]["to"], "2027-12-31")


class TestSincronizarInvestimentos(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"

    def test_grava_snapshot_das_posicoes(self):
        investimentos = [
            {"holding_id": "h1", "institution_name": "Clear", "name": "PETR4", "category": "equity",
             "value": {"amount": 500.0}, "quantity": 20},
            {"holding_id": "h2", "institution_name": "Clear", "name": "Tesouro Selic", "category": "fixed_income",
             "value": {"amount": 1000.0}, "quantity": None},
        ]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=mock_ferramentas(investimentos=investimentos)):
            total = upx_sync.sincronizar_investimentos(conexao)
            gravadas = db.listar_posicoes_investimento(conexao)

        self.assertEqual(total, 2)
        self.assertEqual(len(gravadas), 2)
        valores = {p["nome"]: p["valor"] for p in gravadas}
        self.assertEqual(valores["PETR4"], 500.0)
        self.assertEqual(valores["Tesouro Selic"], 1000.0)

    def test_sem_posicoes_devolve_zero(self):
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=mock_ferramentas(investimentos=[])):
            total = upx_sync.sincronizar_investimentos(conexao)
        self.assertEqual(total, 0)

    def test_posicao_sem_id_e_ignorada_sem_quebrar(self):
        investimentos = [
            {"institution_name": "Clear", "name": "sem id", "category": "equity", "value": {"amount": 100.0}, "quantity": 1},
            {"holding_id": "h3", "institution_name": "Clear", "name": "com id", "category": "equity",
             "value": {"amount": 200.0}, "quantity": 2},
        ]
        with db.banco(self.banco) as conexao, \
             mock.patch.object(ia, "chamar_ferramenta_unica", side_effect=mock_ferramentas(investimentos=investimentos)):
            total = upx_sync.sincronizar_investimentos(conexao)
        self.assertEqual(total, 1)


if __name__ == "__main__":
    unittest.main()
