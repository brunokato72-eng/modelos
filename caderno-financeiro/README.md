# Caderno Financeiro

Registro de gastos por texto livre, estatísticas locais e perguntas em linguagem
natural sobre os próprios dados — rodando na sua máquina, em cima de um SQLite, e
usando **o login da sua assinatura Claude** (sem chave de API paga).

Substitui o agente do n8n: mesma ideia (mandar "gastei 45,90 no mercado ontem" e
o resto se resolve), sem depender de conexão que cai nem de low-code pra ajustar.

---

## 1. O requisito não-negociável: autenticação

**Não há nenhuma leitura de `ANTHROPIC_API_KEY` neste projeto.** Toda chamada de
modelo passa pelo binário `claude` em modo headless (`claude -p`), que usa a
sessão já autenticada da sua assinatura — o mesmo login do `claude` interativo.

Confira a qualquer momento:

```bash
caderno auth
```

```
Autenticação
  CLI claude: /opt/node22/bin/claude
  versão:     2.1.251 (Claude Code)
  chave de API no ambiente: nenhuma (usa a assinatura)
  chamada de teste: OK ('pronto')
```

Se `ANTHROPIC_API_KEY` estiver setada no seu ambiente, o comando avisa em
amarelo: com ela presente o CLI cobra por token em vez de usar a assinatura.
Nesse caso, `unset ANTHROPIC_API_KEY` antes de usar o caderno.

Cada chamada roda isolada de propósito (`ia.py`): prompt de sistema próprio,
`--tools ""`, `--strict-mcp-config`, sem persistência de sessão e com o processo
rodando num diretório vazio. Isso derruba o contexto de ~38k tokens do Claude
Code padrão pra ~1k por chamada, e garante que o modelo não tem acesso a nenhum
arquivo seu.

## 2. Instalação

Requisitos: Python 3.9+ e o CLI `claude` logado com a assinatura.

```bash
cd caderno-financeiro
python3 -m caderno_financeiro init        # cria ~/.caderno-financeiro/caderno.db
```

Para ter o comando curto `caderno` no PATH:

```bash
pip install -e .            # opcional; instala o entry point `caderno`
pip install -e ".[xlsx]"    # idem, com suporte a importar .xlsx
pip install -e ".[web]"     # idem, com o servidor web pro acesso remoto (seção 8)
```

Sem instalar, todo comando deste README funciona trocando `caderno` por
`python3 -m caderno_financeiro`.

Variáveis de ambiente úteis:

| variável | efeito |
|---|---|
| `CADERNO_DB` | caminho do banco (padrão `~/.caderno-financeiro/caderno.db`) |
| `CADERNO_MODELO_EXTRACAO` | modelo das tarefas estruturadas (padrão `haiku`) |
| `CADERNO_MODELO_ANALISE` | modelo que escreve a análise (padrão `sonnet`) |

## 3. Uso

### Registrar (texto)

```bash
caderno registrar "45,90 no mercado ontem"
caderno registrar "sofá de 2400 em 6x na casas bahia"
caderno registrar "recebi 5200 de salário hoje no itaú"
caderno registrar "22 no uber e 50 na farmácia" -s     # -s salva sem perguntar
caderno registrar "almoço 32" --simular                # mostra e não grava
```

Ele mostra o que entendeu antes de salvar:

```
Despesa: Sofá Casas Bahia
  categoria:  Compras
  valor:      R$ 2.400,00 em 6x de R$ 400,00
  pagamento:  Cartão de crédito · Nubank
  data:       2026-08-31
  parcelas:   2026-08-31 R$ 400,00, 2026-09-30 R$ 400,00, ... até 2027-01-31
```

### Registrar (voz)

```bash
caderno registrar --audio ~/audio.m4a
```

A transcrição roda **na sua máquina** (nada de áudio sai daqui) e depende de um
transcritor local opcional: `pip install faster-whisper`. Sem ele, o comando
explica isso e o registro por texto segue funcionando.

