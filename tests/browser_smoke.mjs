import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';

const [port, origin, passphrase] = process.argv.slice(2);
const targets = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json();
const socket = new WebSocket(targets.find((target) => target.type === 'page').webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, { once: true });
  socket.addEventListener('error', reject, { once: true });
});
let sequence = 0;
const pending = new Map();
socket.addEventListener('message', ({ data }) => {
  const response = JSON.parse(data);
  if (pending.has(response.id)) {
    const { resolve, reject } = pending.get(response.id);
    pending.delete(response.id);
    if (response.error) reject(response.error);
    else resolve(response.result);
  }
});
function cdp(method, params = {}) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const response = await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (response.exceptionDetails) throw new Error(response.exceptionDetails.text);
  return response.result.value;
}
async function until(expression) {
  for (let i = 0; i < 60; i++) {
    if (await evaluate(expression)) return;
    await delay(100);
  }
  throw new Error('画面条件がタイムアウトしました');
}
async function unlock(value) {
  await evaluate('document.getElementById("pass").value = ' + JSON.stringify(value) +
    '; document.getElementById("gate-form").requestSubmit(); true');
}

try {
  await cdp('Page.enable');
  await cdp('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await cdp('Page.navigate', { url: origin });
  await until('!!document.querySelector("#gate:not([hidden])")');
  await unlock('synthetic wrong input');
  await until('!!document.querySelector("#gate-error:not([hidden])")');
  await unlock(passphrase);
  await until('!!document.querySelector("#app:not([hidden])")');
  const mobile = await evaluate('({pc:document.querySelector("#pc-value").textContent,' +
    ' detail:document.querySelector("#detail").textContent,' +
    ' sample:!document.querySelector("#sample-banner").hidden,' +
    ' noindex:document.querySelector("meta[name=robots]").content.includes("noindex"),' +
    ' local:localStorage.length, session:sessionStorage.length,' +
    ' viewport:innerWidth, documentWidth:document.documentElement.scrollWidth})');
  assert.equal(mobile.pc, '一部欠測');
  assert.match(mobile.detail, /操作中か離席かの判定がないアプリ記録/);
  assert.equal(mobile.sample, true);
  assert.equal(mobile.noindex, true);
  assert.equal(mobile.local, 0);
  assert.equal(mobile.session, 0);
  assert.equal(mobile.viewport, 390);
  assert.equal(mobile.documentWidth, 390);
  const render = await evaluate(`(() => {
    const rowCount = () => document.querySelectorAll('#rhythm .row').length;
    const columns = () => [...document.querySelectorAll('#trend .cols')].map((item) => item.children.length);
    const pcChart = () => document.querySelectorAll('#trend .chart')[1];
    const start = { rows: rowCount(), columns: columns() };
    document.querySelector('[data-range="30"]').click();
    const thirty = { rows: rowCount(), columns: columns(), pressed: document.querySelector('[data-range="30"]').getAttribute('aria-pressed'),
      pcBars: pcChart().querySelectorAll('.bar-pc').length, average: pcChart().querySelector('.avg').textContent,
      missingAtEnd: Boolean(pcChart().querySelector('.col:last-child .none-mark')) };
    const originalRow = document.querySelector('#rhythm .row');
    const originalChart = document.querySelector('#trend .chart');
    for (let i = 0; i < 4; i++) { document.querySelector('#prev').click(); document.querySelector('#next').click(); }
    const measurements = [];
    for (let i = 0; i < 30; i++) {
      const begin = performance.now();
      document.querySelector('#prev').click();
      document.querySelector('#next').click();
      measurements.push(performance.now() - begin);
    }
    const retainsNodes = originalRow === document.querySelector('#rhythm .row') &&
      originalChart === document.querySelector('#trend .chart');
    document.querySelector('#rhythm .row:nth-child(3)').click();
    const selected = document.querySelector('#rhythm [aria-current="date"]')?.dataset.date;
    const selectedCharts = [...document.querySelectorAll('#trend .col.is-selected')].map((node) => node.dataset.date);
    document.querySelector('#next').click();
    document.querySelector('[data-range="14"]').click();
    return {start, thirty, retainsNodes, selected, selectedCharts, end: rowCount(),
      median: measurements.sort((a, b) => a - b)[15]};
  })()`);
  assert.deepEqual(render.start, { rows: 14, columns: [14, 14] });
  assert.equal(render.thirty.rows, 30);
  assert.deepEqual(render.thirty.columns, [30, 30]);
  assert.equal(render.thirty.pressed, 'true');
  assert.equal(render.thirty.missingAtEnd, true);
  assert.match(render.thirty.average, new RegExp('記録のある' + render.thirty.pcBars + '日'));
  assert.deepEqual(render.selectedCharts, [render.selected, render.selected]);
  assert.equal(render.end, 14);
  console.log('日付を往復したクリック処理の中央値: ' + render.median.toFixed(2) + 'ms / リズム表とグラフのDOMを保持: ' + render.retainsNodes);
  assert.equal(render.retainsNodes, true);
  await cdp('Emulation.clearDeviceMetricsOverride');
  assert.equal(await evaluate('document.querySelector("#pc-value").textContent'), '一部欠測');
  assert.equal(await evaluate('!!document.querySelector("#day-dial svg")'), true);
  console.log('Edge実行確認: モバイル390px、デスクトップ、誤った合言葉、WebCrypto復号、欠測表示、noindex、未記憶を検証');
} finally {
  socket.close();
}
