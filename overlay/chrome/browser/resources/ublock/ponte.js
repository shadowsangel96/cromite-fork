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

  window.addEventListener('message', async (event) => {
    if (event.origin !== SETTINGS || event.source !== window.parent) {
      return;
    }
    const {id, message} = event.data || {};
    if (typeof id !== 'number' || !allowed(message)) {
      return;
    }
    let response;
    try {
      response = await vAPI.messaging.send('dashboard', message);
    } catch (e) {
      response = undefined;
    }
    window.parent.postMessage({id, response}, SETTINGS);
  });

  window.parent.postMessage({ready: true}, SETTINGS);
})();
