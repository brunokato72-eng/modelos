"use strict";

// ---------------------------------------------------------------------------
// estado + utilidades
// ---------------------------------------------------------------------------

const estado = {
  token: localStorage.getItem("caderno_token") || null,
  abaAtiva: "registrar",
  mesResumo: mesAtual(),
  mesHistorico: mesAtual(),
  categoriasDespesa: [],
  historicoChat: [], // [{pergunta, resposta}]
};

const MESES_PT = [
  "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
  "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
];

function mesAtual() {
  const hoje = new Date();
  return `${hoje.getFullYear()}-${String(hoje.getMonth() + 1).padStart(2, "0")}`;
}

function somarMes(mes, delta) {
  const [ano, m] = mes.split("-").map(Number);
  const indice = (m - 1) + delta;
  const anoNovo = ano + Math.floor(indice / 12);
  const mesNovo = ((indice % 12) + 12) % 12;
  return `${anoNovo}-${String(mesNovo + 1).padStart(2, "0")}`;
}

function rotuloMes(mes) {
  const [ano, m] = mes.split("-").map(Number);
  return `${MESES_PT[m - 1]} de ${ano}`;
}

function formatarMoeda(valor) {
  const numero = Number(valor || 0);
  const partes = numero.toFixed(2).split(".");
  partes[0] = partes[0].replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `R$ ${partes[0]},${partes[1]}`;
}

function formatarDataCurta(iso) {
  const [, m, d] = iso.split("-");
  return `${d}/${m}`;
}

function el(tag, propriedades = {}, filhos = []) {
  const elemento = document.createElement(tag);
  Object.entries(propriedades).forEach(([chave, valor]) => {
    if (chave === "class") elemento.className = valor;
    else if (chave === "texto") elemento.textContent = valor;
    else if (chave.startsWith("on")) elemento.addEventListener(chave.slice(2), valor);
    else elemento.setAttribute(chave, valor);
  });
  filhos.forEach((filho) => elemento.appendChild(filho));
  return elemento;
}

// ---------------------------------------------------------------------------
// chamadas à API
// ---------------------------------------------------------------------------

class ErroApi extends Error {
  constructor(mensagem, status) {
    super(mensagem);
    this.status = status;
  }
}

async function api(caminho, opcoes = {}) {
  const cabecalhos = { ...(opcoes.headers || {}) };
  if (estado.token) cabecalhos["Authorization"] = `Bearer ${estado.token}`;
  if (opcoes.body) cabecalhos["Content-Type"] = "application/json";

  const resposta = await fetch(caminho, { ...opcoes, headers: cabecalhos });

  if (resposta.status === 401) {
    esquecerSessao();
    mostrarLogin("sua sessão expirou — entre de novo.");
    throw new ErroApi("sessão expirada", 401);
  }

  const tipo = resposta.headers.get("content-type") || "";
  const corpo = tipo.includes("application/json") ? await resposta.json() : await resposta.text();

  if (!resposta.ok) {
    const mensagem = (corpo && corpo.erro) || String(corpo).slice(0, 200) || "erro desconhecido";
    throw new ErroApi(mensagem, resposta.status);
  }
  return corpo;
}

function guardarSessao(token) {
  estado.token = token;
  localStorage.setItem("caderno_token", token);
}

function esquecerSessao() {
  estado.token = null;
  localStorage.removeItem("caderno_token");
}

// ---------------------------------------------------------------------------
// login
// ---------------------------------------------------------------------------

function mostrarLogin(mensagem) {
  document.getElementById("tela-login").hidden = false;
  document.getElementById("app").hidden = true;
  if (mensagem) {
    const erro = document.getElementById("login-erro");
    erro.textContent = mensagem;
    erro.hidden = false;
  }
  document.getElementById("campo-pin").focus();
}

function mostrarApp() {
  document.getElementById("tela-login").hidden = true;
  document.getElementById("app").hidden = false;
}