### Estatísticas (nenhuma IA, custo zero)

```bash
caderno resumo                    # mês atual
caderno resumo --mes 2026-07
caderno listar --mes 2026-08 --categoria Mercado
caderno calcular soma --de 2026-01 --ate 2026-08 -t Despesa --agrupar mes
caderno calcular media -c Alimentação
```

### Perguntar / conversar

```bash
caderno perguntar "quanto eu gastei em agosto e onde foi o maior gasto?"
caderno perguntar "minha média de mercado nos últimos 3 meses" --detalhes
caderno chat        # mantém o contexto entre as perguntas da sessão
```

`--detalhes` mostra exatamente quais cálculos foram executados por trás da
resposta — útil pra auditar qualquer número.

### Importar e exportar

```bash
caderno importar historico.csv --simular   # confere colunas e duplicatas
caderno importar historico.csv
caderno importar planilha.xlsx             # requer openpyxl
caderno exportar backup.csv
caderno exportar -                         # pra stdout
```

## 4. Regras de negócio (código puro, nunca a IA)

A IA extrai **só o que a mensagem diz** e devolve `null` no que não foi dito.
Tudo abaixo acontece depois, em Python, sempre igual:

1. Nem forma de pagamento nem conta mencionadas → `Cartão de crédito` + `Nubank`.
2. Conta = `Ifood` → forma de pagamento `VR` (tem prioridade sobre a regra 1).
3. O que foi dito explicitamente é respeitado; as regras acima só preenchem branco.

