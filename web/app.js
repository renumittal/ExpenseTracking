/* Simple expense diary. Plain JS, talks to the existing /api/ endpoints.
   Technical errors go to the console only; users only see simple messages. */
(function () {
  'use strict';

  // ---------- words the user sees ----------
  const CATS = [
    { key: 'LABOUR',        icon: '👷', hi: 'मज़दूर',   en: 'Labour',     type: 'Labour Payment',     party: 'LABOUR' },
    { key: 'CONTRACTOR',    icon: '🏗️', hi: 'ठेकेदार',  en: 'Contractor', type: 'Contractor Payment', party: 'CONTRACTOR' },
    { key: 'SUPPLIER',      icon: '🚚', hi: 'सप्लायर',  en: 'Supplier',   type: 'Supplier Payment',   party: 'SUPPLIER' },
    { key: 'MISCELLANEOUS', icon: '📦', hi: 'दूसरा खर्च', en: 'Other',      type: 'Other',              party: 'NONE' },
  ];
  const CAT = Object.fromEntries(CATS.map(c => [c.key, c]));
  const MODES = [
    { key: 'CASH', hi: 'नकद', en: 'Cash' },
    { key: 'UPI', hi: 'UPI', en: 'UPI' },
    { key: 'BANK_TRANSFER', hi: 'बैंक', en: 'Bank' },
    { key: 'CHEQUE', hi: 'चेक', en: 'Cheque' },
  ];
  const STATUS_HI = { PLANNED: 'शुरू होना बाकी', ONGOING: 'काम चालू है', COMPLETED: 'काम पूरा हो गया' };
  const MONTHS = ['जनवरी', 'फ़रवरी', 'मार्च', 'अप्रैल', 'मई', 'जून', 'जुलाई', 'अगस्त', 'सितंबर', 'अक्तूबर', 'नवंबर', 'दिसंबर'];
  const MSG = {
    problem: 'कुछ समस्या हुई। कृपया दोबारा प्रयास करें.',
    network: 'इंटरनेट नहीं चल रहा है। कृपया इंटरनेट देखकर दोबारा प्रयास करें.',
    login: 'नाम या पासवर्ड गलत है। कृपया दोबारा भरें.',
    saved: 'खर्च सफलतापूर्वक सेव हो गया.',
    noPermission: 'इस प्रोजेक्ट पर खर्च डालने की अनुमति नहीं है.',
  };

  // ---------- small helpers ----------
  // API address comes from config.js; empty means "same server" (local development).
  let API = (window.APP_CONFIG && window.APP_CONFIG.apiBase) || '../api/';
  if (!API.endsWith('/')) API += '/';
  if (!(window.APP_CONFIG && window.APP_CONFIG.apiBase) && /github\.io$/.test(location.hostname)) {
    console.error('web/config.js: apiBase is empty. Set it to the hosted Django API address.');
  }
  const $view = document.getElementById('view');
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const money = n => '₹ ' + Number(n || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 });
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode: ignore */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
  };
  const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const today = () => iso(new Date());
  const yesterday = () => { const d = new Date(); d.setDate(d.getDate() - 1); return iso(d); };
  const niceDate = s => { const [y, m, d] = String(s).split('-').map(Number); return y ? `${d} ${MONTHS[m - 1]} ${y}` : ''; };

  const state = { token: store.get('token'), me: null, projects: [], project: null, names: null };

  class AppError extends Error {
    constructor(kind, status, data) { super(kind); this.kind = kind; this.status = status; this.data = data; }
  }

  async function api(path, { method = 'GET', body } = {}) {
    const headers = { Accept: 'application/json' };
    if (state.token) headers.Authorization = 'Token ' + state.token;
    if (body) headers['Content-Type'] = 'application/json';
    let res;
    try {
      res = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
    } catch (e) {
      console.error('network error', path, e);
      throw new AppError('network');
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty body is fine */ }
    if (res.status === 401 && path !== 'auth/login/') {
      logoutLocal();
      location.hash = '#/login';
      throw new AppError('auth', 401);
    }
    if (!res.ok) {
      console.error('server error', method, path, res.status, data);
      throw new AppError('server', res.status, data);
    }
    return data;
  }

  const friendly = e => (e && e.kind === 'network' ? MSG.network : e && e.status === 403 ? MSG.noPermission : MSG.problem);
  const errBox = text => `<div class="msg error" role="alert">${esc(text)}</div>`;
  const loading = () => { $view.innerHTML = '<div class="spinner">⏳ रुकिए...</div>'; };

  function logoutLocal() {
    store.del('token'); store.del('projectId');
    Object.assign(state, { token: null, me: null, projects: [], project: null, names: null });
  }

  // ---------- data loading ----------
  async function loadBasics() {
    if (state.me) return;
    const me = await api('me/');
    const projects = me.owner_id ? await api('projects/') : [];
    state.me = me;
    state.projects = projects.results || projects;
    const saved = Number(store.get('projectId'));
    state.project = state.projects.find(p => p.id === saved) || state.projects[0] || null;
  }

  // Names for turning ids into words (labour / suppliers / contractors of this project).
  async function loadNames(force) {
    if (state.names && !force && state.names.projectId === state.project.id) return state.names;
    const [labour, suppliers, contracts] = await Promise.all([
      api('labour/'), api('suppliers/'), api(`contractor-contracts/?project=${state.project.id}`),
    ]);
    state.names = { projectId: state.project.id, labour, suppliers, contracts };
    return state.names;
  }

  // ---------- chrome (back button, tabs) ----------
  function chrome(tab, back) {
    const loggedIn = !!state.token && tab !== 'login';
    document.getElementById('tabs').hidden = !(loggedIn && tab !== 'home' && tab !== 'blocked');
    document.body.classList.toggle('no-tabs', document.getElementById('tabs').hidden);
    const top = document.getElementById('topbar');
    top.hidden = !back;
    if (back) document.getElementById('backBtn').setAttribute('href', back);
    document.querySelectorAll('.tabs a').forEach(a => a.classList.toggle('on', a.dataset.tab === tab));
    window.scrollTo(0, 0);
  }

  // ---------- screens ----------
  function screenLogin() {
    chrome('login');
    $view.innerHTML = `
      <h1>🙏 नमस्ते</h1>
      <p class="muted">अपना नाम और पासवर्ड भरिए.</p>
      <div id="err"></div>
      <form id="f" novalidate>
        <div class="step"><label for="u">आपका नाम <small>(User name)</small></label>
          <input id="u" type="text" autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false" enterkeyhint="next"></div>
        <div class="step"><label for="p" class="q">पासवर्ड <small>(Password)</small></label>
          <input id="p" type="password" autocomplete="current-password" enterkeyhint="go">
          <button type="button" class="btn line" id="show" style="min-height:52px;font-size:1rem">👁 पासवर्ड दिखाएँ</button></div>
        <button class="btn green big" type="submit">✅ अंदर जाएँ <span class="sub">LOGIN</span></button>
      </form>`;
    const p = document.getElementById('p');
    document.getElementById('show').onclick = e => {
      p.type = p.type === 'password' ? 'text' : 'password';
      e.currentTarget.textContent = p.type === 'password' ? '👁 पासवर्ड दिखाएँ' : '🙈 पासवर्ड छिपाएँ';
    };
    document.getElementById('f').onsubmit = async ev => {
      ev.preventDefault();
      const err = document.getElementById('err');
      const username = document.getElementById('u').value.trim();
      if (!username) { err.innerHTML = errBox('कृपया अपना नाम भरें.'); return; }
      if (!p.value) { err.innerHTML = errBox('कृपया पासवर्ड भरें.'); return; }
      const btn = ev.submitter || ev.target.querySelector('button[type=submit]');
      btn.disabled = true;
      try {
        const r = await api('auth/login/', { method: 'POST', body: { username, password: p.value } });
        state.token = r.token; store.set('token', r.token);
        location.hash = '#/home';
      } catch (e) {
        btn.disabled = false;
        err.innerHTML = errBox(e.status === 400 ? MSG.login : friendly(e));
      }
    };
  }

  function screenBlocked() {
    chrome('blocked');
    $view.innerHTML = `
      <div class="empty"><div class="ico">🙏</div>
        <h1>यह लॉगिन खर्च डालने के लिए नहीं है</h1>
        <p>कृपया मालिक वाले नाम और पासवर्ड से अंदर जाएँ.</p></div>
      <button class="btn line" id="out">🚪 बाहर निकलें <span class="sub">LOGOUT</span></button>`;
    document.getElementById('out').onclick = doLogout;
  }

  async function doLogout() {
    try { await api('auth/logout/', { method: 'POST' }); } catch (e) { /* still log out on this phone */ }
    logoutLocal();
    location.hash = '#/login';
  }

  function screenHome() {
    chrome('home');
    const proj = state.project;
    $view.innerHTML = `
      <h1>नमस्ते, ${esc(state.me.name)} 🙏</h1>
      <p class="muted">${proj ? 'प्रोजेक्ट: <b>' + esc(proj.name) + '</b>' : 'अभी कोई प्रोजेक्ट नहीं जुड़ा है.'}</p>
      <div class="home-grid">
        <a class="btn green big" href="#/add"><span class="ico">➕</span><span>खर्च डालें<span class="sub">Add Expense</span></span></a>
        <a class="btn big" href="#/list"><span class="ico">📋</span><span>खर्च देखें<span class="sub">View Expenses</span></span></a>
        <a class="btn big" href="#/project"><span class="ico">🏠</span><span>मेरा प्रोजेक्ट<span class="sub">My Project</span></span></a>
        <a class="btn big" href="#/reports"><span class="ico">📊</span><span>हिसाब देखें<span class="sub">Total Expense</span></span></a>
      </div>
      <button class="btn line" id="out" style="margin-top:28px">🚪 बाहर निकलें <span class="sub">LOGOUT</span></button>`;
    document.getElementById('out').onclick = doLogout;
  }

  async function screenProject() {
    chrome('home', '#/home');
    loading();
    try {
      const totals = await Promise.all(state.projects.map(p => api(`projects/${p.id}/summary/`).catch(() => null)));
      const many = state.projects.length > 1;
      $view.innerHTML = `<h1>🏠 मेरा प्रोजेक्ट <small>My Project</small></h1>` +
        (many ? '<p class="muted">जिस प्रोजेक्ट में काम करना है उसे छूइए.</p>' : '') +
        (state.projects.map((p, i) => `
          <${many ? 'button type="button"' : 'div'} class="card pick ${p.id === state.project.id ? 'on' : ''}" data-id="${p.id}">
            <div class="row"><b style="font-size:1.3rem">${esc(p.name)}</b>${many && p.id === state.project.id ? '<span class="pill">✔ चुना है</span>' : ''}</div>
            ${p.location ? `<div class="muted">📍 ${esc(p.location)}</div>` : ''}
            ${p.plot_size ? `<div class="muted">प्लॉट: ${esc(p.plot_size)}</div>` : ''}
            <div class="muted">${esc(STATUS_HI[p.status] || '')}${p.start_date ? ' · शुरू: ' + niceDate(p.start_date) : ''}</div>
            ${totals[i] ? `<div style="margin-top:8px">कुल खर्च <span class="amount">${money(totals[i].total_expense)}</span></div>` : ''}
          </${many ? 'button' : 'div'}>`).join('') || '<div class="empty">अभी कोई प्रोजेक्ट नहीं जुड़ा है.</div>');
      $view.querySelectorAll('button.pick').forEach(b => b.onclick = () => {
        state.project = state.projects.find(p => p.id === Number(b.dataset.id));
        store.set('projectId', state.project.id);
        state.names = null;
        location.hash = '#/home';
      });
    } catch (e) { $view.innerHTML = errBox(friendly(e)); }
  }

  // ----- Add expense -----
  async function screenAdd() {
    chrome('add', '#/home');
    if (!state.project) { $view.innerHTML = '<div class="empty"><div class="ico">🏠</div>अभी कोई प्रोजेक्ट नहीं जुड़ा है.</div>'; return; }
    loading();
    let names;
    try { names = await loadNames(); } catch (e) { $view.innerHTML = errBox(friendly(e)); return; }

    const f = { cat: '', amount: '', name: '', contractId: '', what: '', date: today(), mode: 'CASH', note: '' };
    let saving = false;
    $view.innerHTML = `
      <h1>➕ खर्च डालें <small>Add Expense</small></h1>
      <p class="muted">प्रोजेक्ट: <b>${esc(state.project.name)}</b></p>
      ${state.projects.length > 1 ? '<a class="btn line" href="#/project" style="min-height:56px;font-size:1.05rem">🔁 प्रोजेक्ट बदलें <span class="sub">Change Project</span></a>' : ''}
      <div id="top"></div>
      <div class="step" id="s-cat"><div class="q"><span class="num">1</span>किस चीज़ का खर्च है?</div>
        <div class="choices">${CATS.map(c => `<button type="button" class="choice" data-cat="${c.key}" aria-pressed="false"><span class="ico">${c.icon}</span>${c.hi}<br><small>${c.en}</small></button>`).join('')}</div>
        <div class="field-error" id="e-cat"></div></div>
      <div class="step" id="s-amt"><label for="amt"><span class="num">2</span>कितना पैसा? <small>Amount</small></label>
        <div class="rupee"><span>₹</span><input id="amt" type="text" inputmode="decimal" pattern="[0-9.]*" autocomplete="off" enterkeyhint="next" placeholder="0"></div>
        <div class="words" id="words"></div><div class="field-error" id="e-amt"></div></div>
      <div class="step" id="s-who"></div>
      <div class="step" id="s-date"><label for="date"><span class="num">4</span>कब दिया? <small>Date</small></label>
        <input id="date" type="date" value="${f.date}">
        <div class="quick"><button type="button" class="choice" data-d="0">आज<br><small>Today</small></button><button type="button" class="choice" data-d="1">कल<br><small>Yesterday</small></button></div>
        <div class="field-error" id="e-date"></div></div>
      <div class="step"><div class="q"><span class="num">5</span>कैसे दिया? <small>Paid by</small></div>
        <div class="choices small">${MODES.map(m => `<button type="button" class="choice" data-mode="${m.key}" aria-pressed="${m.key === 'CASH'}">${m.hi}<br><small>${m.en}</small></button>`).join('')}</div></div>
      <div class="step"><label for="note"><span class="num">6</span>कोई जानकारी? <small>Note (optional)</small></label>
        <textarea id="note" placeholder="जैसे: सीमेंट के 10 बैग"></textarea></div>
      <div id="bottom"></div>
      <button class="btn green big" id="save" type="button">💾 खर्च सेव करें <span class="sub">SAVE EXPENSE</span></button>`;

    const $ = id => document.getElementById(id);
    const setErr = (id, text) => { $(id).textContent = text || ''; $(id).parentElement.classList.toggle('bad', !!text); };

    function drawWho() {
      const cat = f.cat;
      let html = '';
      if (!cat) {
        html = '<div class="q"><span class="num">3</span>किसको दिया? <small>Name</small></div><p class="muted">पहले ऊपर बताइए कि किस चीज़ का खर्च है.</p>';
      } else if (cat === 'CONTRACTOR') {
        html = `<label for="contract"><span class="num">3</span>किस ठेकेदार को दिया? <small>Contractor</small></label>` +
          (names.contracts.length
            ? `<select id="contract"><option value="">— ठेकेदार चुनिए —</option>${names.contracts.map(c => `<option value="${c.id}">${esc(c.contractor_name)}</option>`).join('')}</select>`
            : '<div class="msg info">इस प्रोजेक्ट में अभी कोई ठेकेदार नहीं जुड़ा है। कृपया एडमिन से जुड़वाइए.</div>') +
          '<div class="field-error" id="e-who"></div>';
      } else {
        const label = cat === 'LABOUR' ? 'किस मज़दूर को दिया?' : cat === 'SUPPLIER' ? 'किस सप्लायर को दिया?' : 'किसको दिया?';
        html = `<label for="name"><span class="num">3</span>${label} <small>Name</small></label>
          <input id="name" type="text" autocomplete="off" autocapitalize="words" enterkeyhint="next" placeholder="नाम लिखिए">
          <div class="suggest" id="sug"></div><div class="hint" id="newhint"></div><div class="field-error" id="e-who"></div>` +
          (cat === 'MISCELLANEOUS' ? `<label for="what" style="margin-top:18px;display:block;font-weight:700">किस काम का? <small>(optional, जैसे: चाय, ट्रांसपोर्ट)</small></label><input id="what" type="text" autocomplete="off" autocapitalize="sentences" enterkeyhint="next">` : '');
      }
      $('s-who').innerHTML = html;
      f.name = ''; f.contractId = ''; f.what = '';
      if ($('contract')) $('contract').onchange = e => { f.contractId = e.target.value; setErr('e-who', ''); };
      if ($('what')) $('what').oninput = e => { f.what = e.target.value; };
      if ($('name')) $('name').oninput = e => { f.name = e.target.value; setErr('e-who', ''); drawSuggest(); };
    }

    function knownNames() {
      return (f.cat === 'LABOUR' ? names.labour : f.cat === 'SUPPLIER' ? names.suppliers : []).map(x => x.name);
    }
    function drawSuggest() {
      const list = knownNames(), typed = f.name.trim().toLowerCase();
      const exact = list.some(n => n.toLowerCase() === typed);
      const matches = list.filter(n => typed && n.toLowerCase().includes(typed) && n.toLowerCase() !== typed).slice(0, 5);
      $('sug').innerHTML = matches.map(n => `<button type="button">${esc(n)}</button>`).join('');
      $('sug').querySelectorAll('button').forEach(b => b.onclick = () => { f.name = b.textContent; $('name').value = f.name; drawSuggest(); });
      $('newhint').textContent = f.cat !== 'MISCELLANEOUS' && typed && !exact ? '✚ यह नया नाम जुड़ जाएगा.' : '';
    }

    document.querySelectorAll('[data-cat]').forEach(b => b.onclick = () => {
      f.cat = b.dataset.cat;
      document.querySelectorAll('[data-cat]').forEach(x => x.setAttribute('aria-pressed', x === b));
      setErr('e-cat', '');
      drawWho();
    });
    document.querySelectorAll('[data-mode]').forEach(b => b.onclick = () => {
      f.mode = b.dataset.mode;
      document.querySelectorAll('[data-mode]').forEach(x => x.setAttribute('aria-pressed', x === b));
    });
    document.querySelectorAll('[data-d]').forEach(b => b.onclick = () => { $('date').value = b.dataset.d === '0' ? today() : yesterday(); setErr('e-date', ''); });
    $('amt').oninput = e => {
      e.target.value = e.target.value.replace(/[^0-9.]/g, '').replace(/(\..*)\./g, '$1');
      f.amount = e.target.value;
      $('words').textContent = Number(f.amount) > 0 ? money(f.amount) : '';
      setErr('e-amt', '');
    };
    drawWho();

    function firstError() {
      const amt = Number(f.amount);
      const fail = (id, text, scrollTo) => { setErr(id, text); $(scrollTo).scrollIntoView({ behavior: 'smooth', block: 'center' }); return true; };
      if (!f.cat) return fail('e-cat', 'कृपया बताइए किस चीज़ का खर्च है.', 's-cat');
      if (!f.amount || !(amt > 0)) return fail('e-amt', 'कृपया राशि भरें.', 's-amt');
      if (amt >= 1e10) return fail('e-amt', 'राशि बहुत बड़ी है। कृपया जाँच लें.', 's-amt');
      if (f.cat === 'CONTRACTOR' && !f.contractId) return fail('e-who', 'कृपया ठेकेदार चुनिए.', 's-who');
      if (f.cat !== 'CONTRACTOR' && !f.name.trim()) return fail('e-who', 'कृपया नाम भरें.', 's-who');
      if (!$('date').value) return fail('e-date', 'कृपया तारीख चुनिए.', 's-date');
      return false;
    }

    $('save').onclick = async () => {
      if (saving) return;
      $('top').innerHTML = ''; $('bottom').innerHTML = '';
      if (firstError()) return;
      saving = true; $('save').disabled = true; $('save').firstChild.textContent = '⏳ सेव हो रहा है... ';
      try {
        const cat = CAT[f.cat];
        const body = {
          project: state.project.id,
          expense_date: $('date').value,
          expense_category: f.cat,
          expense_type: f.cat === 'MISCELLANEOUS' ? (f.what.trim() || cat.type) : cat.type,
          party_type: cat.party,
          paid_by_owner: state.me.owner_id,
          amount: Number(f.amount).toFixed(2),
          payment_mode: f.mode,
          remarks: $('note').value.trim() || null,
        };
        if (f.cat === 'CONTRACTOR') body.contractor_contract = Number(f.contractId);
        else if (f.cat === 'MISCELLANEOUS') body.payee_name = f.name.trim();
        else {
          const list = f.cat === 'LABOUR' ? names.labour : names.suppliers;
          const typed = f.name.trim();
          let match = list.find(x => x.name.toLowerCase() === typed.toLowerCase());
          if (!match) match = await api(f.cat === 'LABOUR' ? 'labour/' : 'suppliers/', { method: 'POST', body: { name: typed } });
          body[f.cat === 'LABOUR' ? 'labour' : 'supplier'] = match.id;
        }
        await api('expense-transactions/', { method: 'POST', body });
        state.names = null;
        sessionStorage.setItem('justSaved', JSON.stringify({ amount: body.amount, who: f.cat === 'CONTRACTOR' ? names.contracts.find(c => String(c.id) === f.contractId).contractor_name : f.name.trim() }));
        location.hash = '#/done';
      } catch (e) {
        saving = false; $('save').disabled = false; $('save').firstChild.textContent = '💾 खर्च सेव करें ';
        $('bottom').innerHTML = errBox(friendly(e));
        $('bottom').scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    };
  }

  function screenDone() {
    chrome('add');
    let info = null;
    try { info = JSON.parse(sessionStorage.getItem('justSaved')); sessionStorage.removeItem('justSaved'); } catch (e) { /* ignore */ }
    $view.innerHTML = `
      <div class="success"><div class="tick">✅</div><h1>${MSG.saved}</h1>
        ${info ? `<div class="card"><div class="amount">${money(info.amount)}</div><div class="muted">${esc(info.who)}</div></div>` : ''}</div>
      <a class="btn green big" href="#/add">➕ एक और खर्च डालें <span class="sub">Add Another</span></a>
      <a class="btn line" href="#/list">📋 खर्च देखें <span class="sub">View Expenses</span></a>
      <a class="btn line" href="#/home">🏠 होम पर जाएँ <span class="sub">Home</span></a>`;
  }

  // ----- Expenses list -----
  let listState = { cat: '', shown: 20 };

  async function screenList(params) {
    chrome('list', '#/home');
    if (!state.project) { $view.innerHTML = '<div class="empty"><div class="ico">🏠</div>अभी कोई प्रोजेक्ट नहीं जुड़ा है.</div>'; return; }
    listState = { cat: params.get('cat') || '', shown: 20 };
    loading();
    let rows, names;
    try {
      [names, rows] = await Promise.all([
        loadNames(true),
        api(`reports/payment-register/?project=${state.project.id}&page_size=500`),
      ]);
      rows = rows.results || rows;
    } catch (e) { $view.innerHTML = errBox(friendly(e)); return; }

    const nameOf = r => {
      if (r.labour) return (names.labour.find(x => x.id === r.labour) || {}).name;
      if (r.supplier) return (names.suppliers.find(x => x.id === r.supplier) || {}).name;
      if (r.contractor_contract) return (names.contracts.find(x => x.id === r.contractor_contract) || {}).contractor_name;
      return r.payee_name;
    };

    function draw() {
      const shown = rows.filter(r => !listState.cat || r.expense_category === listState.cat);
      const total = shown.reduce((s, r) => s + Number(r.amount), 0);
      const chip = (key, label) => `<button type="button" class="choice" data-c="${key}" aria-pressed="${listState.cat === key}">${label}</button>`;
      $view.innerHTML = `
        <h1>📋 खर्च देखें <small>View Expenses</small></h1>
        <div class="chips">${chip('', 'सब')}${CATS.map(c => chip(c.key, `${c.icon} ${c.hi}`)).join('')}</div>
        <div class="card"><div class="row"><span>कुल खर्च <small>Total</small></span><span class="amount">${money(total)}</span></div></div>` +
        (shown.length ? shown.slice(0, listState.shown).map(r => `
          <div class="card item">
            <div class="row"><span class="who">${esc(nameOf(r) || '—')}</span><span class="amount">${money(r.amount)}</span></div>
            <div class="meta">${CAT[r.expense_category].icon} ${CAT[r.expense_category].hi}${r.expense_category === 'MISCELLANEOUS' && r.expense_type !== 'Other' ? ' · ' + esc(r.expense_type) : ''} · ${niceDate(r.expense_date)}</div>
            ${r.remarks ? `<div class="note">📝 ${esc(r.remarks)}</div>` : ''}
          </div>`).join('') : `<div class="empty"><div class="ico">📭</div><p>अभी कोई खर्च नहीं है.</p><a class="btn green" href="#/add">➕ खर्च डालें <span class="sub">Add Expense</span></a></div>`) +
        (shown.length > listState.shown ? '<button type="button" class="btn line" id="more">⬇ और दिखाएँ <span class="sub">Show more</span></button>' : '');
      $view.querySelectorAll('[data-c]').forEach(b => b.onclick = () => { listState.cat = b.dataset.c; listState.shown = 20; draw(); });
      const more = document.getElementById('more');
      if (more) more.onclick = () => { listState.shown += 20; draw(); };
    }
    draw();
  }

  // ----- Reports -----
  async function screenReports() {
    chrome('reports', '#/home');
    if (!state.project) { $view.innerHTML = '<div class="empty"><div class="ico">🏠</div>अभी कोई प्रोजेक्ट नहीं जुड़ा है.</div>'; return; }
    loading();
    let d;
    try { d = await api(`projects/${state.project.id}/dashboard/`); } catch (e) { $view.innerHTML = errBox(friendly(e)); return; }
    const total = Number(d.total_expense);
    const pct = v => (total > 0 ? Math.round((Number(v) / total) * 100) : 0);

    $view.innerHTML = `
      <h1>📊 हिसाब देखें <small>Total Expense</small></h1>
      <p class="muted">प्रोजेक्ट: <b>${esc(state.project.name)}</b></p>
      <div class="card"><div class="muted">कुल खर्च <small>Total Expense</small></div><div class="big-total">${money(total)}</div></div>
      <h2>किस पर कितना खर्च हुआ</h2>
      ${CATS.map(c => `
        <a class="card" href="#/report/${c.key}">
          <div class="row"><b>${c.icon} ${c.hi} <small>${c.en}</small></b><span class="amount">${money(d.category_breakup[c.key])}</span></div>
          <div class="bar"><i style="width:${pct(d.category_breakup[c.key])}%"></i></div>
          <div class="muted" style="margin-top:6px">${pct(d.category_breakup[c.key])}% · देखने के लिए छूइए ›</div>
        </a>`).join('')}
      ${d.owner_contribution.length ? `<h2>मालिक का हिस्सा <small>Owner Share</small></h2>` + d.owner_contribution.map(o => `
        <div class="card"><div class="row"><b>${esc(o.owner_name)}</b><span class="amount">${money(o.total)}</span></div>
        <div class="muted">कुल खर्च का ${pct(o.total)}% दिया</div></div>`).join('') : ''}
      ${d.contractor_positions.length ? `<h2>ठेकेदार का हिसाब</h2>` + d.contractor_positions.map(c => {
        const bal = Number(c.balance);
        return `<div class="card"><b>${esc(c.contractor_name)}</b>
          <div class="row"><span class="muted">पूरा काम</span><span>${money(c.contract_amount)}</span></div>
          <div class="row"><span class="muted">अब तक दिया</span><span>${money(c.paid_amount)}</span></div>
          <div class="row"><b>${bal < 0 ? 'ज़्यादा दिया' : 'देना बाकी'}</b><span class="pill ${bal < 0 ? 'warn' : ''}" style="font-size:1.1rem">${money(Math.abs(bal))}</span></div></div>`;
      }).join('') : ''}`;
  }

  async function screenReportOne(key) {
    const c = CAT[key];
    if (!c || !state.project) { location.hash = '#/reports'; return; }
    chrome('reports', '#/reports');
    loading();
    const q = `?project=${state.project.id}`;
    try {
      let rows, total;
      if (key === 'LABOUR') { const r = await api('reports/labour/' + q); rows = r.labour.map(x => [x.labour_name, x.total_paid]); total = r.grand_total; }
      else if (key === 'SUPPLIER') { const r = await api('reports/supplier/' + q); rows = r.suppliers.map(x => [x.supplier_name, x.total_paid]); total = r.grand_total; }
      else if (key === 'CONTRACTOR') { const r = await api('reports/contractor/' + q); rows = r.contractor_totals.map(x => [x.contractor_name, x.paid_amount]); total = rows.reduce((s, x) => s + Number(x[1]), 0); }
      else { const r = await api('reports/misc/' + q); rows = r.expense_types.map(x => [x.expense_type, x.total]); total = r.grand_total; }
      $view.innerHTML = `
        <h1>${c.icon} ${c.hi} <small>${c.en}</small></h1>
        <div class="card"><div class="muted">कुल खर्च <small>Total</small></div><div class="big-total">${money(total)}</div></div>` +
        (rows.length ? rows.map(([n, v]) => `<div class="card"><div class="row"><b>${esc(n)}</b><span class="amount">${money(v)}</span></div></div>`).join('')
          : '<div class="empty"><div class="ico">📭</div>अभी कोई खर्च नहीं है.</div>') +
        `<a class="btn line" href="#/list?cat=${key}">📋 सारे खर्च देखें <span class="sub">View all</span></a>`;
    } catch (e) { $view.innerHTML = errBox(friendly(e)); }
  }

  // ---------- router ----------
  async function route() {
    const [path, qs] = (location.hash.replace(/^#/, '') || '/home').split('?');
    const params = new URLSearchParams(qs || '');
    if (!state.token) { if (path !== '/login') { location.hash = '#/login'; return; } return screenLogin(); }
    if (path === '/login') { location.hash = '#/home'; return; }
    try { await loadBasics(); } catch (e) { if (e.kind !== 'auth') { chrome('blocked'); $view.innerHTML = errBox(friendly(e)); } return; }
    if (!state.me.owner_id) return screenBlocked();

    const [, page, arg] = path.split('/');
    switch (page) {
      case 'add': return screenAdd();
      case 'done': return screenDone();
      case 'list': return screenList(params);
      case 'reports': return screenReports();
      case 'report': return screenReportOne(arg);
      case 'project': return screenProject();
      default: return screenHome();
    }
  }

  // On phones the on-screen keyboard shrinks the page: hide the bottom bar while typing.
  const typing = el => el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName);
  document.addEventListener('focusin', e => { if (typing(e.target)) document.body.classList.add('typing'); });
  document.addEventListener('focusout', () => setTimeout(() => { if (!typing(document.activeElement)) document.body.classList.remove('typing'); }, 50));

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(e => console.error('service worker', e)));
  }

  window.addEventListener('hashchange', route);
  route();
})();