async function iniciar() {
  if (!estado.token) {
    mostrarLogin();
    return;
  }
  try {
    const status = await api("/api/auth/status");
    if (status.sessaoValida) {
      mostrarApp();
      irParaAba("registrar");
    } else {
      esquecerSessao();
      mostrarLogin();
    }
  } catch (erro) {
    mostrarLogin();
  }
}

document.getElementById("form-login").addEventListener("submit", async (evento) => {
  evento.preventDefault();
  const campoPin = document.getElementById("campo-pin");
  const erroEl = document.getElementById("login-erro");
  erroEl.hidden = true;
  try {
    const resposta = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin: campoPin.value }),
    });
    const corpo = await resposta.json();
    if (!resposta.ok) throw new Error(corpo.erro || "PIN incorreto");
    guardarSessao(corpo.token);
    campoPin.value = "";
    mostrarApp();
    irParaAba("registrar");
  } catch (erro) {
    erroEl.textContent = erro.message;
    erroEl.hidden = false;
  }
});

document.getElementById("botao-sair").addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (e) { /* segue o baile */ }
  esquecerSessao();
  mostrarLogin();
});

// ---------------------------------------------------------------------------
// navegação entre abas
// ---------------------------------------------------------------------------

function irParaAba(nome) {
  estado.abaAtiva = nome;
  document.querySelectorAll(".aba").forEach((secao) => {
    secao.hidden = secao.id !== `tab-${nome}`;
  });
  document.querySelectorAll(".aba-botao").forEach((botao) => {
    botao.classList.toggle("ativo", botao.dataset.aba === nome);
  });
  if (nome === "resumo") carregarResumo();
  if (nome === "painel") carregarPainel();
  if (nome === "historico") carregarHistorico();
  if (nome === "perguntar" && estado.historicoChat.length === 0) {
    renderizarMensagemAssistente("Pergunte o que quiser sobre seus gastos — ex: \"quanto gastei em mercado esse mês?\"");
  }
}

document.querySelectorAll(".aba-botao").forEach((botao) => {
  botao.addEventListener("click", () => irParaAba(botao.dataset.aba));
});

// ---------------------------------------------------------------------------
// registrar
// ---------------------------------------------------------------------------

document.getElementById("form-registrar").addEventListener("submit", async (evento) => {
  evento.preventDefault();
  const campo = document.getElementById("campo-texto");
  const botao = document.getElementById("botao-registrar");
  const status = document.getElementById("registrar-status");
  const texto = campo.value.trim();
  if (!texto) return;

  botao.disabled = true;
  botao.textContent = "Registrando...";
  status.textContent = "";

  try {
    const resultado = await api("/api/registrar", {
      method: "POST",
      body: JSON.stringify({ texto }),
    });
    if (!resultado.grupos.length) {
      status.innerHTML = "";
      status.appendChild(el("p", { class: "texto-fraco", texto: resultado.observacao || "não identifiquei nenhum lançamento nessa mensagem." }));
    } else {
      campo.value = "";
      resultado.grupos.forEach((grupo) => adicionarItemRegistrado(grupo));
    }
  } catch (erro) {
    if (erro.status !== 401) {
      status.innerHTML = "";
      status.appendChild(el("p", { class: "texto-erro", texto: erro.message }));
    }
  } finally {
    botao.disabled = false;
    botao.textContent = "Registrar";
  }
});

function adicionarItemRegistrado(grupo) {
  const lista = document.getElementById("registrar-lista");
  const primeira = grupo.linhas[0];
  const totalParcelas = primeira.totalParcelas;

  const linhaValor = totalParcelas > 1
    ? `${formatarMoeda(primeira.valorTotal)} em ${totalParcelas}x de ${formatarMoeda(primeira.valor)}`
    : formatarMoeda(primeira.valor);

  const item = el("div", { class: "item-lancamento" }, [
    el("div", { class: "detalhe" }, [
      el("span", { class: "descricao", texto: primeira.descricao || "(sem descrição)" }),
      el("span", { class: "meta", texto: `${primeira.categoria} · ${primeira.formaPagamento}${primeira.conta ? " · " + primeira.conta : ""} · ${formatarDataCurta(primeira.data)}` }),
    ]),
    el("span", { class: `valor ${primeira.tipo === "Despesa" ? "despesa" : "receita"}`, texto: linhaValor }),
    el("button", {
      class: "remover", texto: "desfazer",
      onclick: async () => {
        try {
          const grupoParcelamento = primeira.grupoParcelamento;
          const url = grupoParcelamento
            ? `/api/lancamentos/${primeira.id}?grupo=1`
            : `/api/lancamentos/${primeira.id}`;
          await api(url, { method: "DELETE" });
          item.remove();
        } catch (erro) {
          if (erro.status !== 401) alert(`não consegui desfazer: ${erro.message}`);
        }
      },
    }),
  ]);
  lista.prepend(item);
}

