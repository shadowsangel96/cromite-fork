// Pannello "Blocco contenuti": parla con uBlock Origin come il suo popup, sullo stesso canale.
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  // Le scritte vengono da _locales di uBlock, dove forkctl aggiunge le nostre (fork-messages.json).
  const t = (key, ...subs) => chrome.i18n.getMessage('fork_' + key, subs.map(String));

  document.documentElement.lang = chrome.i18n.getUILanguage();
  for (const el of document.querySelectorAll('[data-fork-i18n]')) {
    el.textContent = chrome.i18n.getMessage(el.dataset.forkI18n);
  }
  for (const el of document.querySelectorAll('[data-fork-i18n-label]')) {
    el.setAttribute('aria-label', chrome.i18n.getMessage(el.dataset.forkI18nLabel));
  }
  const send = (message) => vAPI.messaging.send('popupPanel', message);

  let data = null;

  // Il conteggio di uBlock è per nome host: qui si somma per dominio.
  function summarize(popupData) {
    const blocked = new Map();
    const external = new Set();
    for (const entry of Object.values(popupData.hostnameDict || {})) {
      if (!entry.counts) {
        continue;
      }
      if (entry.domain !== popupData.pageDomain) {
        external.add(entry.domain);
      }
      if (entry.counts.blocked.any > 0) {
        blocked.set(entry.domain, (blocked.get(entry.domain) || 0) + entry.counts.blocked.any);
      }
    }
    const rows = [...blocked].sort((a, b) => b[1] - a[1]);
    const blockedExternal = rows.filter(([domain]) => external.has(domain)).length;
    return {rows, blockedExternal, external: external.size};
  }

  function renderDomains(on, rows) {
    const box = $('domains');
    box.textContent = '';
    if (!on || rows.length === 0) {
      const text = document.createElement('p');
      text.textContent = on ? t('bloccoNothingYet') : t('bloccoOffText');
      box.append(text);
      return;
    }
    for (const [domain, count] of rows) {
      const row = document.createElement('div');
      row.className = 'row';
      const name = document.createElement('span');
      name.textContent = domain;
      const number = document.createElement('span');
      number.className = 'count';
      number.textContent = String(count);
      row.append(name, number);
      box.append(row);
    }
  }

  function render() {
    const on = data.netFilteringSwitch === true;
    document.body.classList.toggle('off', !on);
    $('power').setAttribute('aria-pressed', String(on));
    $('power-title').textContent =
        t(on ? 'bloccoOnAt' : 'bloccoOffAt', data.pageHostname);
    $('power-hint').textContent = t(on ? 'bloccoHintOn' : 'bloccoHintOff');
    const {rows, blockedExternal, external} = summarize(data);
    $('blocked').textContent = String(on && data.pageCounts ? data.pageCounts.blocked.any : 0);
    $('domains-blocked').textContent = String(on ? blockedExternal : 0);
    $('domains-total').textContent = String(external);
    renderDomains(on, rows);
    for (const button of document.querySelectorAll('[data-switch]')) {
      button.setAttribute('aria-pressed', String(data[button.dataset.state] === true));
    }
  }

  async function refresh() {
    data = await send({what: 'getPopupData', tabId: data ? data.tabId : null});
    render();
  }

  function reloadPage(bypassCache) {
    return send({
      what: 'reloadTab',
      tabId: data.tabId,
      url: data.rawURL,
      select: false,
      bypassCache,
    });
  }

  $('close').onclick = () => vAPI.closePopup();

  $('power').onclick = async () => {
    await send({
      what: 'toggleNetFiltering',
      url: data.pageURL,
      scope: '',
      state: data.netFilteringSwitch !== true,
      tabId: data.tabId,
    });
    await reloadPage(false);
    await refresh();
  };

  for (const button of document.querySelectorAll('[data-switch]')) {
    button.onclick = async () => {
      const name = button.dataset.switch;
      data = await send({
        what: 'toggleHostnameSwitch',
        name,
        hostname: data.pageHostname,
        state: data[button.dataset.state] !== true,
        tabId: data.tabId,
        persist: true,
      });
      render();
      // I popup si bloccano all'apertura: la pagina già caricata non cambia.
      if (name !== 'no-popups') {
        await reloadPage(name === 'no-scripting');
      }
    };
  }

  $('picker').onclick = () => {
    send({what: 'launchElementPicker', tabId: data.tabId});
    vAPI.closePopup();
  };

  const openPage = (url) => {
    send({what: 'gotoURL', details: {url, select: true, index: -1}});
    vAPI.closePopup();
  };
  $('logger').onclick = () => openPage(`logger-ui.html#_+${data.tabId}`);
  // Le impostazioni di uBlock stanno nella pagina delle impostazioni, sezione "Blocco contenuti".
  $('settings').onclick = () => {
    chrome.tabs.create({url: 'chrome://settings/adblock'});
    vAPI.closePopup();
  };

  refresh();
  setInterval(refresh, 1000);
})();
