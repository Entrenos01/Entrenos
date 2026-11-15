// Permite instalar o EntreNós como app. Não guarda cópias das páginas:
// sempre busca a versão mais nova do site e dos números.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