// ---------------------------------------------------------------------------
// resumo
// ---------------------------------------------------------------------------

document.getElementById("mes-anterior").addEventListener("click", () => {
  estado.mesResumo = somarMes(estado.mesResumo, -1);
  carregarResumo();
});
document.getElementById("mes-seguinte").addEventListener("click", () => {
  estado.mesResumo = somarMes(estado.mesResumo, 1);
  carregarResumo();
});

async function carregarResumo() {
  document.getElementById("mes-atual-rotulo").textContent = rotuloMes(estado.mesResumo);
  const container = document.getElementById("resumo-conteudo");
  container.innerHTML = `<p class="texto-fraco">carregando...</p>`;
  try {
    const resumo = await api(`/api/resumo?mes=${estado.mesResumo}`);
    renderizarResumo(container, resumo);
  } catch (erro) {
    if (erro.status !== 401) {
      container.innerHTML = "";
      container.appendChild(el("p", { class: "texto-erro", texto: erro.message }));
    }
  }
}

function renderizarResumo(container, r) {
  container.innerHTML = "";

  const totais = el("div", { class: "resumo-totais" }, [
    el("div", { class: "resumo-caixa" }, [
      el("div", { class: "rotulo", texto: "Despesas" }),
      el("div", { class: "numero despesa", texto: formatarMoeda(r.totalDespesas) }),
    ]),
    el("div", { class: "resumo-caixa" }, [
      el("div", { class: "rotulo", texto: "Receitas" }),
      el("div", { class: "numero receita", texto: formatarMoeda(r.totalReceitas) }),
    ]),
  ]);
  container.appendChild(totais);

  const saldoCartao = el("div", { class: "cartao" }, [
    el("div", { class: "rotulo", texto: "Saldo do mês" }),
    el("div", { class: `numero ${r.saldo >= 0 ? "receita" : "despesa"}`, texto: formatarMoeda(r.saldo) }),
  ]);
  container.appendChild(saldoCartao);

  if (r.mesAnterior && r.mesAnterior.variacaoPercentual !== null && r.mesAnterior.variacaoPercentual !== undefined) {
    const seta = r.mesAnterior.variacaoPercentual > 0 ? "↑" : "↓";
    const nota = el("p", {
      class: "texto-fraco",
      texto: `vs ${rotuloMes(r.mesAnterior.mes)}: ${formatarMoeda(r.mesAnterior.totalDespesas)} (${seta} ${Math.abs(r.mesAnterior.variacaoPercentual)}%)`,
    });
    container.appendChild(nota);
  }

  adicionarBlocoBarras(container, "Por categoria", r.porCategoria, r.totalDespesas);
  adicionarBlocoBarras(container, "Por forma de pagamento", r.porFormaPagamento, r.totalDespesas);
  adicionarBlocoBarras(container, "Por conta", r.porConta, r.totalDespesas);

  if (!r.porCategoria.length && !r.totalReceitas) {
    container.appendChild(el("p", { class: "vazio", texto: "nada registrado nesse mês." }));
  }
}

function adicionarBlocoBarras(container, titulo, grupos, total) {
  if (!grupos || !grupos.length) return;
  container.appendChild(el("div", { class: "resumo-bloco-titulo", texto: titulo }));
  grupos.forEach((grupo) => {
    const fatia = total ? (grupo.resultado / total) * 100 : 0;
    container.appendChild(
      el("div", { class: "barra-grupo" }, [
        el("div", { class: "barra-topo" }, [
          el("span", { texto: grupo.grupo }),
          el("span", { texto: formatarMoeda(grupo.resultado) }),
        ]),
        el("div", { class: "barra-fundo" }, [
          el("div", { class: "barra-preenchimento", style: `width:${Math.min(100, fatia)}%` }),
        ]),
      ])
    );
  });
}