> Um caso que o briefing não fecha explicitamente: só um dos dois campos vem
> mencionado (ex.: "no pix" sem dizer o banco, ou conta "Inter" sem dizer a
> forma). A leitura adotada é a distributiva da regra 3 ("as regras acima só
> preenchem o que ficou em branco"): cada campo em branco recebe o próprio
> padrão da regra 1 — forma ausente vira `Cartão de crédito`, conta ausente
> vira `Nubank` — independente do outro campo ter sido dito. Nenhum lançamento
> fica sem forma ou sem conta por causa disso. Está isolado em
> `regras.aplicar_regras` se você quiser outro comportamento.

**Parcelamento** (`regras.dividir_parcelas` + `datas.somar_meses`): o valor total
é dividido **em centavos inteiros** e a sobra vai pra última parcela, então a soma
das parcelas bate exatamente com o total — sempre. O dia é clampado pro último dia
de meses curtos: compra em 31/01 gera parcela em 28/02 (29/02 em ano bissexto),
nunca 01/03.

**Import**: colunas detectadas por nome ignorando acento, caixa e separador
(`Forma de Pagamento`, `forma_pagamento` e `FORMA-PAGAMENTO` são a mesma coisa);
categorias normalizadas por acento/caixa/alias (`Alimentacao` → `Alimentação`,
`Outros_Receita` → `Outras entradas`); valores em `R$ 1.234,56` ou `1234.56`;
datas em ISO, `dd/mm/aaaa` ou número de série do Excel. Duplicata = **mesmo valor
+ data a até 1 dia de distância**, checada contra o banco e dentro do próprio
arquivo.

## 5. Como os números são calculados

Nenhum número sai do modelo. Existe um só lugar onde conta vira conta:
`calculadora.executar_calculo`, porte da função já validada no protótipo.

Operações: `soma`, `media`, `mediana`, `desviopadrao`, `minimo`, `maximo`,
`contagem`, `listar` (top 20 por valor). Filtros: período (`mesInicio`/`mesFim`),
tipo, categoria, forma de pagamento, conta — mais duas extensões: trecho da
descrição (`descricaoContem`) e agrupamento (`agruparPor`: categoria, forma,
conta, tipo, mês). Somas são feitas em centavos inteiros, sem erro de float.

Existem dois caminhos até esse calculador, e **os dois calculam em Python**:

**Modo `manual`** (o fallback validado, 3 passos):
1. IA traduz a pergunta numa lista de filtros/operações (JSON, sem fazer conta);
2. o código executa a agregação sobre os dados reais;
3. a IA recebe só o resultado pronto e escreve a resposta em cima dele.

**Modo `toolcall`** (function calling nativo): um servidor MCP local
(`mcp_calculadora.py`, protocolo JSON-RPC escrito na mão, sem dependência) expõe
a ferramenta `calcular`. O modelo chama a ferramenta; ela roda **neste processo**,
lê o SQLite e devolve o número pronto.

### O bug do artefato, e como ele é impedido aqui

No artefato do navegador o modelo *simulava* a chamada de ferramenta como texto
(`<tool_call>...</tool_call>`) e inventava números. Aqui isso é testado, não
assumido:

```bash
caderno testar-toolcall
```

O teste monta um banco temporário com valores propositais (111,11 + 222,22 +
333,33 = 666,66), faz a pergunta e só passa se **as duas** coisas forem verdade:
a ferramenta foi realmente executada (o servidor MCP grava um log de auditoria de
cada chamada recebida) **e** o valor certo apareceu na resposta. Resultado neste
ambiente:

```
  ferramenta executada de verdade: sim
  chamadas registradas: 1
    {"operacao": "soma", "tipo": "Despesa", "mesInicio": "2024-02", "mesFim": "2024-02"}
  resposta do modelo: O total de despesas em fevereiro de 2024 foi R$ 666,66...
  OK — o modo toolcall vai ser usado por padrão.
```

O resultado fica gravado no banco: `--modo auto` (padrão) usa `toolcall` só
depois de ter passado no teste, e cai pro `manual` caso contrário. Mesmo em
`toolcall`, se uma resposta vier **sem nenhuma chamada registrada** no servidor
MCP, ela é descartada e a pergunta é refeita pelo caminho manual — resposta com
número simulado nunca chega até você.

## 6. Estrutura

```
caderno_financeiro/
  config.py           categorias, formas de pagamento, padrões, caminho do banco
  texto.py            normalizeStr (acento/caixa) portado do protótipo
  datas.py            addMonths com clamp de fim de mês, validações
  valores.py          dinheiro em centavos, parse de "R$ 1.234,56"
  regras.py           regras determinísticas + expansão de parcelas
  db.py               esquema SQLite, CRUD, checagem de duplicata
  calculadora.py      executarCalculo — o único lugar que faz conta
  estatisticas.py     resumo mensal, quebras, parcelas futuras (sem IA)
  ia.py               ponte com o CLI claude (extração / plano / redação)
  mcp_calculadora.py  servidor MCP stdio expondo `calcular`
  consulta.py         modos manual e toolcall + teste do function calling
  registro.py         texto/voz -> extração -> regras -> parcelas -> banco
  importador.py       CSV/XLSX: detecção de colunas, normalização, dedup
  exportador.py       backup CSV (relegível pelo próprio importador)
  auth.py             PIN + token de sessão do servidor web
  servidor.py         API HTTP (Flask) + arquivos do PWA — acesso remoto
  cli.py              comandos
web/                  PWA: index.html, app.js, styles.css, manifest, ícones
tests/                98 testes, nenhum deles chama o modelo de verdade
exemplos/             CSV de exemplo no formato do histórico
```

Testes:

```bash
python3 -m unittest discover -s tests
```

## 7. Trazer o histórico

```bash
caderno importar ~/Downloads/planilha-antiga.xlsx --simular   # confere
caderno importar ~/Downloads/planilha-antiga.xlsx
caderno importar ~/Downloads/lancamentos-novos.csv            # 131 linhas, 21/07–29/08
caderno exportar ~/backup-inicial.csv                         # backup logo depois
```

Importe a planilha antiga primeiro e o CSV novo depois: a checagem de duplicata
(mesmo valor + data ±1 dia) cuida da sobreposição entre os dois. Rode sempre com
`--simular` antes pra ver as colunas detectadas e quantas linhas seriam
descartadas como duplicata; se a detecção errar alguma coluna, o relatório mostra
qual cabeçalho foi pra qual campo.

Um efeito colateral esperado da regra de duplicata: dois gastos legítimos de
mesmo valor em dias vizinhos (dois cafés de R$ 20 seguidos) contam como
duplicata. Nesses casos, `--tolerancia 0` (exige data idêntica) ou `--sem-dedup`.

## 8. Acesso remoto (celular, via PWA + Tailscale)

Passo 10 do briefing. A arquitetura escolhida foi a mais simples das duas
cogitadas: **sua própria máquina fica ligada**, e o celular acessa por um túnel
— nunca pela internet aberta, e a credencial da assinatura nunca sai daqui. Quem
fala com o Claude continua sendo o CLI `claude` local (`ia.py`), exatamente como
no uso por linha de comando; o servidor web só expõe a lógica que já existia
(`registro`, `consulta`, `estatisticas` não sabem que existe HTTP).

### Peças

- **`caderno servir`** — sobe uma API HTTP local (Flask) + o PWA em
  `web/`. Roda na sua máquina, escuta em todas as interfaces por padrão.
- **PIN de acesso** — segunda camada de proteção além do túnel. Guardado como
  hash (PBKDF2 + salt), nunca em texto puro. O PWA troca o PIN por um token de
  sessão (90 dias) guardado no `localStorage` do celular; perdeu o celular?
  `caderno revogar-sessoes` derruba todo mundo de uma vez.
- **Tailscale** — a rede privada que conecta seus dispositivos sem abrir porta
  nenhuma pra internet. É o componente que falta você mesmo instalar (não dá
  pra fazer isso remotamente por você).

### Configurar (uma vez)

```bash
pip install -e ".[web]"       # instala o Flask
caderno definir-pin           # escolhe o PIN de acesso ao servidor
```

Instale o [Tailscale](https://tailscale.com/download) na máquina que vai rodar
o servidor e no celular, e entre com a mesma conta nos dois. Cada dispositivo
ganha um nome na sua rede privada (`tailscale status` mostra os nomes).

### Usar

Na máquina (precisa estar ligada e com o Tailscale ativo):

```bash
caderno servir
```

No celular, com o Tailscale ativo, abra no navegador:

```
http://<nome-tailscale-da-maquina>:8420
```

Digite o PIN, e no menu do navegador escolha **"Adicionar à tela de início"**
(Android/Chrome) ou **"Adicionar à Tela de Início"** (iOS/Safari) — vira um
ícone que abre em tela cheia, como um app.

Prefere IP fixo a nome? `tailscale ip -4` na máquina do servidor mostra o IP da
tailnet (só muda se você reconfigurar a rede).

### O que o PWA cobre

Registrar por texto, resumo do mês, perguntas em chat (mantendo contexto) e
histórico com exclusão — os quatro usos do dia a dia. Import de planilha e
voz continuam só no CLI por enquanto (uso pontual, não precisa estar no bolso).

### Limites conscientes

- O servidor Flask embutido (`app.run()`) é o de desenvolvimento — adequado
  pra um único usuário atrás de uma tailnet, mas o próprio Flask avisa que não
  é pensado pra produção geral. Se algum dia isso for exposto além da sua
  tailnet, troque por um servidor WSGI de verdade (gunicorn/waitress) antes.
- Sem a máquina ligada (ou sem o Tailscale ativo nela), o PWA abre mas não
  funciona — é o trade-off assumido ao escolher essa arquitetura em vez de uma
  VPS 24/7.

**Interface gráfica** — o PWA existe hoje como a superfície remota; a CLI
continua sendo a forma mais direta de uso local (import de planilha, testes,
automações). Os dois falam com os mesmos módulos, nenhum é "o principal".
