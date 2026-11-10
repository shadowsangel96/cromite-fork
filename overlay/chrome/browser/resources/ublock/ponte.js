// Ponte tra chrome://settings ("Blocco contenuti") e uBlock Origin. La pagina delle
// impostazioni non può parlare con un'estensione: carica questa pagina in un frame
// nascosto e le passa i messaggi, che qui vanno a uBlock sul canale della sua dashboard.
// Si accettano solo messaggi da chrome://settings e solo quelli che la sezione usa.
(() => {
  'use strict';

  const SETTINGS = 'chrome://settings';
  const ALLOWED = new Set([
    'applyFilterListSelection',
    'getAppData',
    'getLists',
    'getRules',
    'getWhitelist',
    'gotoURL',
    'modifyRuleset',
    'readUserFilters',
    'reloadAllFilters',
    'setWhitelist',
    'updateNow',
    'userSettings',
    'writeUserFilters',
  ]);

  function allowed(message) {
    if (!message || typeof message !== 'object' || !ALLOWED.has(message.what)) {
      return false;
    }
    if (message.what === 'userSettings') {
      return message.name === 'autoUpdate' && typeof message.value === 'boolean';
    }
    if (message.what === 'gotoURL') {
      return message.details && message.details.url === 'dashboard.html';
    }
    return true;
  }

  if (window.parent === window) {
    return;
  }

  // In un frame vapi-client.js spegne vAPI per sempre se la connessione con uBlock cade, e cade
  // se la pagina si apre mentre uBlock si sta ancora avviando. Qui si fa come nella pagina
  // principale: si chiude la connessione (le richieste in corso tornano vuote) e la prossima
  // richiesta ne apre una nuova. Va fatto prima della prima richiesta, che lega questo gestore.
  vAPI.messaging.disconnectListener = function() {
    void chrome.runtime.lastError;
    this.destroyPort();
  };

  // Le letture hanno sempre una risposta: se torna vuota uBlock non era pronto, e si riprova.
  const READS = new Set(['getAppData', 'getLists', 'getRules', 'getWhitelist', 'readUserFilters']);

  async function ask(message) {
    for (let attempt = 0; ; attempt++) {
      if (!self.vAPI) {
        // Spento comunque (connessione impossibile): si ricarica il ponte.
        location.reload();
        return new Promise(() => {});
      }
      let response;
      try {
        response = await vAPI.messaging.send('dashboard', message);
      } catch (e) {
        response = undefined;
      }
      if (response !== undefined || !READS.has(message.what) || attempt >= 20) {
        return response;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }

  window.addEventListener('message', async (event) => {
    if (event.origin !== SETTINGS || event.source !== window.parent) {
      return;
    }
    const {id, message} = event.data || {};
    if (typeof id !== 'number' || !allowed(message)) {
      return;
    }
    const response = await ask(message);
    window.parent.postMessage({id, response}, SETTINGS);
  });

  window.parent.postMessage({ready: true}, SETTINGS);
})();
