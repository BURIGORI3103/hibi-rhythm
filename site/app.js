/* 日々リズム：暗号化データをブラウザ内で復号して表示する。外部へは何も送信しない。
   暗号形式は pipeline/encrypt.py と対になる。変更するときは両方を同時に直すこと。 */
(() => {
  'use strict';

  const STORE_KEY = 'hibi-rhythm-passphrase';
  const DOW = ['日', '月', '火', '水', '木', '金', '土'];
  const SLEEP_LABEL = { none: '記録なし', unsynced: '未同期', missing: '未取得' };
  const SLEEP_HINT = {
    none: 'この日の睡眠記録がありません', unsynced: 'まだ同期されていません', missing: 'まだ取り込まれていません',
  };
  const state = { days: [], byDate: new Map(), index: 0, range: 14 };

  const $ = (id) => document.getElementById(id);
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  /* ---------- 表記 ---------- */
  const pad = (n) => String(n).padStart(2, '0');
  const whole = (min) => Math.floor(min + 1e-9);
  function hm(min) {
    const m = whole(min);
    return m >= 60 ? `${Math.floor(m / 60)}時間${m % 60}分` : `${m}分`;
  }
  function short(min) {
    const m = whole(min);
    return `${Math.floor(m / 60)}:${pad(m % 60)}`;
  }
  function clock(min) {
    const m = Math.round(min);
    return `${pad(Math.floor(m / 60))}:${pad(m % 60)}`;
  }
  function durNode(min) {
    const frag = document.createDocumentFragment();
    const m = whole(min);
    if (m >= 60) frag.append(String(Math.floor(m / 60)), el('small', null, '時間'));
    frag.append(String(m % 60), el('small', null, '分'));
    return frag;
  }
  function parseDate(text) {
    const [y, m, d] = text.split('-').map(Number);
    return { y, m, d, dow: DOW[new Date(y, m - 1, d).getDay()] };
  }
  function longDate(text) {
    const p = parseDate(text);
    return `${p.y}年${p.m}月${p.d}日（${p.dow}）`;
  }
  function addDays(text, count) {
    const p = parseDate(text);
    const t = new Date(p.y, p.m - 1, p.d + count);
    return `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())}`;
  }

  /* ---------- 値の取り出し（不明は null のまま扱う） ---------- */
  function sleepRange(sleep) {
    if (!sleep || sleep.upper == null) return null;
    return [sleep.lower == null ? sleep.upper : sleep.lower, sleep.upper];
  }
  function sleepText(sleep, format) {
    const range = sleepRange(sleep);
    if (!range) return SLEEP_LABEL[sleep ? sleep.state : 'missing'] || '未取得';
    return range[0] === range[1] ? format(range[1]) : `${format(range[0])}〜${format(range[1])}`;
  }
  function pcMinutes(pc) {
    return pc && pc.active != null && pc.state !== 'none' ? pc.active / 60 : null;
  }
  function pcText(pc, format) {
    if (!pc || pc.state === 'missing') return '未取得';
    if (pc.state === 'none') return '記録なし';
    return format(pc.active / 60);
  }
  const appName = (name) => name.replace(/\.exe$/i, '');

  /* ---------- タイムライン ---------- */
  function block(a, b, cls, tip) {
    const node = el('span', `blk ${cls}`);
    node.style.left = `${(a / 1440) * 100}%`;
    node.style.width = `${((b - a) / 1440) * 100}%`;
    node.dataset.tip = tip;
    return node;
  }
  function lanes(rec) {
    const wrap = el('span', 'lanes');
    const sleep = el('span', 'lane');
    const pc = el('span', 'lane');
    if (rec) {
      for (const [a, b, kind] of rec.sleep.segments) {
        const name = kind === 'u' ? '睡眠（実睡眠は不明）' : '睡眠';
        sleep.append(block(a, b, kind === 'u' ? 'is-soft' : 'is-sleep', `${name}\n${clock(a)}〜${clock(b)}（${hm(b - a)}）`));
      }
      for (const [a, b] of rec.pc.segments) {
        pc.append(block(a, b, 'is-pc', `PC稼働\n${clock(a)}〜${clock(b)}（${hm(b - a)}）`));
      }
    }
    wrap.append(sleep, pc);
    return wrap;
  }
  function hoursAxis() {
    const axis = el('span', 'hours');
    [0, 6, 12, 18, 24].forEach((hour) => {
      const label = el('span', hour === 0 ? 'is-first' : hour === 24 ? 'is-last' : null, String(hour));
      label.style.left = `${(hour / 24) * 100}%`;
      axis.append(label);
    });
    return axis;
  }
  function badge(text, warn) {
    return el('span', warn ? 'badge is-warn' : 'badge', text);
  }

  /* ---------- その日のサマリーと詳細 ---------- */
  function renderDay() {
    const rec = state.days[state.index];
    $('day-title').textContent = longDate(rec.date);
    $('prev').disabled = state.index === 0;
    $('next').disabled = state.index === state.days.length - 1;

    const sleepValue = $('sleep-value');
    const sleepNote = $('sleep-note');
    const range = sleepRange(rec.sleep);
    sleepValue.replaceChildren();
    sleepValue.classList.toggle('is-unknown', !range);
    if (!range) {
      sleepValue.textContent = SLEEP_LABEL[rec.sleep.state] || '未取得';
      sleepNote.replaceChildren(el('span', null, SLEEP_HINT[rec.sleep.state] || SLEEP_HINT.missing));
    } else {
      if (range[0] === range[1]) sleepValue.append(durNode(range[1]));
      else sleepValue.append(durNode(range[0]), '〜', document.createElement('wbr'), durNode(range[1]));
      sleepNote.replaceChildren(el('span', null, '0〜24時に眠っていた時間'));
      if (rec.sleep.state === 'partial') sleepNote.append(badge('一部不明'));
    }

    const pcValue = $('pc-value');
    const pcNote = $('pc-note');
    const minutes = pcMinutes(rec.pc);
    pcValue.replaceChildren();
    pcValue.classList.toggle('is-unknown', minutes == null);
    if (minutes == null) {
      pcValue.textContent = pcText(rec.pc);
      pcNote.replaceChildren(el('span', null, rec.pc.state === 'none' ? 'PCの記録がない日です' : 'まだ取り込まれていません'));
    } else {
      pcValue.append(durNode(minutes));
      pcNote.replaceChildren(el('span', null, '操作中と判定された時間'));
      if (rec.pc.state === 'caution') pcNote.append(badge('参考値', true));
    }

    const names = el('div', 'lane-names');
    names.append(el('span', null, '睡眠'), el('span', null, 'PC'));
    $('day-timeline').replaceChildren(names, lanes(rec), hoursAxis());
    renderDetail(rec);
  }

  function fact(list, term, value) {
    list.append(el('dt', null, term), el('dd', null, value));
  }

  function renderDetail(rec) {
    const box = $('detail');
    box.replaceChildren();

    const sleepBox = el('div');
    sleepBox.append(el('h3', null, '睡眠の内訳（この日に目覚めた分）'));
    const sleep = rec.sleep;
    const facts = el('dl', 'facts');
    if (sleep.bed && sleep.wake) fact(facts, '就寝 → 起床', `${sleep.bed} → ${sleep.wake}`);
    if (sleep.night != null) fact(facts, '眠っていた時間', hm(sleep.night));
    if (rec.steps != null) fact(facts, '歩数', `${rec.steps.toLocaleString('ja-JP')}歩`);
    if (facts.children.length) sleepBox.append(facts);
    else sleepBox.append(el('p', 'empty', '内訳の記録がありません'));
    const stages = [['深い睡眠', 'stage-deep', sleep.deep], ['レム睡眠', 'stage-rem', sleep.rem], ['浅い睡眠', 'stage-light', sleep.light]]
      .filter((stage) => stage[2] != null && stage[2] > 0);
    if (stages.length) {
      const bar = el('div', 'stages');
      const legend = el('ul', 'legend');
      for (const [name, cls, value] of stages) {
        const part = el('span', cls);
        part.style.flex = `${value} 1 0`;
        part.dataset.tip = `${name}\n${hm(value)}`;
        bar.append(part);
        const item = el('li');
        item.append(el('span', `key ${cls}`), `${name} ${hm(value)}`);
        legend.append(item);
      }
      bar.setAttribute('role', 'img');
      bar.setAttribute('aria-label', stages.map((stage) => `${stage[0]} ${hm(stage[2])}`).join('、'));
      sleepBox.append(bar, legend);
    }

    const pcBox = el('div');
    pcBox.append(el('h3', null, 'PCで使ったアプリ'));
    const pc = rec.pc;
    if (pc.apps && pc.apps.length && pc.active > 0) {
      const list = el('div', 'apps');
      for (const app of pc.apps) {
        if (app.seconds < 60) continue;
        const row = el('div', 'app');
        const bar = el('div', 'app-bar');
        const fill = el('span');
        fill.style.width = `${Math.max((app.seconds / pc.active) * 100, 0.5)}%`;
        bar.append(fill);
        row.append(el('span', 'app-name', appName(app.name)), el('span', 'app-time', hm(app.seconds / 60)), bar);
        list.append(row);
      }
      const hidden = pc.apps.filter((app) => app.seconds < 60);
      const restCount = (pc.apps_other ? pc.apps_other.count : 0) + hidden.length;
      const restSeconds = (pc.apps_other ? pc.apps_other.seconds : 0) + hidden.reduce((sum, app) => sum + app.seconds, 0);
      if (list.children.length) pcBox.append(list);
      if (restCount) pcBox.append(el('p', 'empty', `ほか${restCount}件（合計 ${restSeconds < 60 ? '1分未満' : hm(restSeconds / 60)}）`));
    } else {
      pcBox.append(el('p', 'empty', 'アプリの記録がありません'));
    }
    box.append(sleepBox, pcBox);

    const caveat = el('div', 'caveat');
    if (pc.state === 'caution') {
      caveat.append(el('p', null, `PC：操作中の判定が${hm(pc.longest_not_afk / 60)}途切れずに続いた区間があります。機器の影響で席を離れていた時間も含まれている可能性があるため、参考値としています。`));
    }
    for (const warning of rec.warnings || []) caveat.append(el('p', null, `確認中：${warning}`));
    if (sleep.note) {
      const memo = el('details');
      memo.append(el('summary', null, '睡眠集計のメモ'), el('p', null, sleep.note));
      caveat.append(memo);
    }
    if (caveat.children.length) box.append(caveat);
  }

  /* ---------- リズム表 ---------- */
  function latestDate() {
    return state.days[state.days.length - 1].date;
  }
  function renderRhythm() {
    const box = $('rhythm');
    box.replaceChildren();
    const head = el('div', 'rhythm-head');
    head.append(el('span'), hoursAxis(), el('span'));
    box.append(head);
    const selected = state.days[state.index].date;
    for (let i = 0; i < state.range; i += 1) {
      const date = addDays(latestDate(), -i);
      const rec = state.byDate.get(date);
      const p = parseDate(date);
      const row = el('button', 'row');
      row.type = 'button';
      const label = el('span', 'row-date', `${p.m}/${p.d}`);
      label.append(el('small', null, p.dow));
      const totals = el('span', 'row-totals');
      const sleepLine = el('span');
      const pcLine = el('span');
      sleepLine.append(el('b', null, '睡'), rec ? sleepText(rec.sleep, short) : '—');
      pcLine.append(el('b', null, 'PC'), rec ? pcText(rec.pc, short) + (rec.pc.state === 'caution' ? ' ※' : '') : '—');
      totals.append(sleepLine, pcLine);
      if (rec) {
        row.dataset.date = date;
        row.setAttribute('aria-label', `${longDate(date)} 睡眠 ${sleepText(rec.sleep, hm)}、PC ${pcText(rec.pc, hm)}`);
        if (date === selected) {
          row.classList.add('is-selected');
          row.setAttribute('aria-current', 'date');
        }
      } else {
        row.disabled = true;
      }
      row.append(label, lanes(rec), totals);
      box.append(row);
    }
  }

  /* ---------- 推移 ---------- */
  function average(values) {
    return values.reduce((sum, value) => sum + value, 0) / values.length;
  }
  function chart(title, keyClass, avgText, top, dates, tips, bars) {
    const wrap = el('div', 'chart');
    const head = el('div', 'chart-head');
    head.append(el('span', `key ${keyClass}`), el('span', null, title), el('span', 'avg', avgText));
    const plot = el('div', 'plot');
    for (let minutes = 0; minutes <= top; minutes += 360) {
      const tick = el('div', minutes === 0 ? 'tick is-base' : 'tick');
      tick.style.bottom = `${(minutes / top) * 100}%`;
      tick.append(el('span', null, `${minutes / 60}時間`));
      plot.append(tick);
    }
    const cols = el('div', 'cols');
    const labels = el('div', 'xlabels');
    const step = dates.length <= 14 ? 2 : 5;
    const selected = state.days[state.index].date;
    dates.forEach((date, i) => {
      const col = el('button', 'col');
      col.type = 'button';
      col.dataset.tip = tips[i];
      col.setAttribute('aria-label', tips[i].replace('\n', ' '));
      if (state.byDate.has(date)) col.dataset.date = date;
      else col.disabled = true;
      if (date === selected) col.classList.add('is-selected');
      col.append(bars[i] || el('span', 'none-mark'));
      cols.append(col);
      const p = parseDate(date);
      labels.append(el('span', null, (dates.length - 1 - i) % step === 0 ? `${p.m}/${p.d}` : ''));
    });
    plot.append(cols);
    wrap.append(head, plot, labels);
    return wrap;
  }
  function renderTrend() {
    const box = $('trend');
    const dates = [];
    for (let i = state.range - 1; i >= 0; i -= 1) dates.push(addDays(latestDate(), -i));
    const recs = dates.map((date) => state.byDate.get(date) || null);
    const sleeps = recs.map((rec) => (rec ? sleepRange(rec.sleep) : null));
    const pcs = recs.map((rec) => (rec ? pcMinutes(rec.pc) : null));
    const peak = Math.max(720, ...sleeps.filter(Boolean).map((range) => range[1]), ...pcs.filter((value) => value != null));
    const top = Math.ceil(peak / 360) * 360;
    const dayLabel = (date) => {
      const p = parseDate(date);
      return `${p.m}/${p.d}（${p.dow}）`;
    };

    const knownSleep = sleeps.filter(Boolean);
    let sleepAvg = '記録のある日がありません';
    if (knownSleep.length) {
      const low = average(knownSleep.map((range) => range[0]));
      const high = average(knownSleep.map((range) => range[1]));
      sleepAvg = `平均 ${whole(low) === whole(high) ? hm(high) : `${hm(low)}〜${hm(high)}`}（記録のある${knownSleep.length}日）`;
    }
    const sleepBars = sleeps.map((range) => {
      if (!range) return null;
      const bar = el('span', 'bar');
      bar.style.height = `${(range[1] / top) * 100}%`;
      if (range[1] > range[0]) {
        const soft = el('span', 'bar-soft');
        soft.style.height = `${((range[1] - range[0]) / range[1]) * 100}%`;
        bar.append(soft);
      }
      bar.append(el('span', 'bar-solid'));
      return bar;
    });
    const sleepTips = dates.map((date, i) => `${dayLabel(date)}\n睡眠 ${recs[i] ? sleepText(recs[i].sleep, hm) : '未取得'}`);

    const knownPc = pcs.filter((value) => value != null);
    const pcAvg = knownPc.length ? `平均 ${hm(average(knownPc))}（記録のある${knownPc.length}日）` : '記録のある日がありません';
    const pcBars = pcs.map((value) => {
      if (value == null) return null;
      const bar = el('span', 'bar bar-pc');
      bar.style.height = `${(value / top) * 100}%`;
      return bar;
    });
    const pcTips = dates.map((date, i) => {
      const rec = recs[i];
      return `${dayLabel(date)}\nPC ${rec ? pcText(rec.pc, hm) : '未取得'}${rec && rec.pc.state === 'caution' ? '（参考値）' : ''}`;
    });

    box.replaceChildren(
      chart('睡眠（0〜24時）', 'key-sleep', sleepAvg, top, dates, sleepTips, sleepBars),
      chart('PC（操作中と判定された時間）', 'key-pc', pcAvg, top, dates, pcTips, pcBars),
    );
  }

  /* ---------- 表 ---------- */
  function renderTable() {
    const table = el('table');
    const head = el('tr');
    ['日付', '睡眠（0〜24時）', '就寝', '起床', 'PC', '歩数', '備考'].forEach((name) => head.append(el('th', null, name)));
    const thead = el('thead');
    thead.append(head);
    const body = el('tbody');
    for (const rec of [...state.days].reverse()) {
      const p = parseDate(rec.date);
      const notes = [];
      if (sleepRange(rec.sleep) && rec.sleep.state === 'partial') notes.push('睡眠は一部不明');
      if (rec.pc.state === 'caution') notes.push('PCは参考値');
      const row = el('tr');
      [`${p.y}/${pad(p.m)}/${pad(p.d)}（${p.dow}）`, sleepText(rec.sleep, hm), rec.sleep.bed || '—', rec.sleep.wake || '—',
        pcText(rec.pc, hm), rec.steps == null ? '—' : rec.steps.toLocaleString('ja-JP'), notes.join('、')]
        .forEach((value) => row.append(el('td', null, value)));
      body.append(row);
    }
    table.append(thead, body);
    $('table').replaceChildren(table);
  }

  function renderAll() {
    renderDay();
    renderRhythm();
    renderTrend();
    document.querySelectorAll('.seg button').forEach((button) => {
      button.setAttribute('aria-pressed', String(Number(button.dataset.range) === state.range));
    });
  }
  function select(date) {
    const index = state.days.findIndex((rec) => rec.date === date);
    if (index < 0) return;
    state.index = index;
    renderAll();
  }

  /* ---------- 復号 ---------- */
  function bytes(base64) {
    return Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
  }
  async function decrypt(envelope, passphrase) {
    const encoder = new TextEncoder();
    const material = await crypto.subtle.importKey('raw', encoder.encode(passphrase.normalize('NFKC').trim()), 'PBKDF2', false, ['deriveKey']);
    const key = await crypto.subtle.deriveKey(
      { name: 'PBKDF2', salt: bytes(envelope.salt), iterations: envelope.iterations, hash: 'SHA-256' },
      material, { name: 'AES-GCM', length: 256 }, false, ['decrypt'],
    );
    let plain = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: bytes(envelope.iv), additionalData: encoder.encode(envelope.aad) }, key, bytes(envelope.data),
    );
    if (envelope.compression === 'gzip') {
      const stream = new Blob([plain]).stream().pipeThrough(new DecompressionStream('gzip'));
      plain = await new Response(stream).arrayBuffer();
    }
    return JSON.parse(new TextDecoder().decode(plain));
  }
  const store = {
    get() {
      try { return sessionStorage.getItem(STORE_KEY) || localStorage.getItem(STORE_KEY); } catch (error) { return null; }
    },
    set(value, keep) {
      try {
        sessionStorage.setItem(STORE_KEY, value);
        if (keep) localStorage.setItem(STORE_KEY, value);
        else localStorage.removeItem(STORE_KEY);
      } catch (error) { /* 保存できない環境でも表示は続ける */ }
    },
    clear() {
      try { sessionStorage.removeItem(STORE_KEY); localStorage.removeItem(STORE_KEY); } catch (error) { /* 同上 */ }
    },
  };

  function fatal(message) {
    $('fatal-text').textContent = message;
    $('fatal').hidden = false;
    $('gate').hidden = true;
    $('app').hidden = true;
  }
  function open(dataset) {
    state.days = [...dataset.days].sort((a, b) => a.date.localeCompare(b.date));
    if (!state.days.length) {
      fatal('まだ記録がありません。');
      return;
    }
    state.byDate = new Map(state.days.map((rec) => [rec.date, rec]));
    state.index = state.days.length - 1;
    const stamp = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(dataset.generated_at || '');
    $('updated').textContent = stamp ? `最終更新 ${stamp[1]}/${stamp[2]}/${stamp[3]} ${stamp[4]}:${stamp[5]}` : '';
    $('sample-banner').hidden = !dataset.sample;
    $('gate').hidden = true;
    $('app').hidden = false;
    renderAll();
    renderTable();
  }

  async function boot() {
    if (!window.crypto || !crypto.subtle || typeof DecompressionStream === 'undefined') {
      fatal('このブラウザでは表示できません。ブラウザを最新版に更新してから開き直してください。');
      return;
    }
    let envelope;
    try {
      const response = await fetch('data.enc.json', { cache: 'no-store' });
      if (!response.ok) throw new Error(String(response.status));
      envelope = await response.json();
    } catch (error) {
      fatal('データを読み込めませんでした。時間をおいて再読み込みしてください。');
      return;
    }
    const saved = store.get();
    if (saved) {
      try {
        open(await decrypt(envelope, saved));
        return;
      } catch (error) {
        store.clear();
      }
    }
    $('gate').hidden = false;
    $('pass').focus();
    $('show-pass').addEventListener('change', (event) => {
      $('pass').type = event.target.checked ? 'text' : 'password';
    });
    $('gate-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const button = $('gate-submit');
      const message = $('gate-error');
      const passphrase = $('pass').value;
      button.disabled = true;
      button.textContent = '確認中…';
      message.hidden = true;
      try {
        const dataset = await decrypt(envelope, passphrase);
        store.set(passphrase, $('remember').checked);
        $('pass').value = '';
        open(dataset);
      } catch (error) {
        message.textContent = '合言葉が違うようです。もう一度確認してください。';
        message.hidden = false;
        $('pass').select();
      } finally {
        button.disabled = false;
        button.textContent = '開く';
      }
    });
  }

  /* ---------- 操作 ---------- */
  $('prev').addEventListener('click', () => select(state.days[Math.max(state.index - 1, 0)].date));
  $('next').addEventListener('click', () => select(state.days[Math.min(state.index + 1, state.days.length - 1)].date));
  $('lock').addEventListener('click', () => {
    store.clear();
    location.reload();
  });
  document.querySelectorAll('.seg button').forEach((button) => {
    button.addEventListener('click', () => {
      state.range = Number(button.dataset.range);
      renderAll();
    });
  });
  for (const id of ['rhythm', 'trend']) {
    $(id).addEventListener('click', (event) => {
      const target = event.target.closest('[data-date]');
      if (target) select(target.dataset.date);
    });
  }
  window.addEventListener('beforeprint', () => {
    $('table-details').open = true;
  });

  const tip = $('tip');
  function placeTip(event) {
    const width = tip.offsetWidth;
    const height = tip.offsetHeight;
    let x = event.clientX + 12;
    let y = event.clientY + 16;
    if (x + width > window.innerWidth - 8) x = Math.max(window.innerWidth - width - 8, 8);
    if (y + height > window.innerHeight - 8) y = event.clientY - height - 12;
    tip.style.left = `${x}px`;
    tip.style.top = `${y}px`;
  }
  document.addEventListener('pointerover', (event) => {
    const target = event.target.closest ? event.target.closest('[data-tip]') : null;
    if (!target) {
      tip.hidden = true;
      return;
    }
    tip.textContent = target.dataset.tip;
    tip.hidden = false;
    placeTip(event);
  });
  document.addEventListener('pointermove', (event) => {
    if (!tip.hidden) placeTip(event);
  });
  document.addEventListener('scroll', () => { tip.hidden = true; }, { passive: true });

  boot();
})();