// ---------------------------------------------------------------------------
// painel (score do dia + meta de poupança + orçamentos + investimentos)
// ---------------------------------------------------------------------------

async function carregarPainel() {
  const scoreContainer = document.getElementById("score-cartao");
  const metaContainer = document.getElementById("meta-cartao");
  const saudeContainer = document.getElementById("saude-cartao");
  const orcamentosContainer = document.getElementById("orcamentos-lista");
  const investimentosContainer = document.getElementById("investimentos-lista");
  scoreContainer.innerHTML = `<p class="texto-fraco">carregando...</p>`;
  metaContainer.innerHTML = `<p class="texto-fraco">carregando...</p>`;
  saudeContainer.innerHTML = `<p class="texto-fraco">carregando...</p>`;
  orcamentosContainer.innerHTML = `<p class="texto-fraco">carregando...</p>`;
  investimentosContainer.innerHTML = `<p class="texto-fraco">carregando...</p>`;

  try {
    if (!estado.categoriasDespesa.length) {
      const panorama = await api("/api/panorama");
      estado.categoriasDespesa = panorama.categoriasDespesa;
      preencherSelectCategorias();
    }
    const [score, meta, saude, orcamentos, investimentos] = await Promise.all([
      api("/api/score"),
      api("/api/meta"),
      api(`/api/saude?mes=${mesAtual()}`),
      api("/api/orcamentos"),
      api("/api/investimentos"),
    ]);
    document.getElementById("painel-ciclo-rotulo").textContent =
      `ciclo de ${rotuloMes(meta.ciclo)} · fecha ${formatarDataCurta(meta.fim)}`;
    renderizarScore(scoreContainer, score);
    renderizarMeta(metaContainer, meta);
    renderizarSaude(saudeContainer, saude);
    renderizarOrcamentos(orcamentosContainer, orcamentos);
    renderizarInvestimentos(investimentosContainer, investimentos);
  } catch (erro) {
    if (erro.status !== 401) {
      [scoreContainer, metaContainer, saudeContainer, orcamentosContainer, investimentosContainer].forEach(
        (c) => (c.innerHTML = "")
      );
      scoreContainer.appendChild(el("p", { class: "texto-erro", texto: erro.message }));
    }
  }
}

const CLASSE_POR_CLASSIFICACAO = {
  tranquilo: "boa", boa: "boa",
  "atenção": "atencao",
  "crítico": "critica", "crítica": "critica",
};

function renderizarScore(container, s) {
  container.innerHTML = "";
  const classe = CLASSE_POR_CLASSIFICACAO[s.classificacao] || "";

  const anel = elementoAnelProgresso(s.pontuacaoFinal, classe);
  const detalhe = el("div", { class: "score-detalhe" }, [
    el("span", { class: "score-rotulo", texto: "Score do dia" }),
    el("span", { class: `score-classificacao ${classe}`, texto: s.classificacao }),
    el("span", { class: "texto-fraco", texto: `comportamento: ${s.pontuacaoComportamento}/100` }),
  ]);

  container.appendChild(el("div", { class: "score-topo" }, [anel, detalhe]));

  const alertas = [...(s.alertasComportamento || [])];
  (s.categoriasEmRiscoDeEstourar || []).forEach((c) => {
    alertas.push(`${c.categoria}: projeção de ${formatarMoeda(c.projecao)} (limite ${formatarMoeda(c.limite)})`);
  });
  if (alertas.length) {
    const lista = el("ul", { class: "score-alertas" });
    alertas.forEach((alerta) => lista.appendChild(el("li", { texto: alerta })));
    container.appendChild(lista);
  } else {
    container.appendChild(el("p", { class: "texto-fraco score-sem-alerta", texto: "nada chamando atenção hoje." }));
  }
}

