// Cache só do "casco" da aplicação (HTML/CSS/JS/ícones) — dados nunca são
// cacheados aqui, porque tudo que é financeiro tem que vir sempre fresco do
// servidor local. Sem o servidor rodando, o app abre mas não funciona: é o
// esperado, já que ele depende da sua máquina estar ligada.
//
// IMPORTANTE: mude VERSAO_CACHE a cada deploy que tocar em web/*. Sem isso,
// o navegador/PWA já instalado continua servindo o casco antigo (HTML/JS)
// indefinidamente, mesmo com o servidor já rodando código novo — achado
// real (2026-11-21): o titular viu o painel "desatualizado" mesmo com os
// dados corretos no banco, porque o app.js que o celular tinha em cache era
// de antes da correção ciclo->mês.
const VERSAO_CACHE = "caderno-v2";
const CASCO = [
  "/", "/index.html", "/app.js", "/styles.css", "/manifest.json",
  "/icons/icon-192.png", "/icons/icon-512.png",
];

self.addEventListener("install", (evento) => {
  evento.waitUntil(
    caches.open(VERSAO_CACHE).then((cache) => cache.addAll(CASCO))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (evento) => {
  evento.waitUntil(
    caches.keys().then((chaves) =>
      Promise.all(chaves.filter((c) => c !== VERSAO_CACHE).map((c) => caches.delete(c)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (evento) => {
  const url = new URL(evento.request.url);
  if (url.pathname.startsWith("/api/")) return; // API sempre direto da rede

  evento.respondWith(
    caches.match(evento.request).then(
      (resposta) => resposta || fetch(evento.request)
    )
  );
});
