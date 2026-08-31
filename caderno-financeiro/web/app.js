"use strict";

// ---------------------------------------------------------------------------
// estado + utilidades
// ---------------------------------------------------------------------------

const estado = {
  token: localStorage.getItem("caderno_token") || null,
  abaAtiva: "registrar",
  mesResumo: mesAtual(),
  mesHistorico: mesAtual(),
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