function elementoAnelProgresso(percentual, classe) {
  const p = Math.max(0, Math.min(100, percentual));
  const raio = 30;
  const circunferencia = 2 * Math.PI * raio;
  const offset = circunferencia * (1 - p / 100);
  const svgNs = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNs, "svg");
  svg.setAttribute("viewBox", "0 0 72 72");
  svg.setAttribute("class", `anel-progresso ${classe}`);

  const fundo = document.createElementNS(svgNs, "circle");
  fundo.setAttribute("cx", "36"); fundo.setAttribute("cy", "36"); fundo.setAttribute("r", String(raio));
  fundo.setAttribute("class", "anel-fundo");
  svg.appendChild(fundo);

  const frente = document.createElementNS(svgNs, "circle");
  frente.setAttribute("cx", "36"); frente.setAttribute("cy", "36"); frente.setAttribute("r", String(raio));
  frente.setAttribute("class", "anel-frente");
  frente.setAttribute("stroke-dasharray", String(circunferencia));
  frente.setAttribute("stroke-dashoffset", String(offset));
  svg.appendChild(frente);

  const texto = document.createElementNS(svgNs, "text");
  texto.setAttribute("x", "36"); texto.setAttribute("y", "41");
  texto.setAttribute("class", "anel-texto");
  texto.textContent = String(Math.round(percentual));
  svg.appendChild(texto);

  const wrapper = el("div", { class: "anel-wrapper" });
  wrapper.appendChild(svg);
  return wrapper;
}

function renderizarMeta(container, m) {
  container.innerHTML = "";
  if (!m.metaDefinida) {
    container.appendChild(el("span", { class: "meta-rotulo", texto: "Meta de poupança" }));
    container.appendChild(el("p", { class: "texto-fraco", texto: "nenhuma meta definida ainda." }));
    return;
  }
  const percentual = Math.max(0, Math.min(100, m.aderenciaPercentual ?? 0));
  const corClasse = m.noCaminho ? "" : (percentual >= 50 ? "atencao" : "estourado");

  container.appendChild(el("div", { class: "meta-topo" }, [
    el("span", { class: "meta-rotulo", texto: "Meta de poupança" }),
    el("span", { class: `meta-status ${m.noCaminho ? "no-caminho" : "fora-caminho"}`,
      texto: m.noCaminho ? "no caminho" : "fora do caminho" }),
  ]));
  container.appendChild(el("div", { class: "barra-fundo meta-barra" }, [
    el("div", { class: `barra-preenchimento ${corClasse}`, style: `width:${percentual}%` }),
  ]));
  container.appendChild(el("div", { class: "meta-numeros" }, [
    el("span", { texto: `poupança projetada: ${formatarMoeda(m.poupancaProjetada)}` }),
    el("span", { class: "texto-fraco", texto: `meta: ${formatarMoeda(m.metaDefinida)}` }),
  ]));
  container.appendChild(el("div", { class: "meta-numeros" }, [
    el("span", { class: "texto-fraco", texto: `receita esperada: ${formatarMoeda(m.receitaEsperada)}` }),
    el("span", { class: "texto-fraco", texto: `despesa projetada: ${formatarMoeda(m.despesaProjetada)}` }),
  ]));
}

function preencherSelectCategorias() {
  const select = document.getElementById("orcamento-categoria");
  select.innerHTML = "";
  estado.categoriasDespesa.forEach((categoria) => {
    select.appendChild(el("option", { value: categoria, texto: categoria }));
  });
}

function renderizarSaude(container, s) {
  container.innerHTML = "";
  const classificacaoClasse = { "boa": "boa", "atenção": "atencao", "crítica": "critica" }[s.classificacao] || "";

  const pontuacao = el("div", { class: `saude-pontuacao ${classificacaoClasse}`, texto: String(s.pontuacao) });

  const linhas = [];
  if (s.taxaPoupanca !== null && s.taxaPoupanca !== undefined) {
    linhas.push(`taxa de poupança: ${s.taxaPoupanca}%`);
  }
  if (s.tendenciaDespesas) linhas.push(`despesas ${s.tendenciaDespesas}`);
  if (s.comprometimentoFuturoPercentual !== null && s.comprometimentoFuturoPercentual !== undefined) {
    linhas.push(`parcelas futuras: ${s.comprometimentoFuturoPercentual}% da receita média`);
  }

  const detalhe = el("div", { class: "saude-detalhe" }, [
    el("span", { class: "saude-classificacao", texto: s.classificacao }),
    ...linhas.map((linha) => el("span", { class: "texto-fraco", texto: linha })),
  ]);

  container.appendChild(pontuacao);
  container.appendChild(detalhe);

  if (s.alertas && s.alertas.length) {
    const lista = el("ul", { class: "saude-alertas" });
    s.alertas.forEach((alerta) => lista.appendChild(el("li", { texto: `⚠ ${alerta}` })));
    detalhe.appendChild(lista);
  }
}

function renderizarOrcamentos(container, itens) {
  container.innerHTML = "";
  if (!itens.length) {
    container.appendChild(el("p", { class: "vazio", texto: "nenhum orçamento definido ainda." }));
    return;
  }
  itens.forEach((item) => {
    const corClasse = item.estourado ? "estourado" : (item.percentual >= 80 ? "atencao" : "");
    const bloco = el("div", { class: "orcamento-item" }, [
      el("div", { class: "orcamento-topo" }, [
        el("span", { class: "categoria", texto: item.categoria }),
        el("span", { class: "valores", texto: `${formatarMoeda(item.gasto)} / ${formatarMoeda(item.limite)}` }),
      ]),
      el("div", { class: "barra-fundo" }, [
        el("div", { class: `barra-preenchimento ${corClasse}`, style: `width:${Math.min(100, item.percentual)}%` }),
      ]),
      el("button", {
        class: "orcamento-remover", texto: "remover orçamento",
        onclick: async () => {
          try {
            await api(`/api/orcamentos/${encodeURIComponent(item.categoria)}`, { method: "DELETE" });
            carregarPainel();
          } catch (erro) {
            if (erro.status !== 401) alert(`não consegui remover: ${erro.message}`);
          }
        },
      }),
    ]);
    container.appendChild(bloco);
  });
}

document.getElementById("form-orcamento").addEventListener("submit", async (evento) => {
  evento.preventDefault();
  const categoria = document.getElementById("orcamento-categoria").value;
  const campoLimite = document.getElementById("orcamento-limite");
  const limite = Number(campoLimite.value);
  if (!categoria || !limite || limite <= 0) return;

  try {
    await api("/api/orcamentos", { method: "POST", body: JSON.stringify({ categoria, limite }) });
    campoLimite.value = "";
    carregarPainel();
  } catch (erro) {
    if (erro.status !== 401) alert(`não consegui salvar: ${erro.message}`);
  }
});

function renderizarInvestimentos(container, dados) {
  container.innerHTML = "";
  if (!dados.posicoes.length) {
    container.appendChild(el("p", { class: "vazio", texto: "nenhuma posição sincronizada ainda." }));
    return;
  }
  const cartao = el("div", { class: "cartao" });
  dados.posicoes.forEach((posicao) => {
    cartao.appendChild(el("div", { class: "investimento-item" }, [
      el("div", {}, [
        el("div", { class: "nome", texto: posicao.nome }),
        el("div", { class: "tipo", texto: `${posicao.tipo} · ${posicao.conta}` }),
      ]),
      el("span", { texto: formatarMoeda(posicao.valor) }),
    ]));
  });
  cartao.appendChild(el("div", { class: "investimento-total" }, [
    el("span", { texto: "Total" }),
    el("span", { texto: formatarMoeda(dados.total) }),
  ]));
  container.appendChild(cartao);
}

// ---------------------------------------------------------------------------
// perguntar (chat)
// ---------------------------------------------------------------------------

function renderizarMensagemUsuario(texto) {
  const container = document.getElementById("chat-mensagens");
  container.appendChild(el("div", { class: "mensagem usuario", texto }));
  container.scrollTop = container.scrollHeight;
}

function renderizarMensagemAssistente(texto, carregando = false) {
  const container = document.getElementById("chat-mensagens");
  const bolha = el("div", { class: `mensagem assistente${carregando ? " carregando" : ""}`, texto });
  container.appendChild(bolha);
  container.scrollTop = container.scrollHeight;
  return bolha;
}

document.getElementById("form-chat").addEventListener("submit", async (evento) => {
  evento.preventDefault();
  const campo = document.getElementById("campo-pergunta");
  const pergunta = campo.value.trim();
  if (!pergunta) return;

  campo.value = "";
  renderizarMensagemUsuario(pergunta);
  const bolhaCarregando = renderizarMensagemAssistente("pensando...", true);

  try {
    const resultado = await api("/api/perguntar", {
      method: "POST",
      body: JSON.stringify({ pergunta, historico: estado.historicoChat }),
    });
    bolhaCarregando.textContent = resultado.resposta;
    bolhaCarregando.classList.remove("carregando");
    estado.historicoChat.push({ pergunta, resposta: resultado.resposta });
  } catch (erro) {
    if (erro.status !== 401) {
      bolhaCarregando.textContent = `erro: ${erro.message}`;
      bolhaCarregando.classList.remove("carregando");
    } else {
      bolhaCarregando.remove();
    }
  }
});

// ---------------------------------------------------------------------------
// histórico
// ---------------------------------------------------------------------------

document.getElementById("hist-mes-anterior").addEventListener("click", () => {
  estado.mesHistorico = somarMes(estado.mesHistorico, -1);
  carregarHistorico();
});
document.getElementById("hist-mes-seguinte").addEventListener("click", () => {
  estado.mesHistorico = somarMes(estado.mesHistorico, 1);
  carregarHistorico();
});

async function carregarHistorico() {
  document.getElementById("hist-mes-rotulo").textContent = rotuloMes(estado.mesHistorico);
  const lista = document.getElementById("historico-lista");
  lista.innerHTML = `<p class="texto-fraco">carregando...</p>`;
  try {
    const lancamentos = await api(`/api/listar?mes=${estado.mesHistorico}&limite=300`);
    renderizarHistorico(lista, lancamentos);
  } catch (erro) {
    if (erro.status !== 401) {
      lista.innerHTML = "";
      lista.appendChild(el("p", { class: "texto-erro", texto: erro.message }));
    }
  }
}

function renderizarHistorico(lista, lancamentos) {
  lista.innerHTML = "";
  if (!lancamentos.length) {
    lista.appendChild(el("p", { class: "vazio", texto: "nenhum lançamento nesse mês." }));
    return;
  }
  lancamentos.forEach((lanc) => {
    const parcela = lanc.totalParcelas > 1 ? ` [${lanc.parcelaAtual}/${lanc.totalParcelas}]` : "";
    const item = el("div", { class: "item-lancamento" }, [
      el("div", { class: "detalhe" }, [
        el("span", { class: "descricao", texto: (lanc.descricao || "(sem descrição)") + parcela }),
        el("span", { class: "meta", texto: `${lanc.categoria} · ${lanc.formaPagamento}${lanc.conta ? " · " + lanc.conta : ""} · ${formatarDataCurta(lanc.data)}` }),
      ]),
      el("span", { class: `valor ${lanc.tipo === "Despesa" ? "despesa" : "receita"}`, texto: formatarMoeda(lanc.valor) }),
      el("button", {
        class: "remover", texto: "excluir",
        onclick: async () => {
          if (!confirm(`Excluir "${lanc.descricao || lanc.categoria}"?`)) return;
          try {
            await api(`/api/lancamentos/${lanc.id}`, { method: "DELETE" });
            item.remove();
          } catch (erro) {
            if (erro.status !== 401) alert(`não consegui excluir: ${erro.message}`);
          }
        },
      }),
    ]);
    lista.appendChild(item);
  });
}

// ---------------------------------------------------------------------------
// service worker + início
// ---------------------------------------------------------------------------

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/service-worker.js").catch(() => {});
  });
}

iniciar();
