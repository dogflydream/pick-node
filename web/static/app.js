/* ============================================================
   代理节点体检面板 —— 前端逻辑
   纯原生 JS, 无构建步骤, 无外部依赖
   ============================================================ */
'use strict';

// ---------------------------------------------------------------- 状态

const state = {
  items: [],          // 当前列表
  selected: new Set(),// 勾选的 node_id
  sort: 'default',
  order: 'asc',
  facets: { countries: [], protocols: [], gemini: [] },
  meta: null,
  retest: { running: false, es: null, total: 0, done: 0 },  // 重新检测状态
};

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- 工具

/** 转义, 防止节点名里的特殊字符破坏 HTML */
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function toast(msg, kind) {
  const el = $('toast');
  el.textContent = msg;
  el.className = 'toast is-visible' + (kind ? ' toast--' + kind : '');
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    el.classList.remove('is-visible');
    setTimeout(() => { el.hidden = true; }, 200);
  }, 2200);
}

function fmtLatency(ms) {
  if (!ms || ms <= 0) return '<span class="dim">–</span>';
  const cls = ms < 1500 ? 'lat--fast' : ms < 4000 ? 'lat--mid' : 'lat--slow';
  return `<span class="mono ${cls}">${ms}</span><span class="dim mono"> ms</span>`;
}

/** 可用性 -> 徽标 (text + tone) */
function badgeFor(value) {
  const map = {
    success:     ['成功', 'ok'],
    available:   ['可用', 'ok'],
    likely:      ['可能可用', 'warn'],
    unknown:     ['未知', 'idle'],
    blocked:     ['被限制', 'bad'],
    unavailable: ['不可用', 'bad'],
    fail:        ['失败', 'bad'],
  };
  const [text, tone] = map[value] || [value || '–', 'idle'];
  return `<span class="badge badge--${tone}">${esc(text)}</span>`;
}

function geoLabel(cc) {
  if (!cc) return '<span class="dim">–</span>';
  return `<span class="geo">${esc(cc)}</span>`;
}

/** 风险等级 -> 徽标. title 里带上原因, 悬停可看细节 */
function riskBadge(row) {
  const lvl = row.risk_level || 'none';
  const reason = row.risk_reason || '';
  const flags = (row.risk_flags || []).join(',');
  if (lvl === 'high') {
    const tip = `高风险: ${reason}${flags ? ' [命中: ' + flags + ']' : ''}`;
    return `<span class="badge badge--bad" title="${esc(tip)}">代理/VPN</span>`;
  }
  if (lvl === 'low') {
    return `<span class="badge badge--warn" title="${esc(reason || '弱风险信号')}">可疑</span>`;
  }
  return '<span class="badge badge--idle">正常</span>';
}

// ---------------------------------------------------------------- 数据加载

async function api(path) {
  const r = await fetch(path, { headers: { 'Accept': 'application/json' } });
  if (!r.ok) {
    let detail = r.status + ' ' + r.statusText;
    try { const j = await r.json(); if (j.detail) detail = j.detail; } catch (_) {}
    throw new Error(detail);
  }
  return r.json();
}

function currentQuery() {
  const p = new URLSearchParams();
  const s = $('f-search').value.trim();
  if (s) p.set('search', s);
  if ($('f-country').value) p.set('country', $('f-country').value);
  if ($('f-protocol').value) p.set('protocol', $('f-protocol').value);
  if ($('f-gemini').value) p.set('gemini', $('f-gemini').value);
  if ($('f-risk').value) p.set('risk', $('f-risk').value);
  if ($('f-usable').checked) p.set('usable_only', 'true');
  p.set('sort', state.sort);
  p.set('order', state.order);
  return p.toString();
}

async function loadMeta() {
  try {
    const m = await api('/api/meta');
    state.meta = m;
    const el = $('data-meta');
    if (!m.exists) {
      el.textContent = '结果文件不存在: ' + m.file;
      showAlert('尚未生成 ' + m.file + '，请先运行：python main.py');
    } else if (m.error) {
      el.textContent = m.file + ' · ' + m.error;
      showAlert(m.error);
    } else {
      el.textContent = `${m.file} · ${m.count} 条 · 更新于 ${m.updated_at || '未知'}`;
      hideAlert();
    }
  } catch (e) {
    showAlert('无法读取数据源: ' + e.message);
  }
}

function showAlert(msg) { const b = $('alert-box'); b.textContent = msg; b.hidden = false; }
function hideAlert() { $('alert-box').hidden = true; }

async function loadSummary() {
  const s = await api('/api/summary');
  $('m-usable').textContent = s.usable;
  $('m-usable-hint').textContent = s.strict_usable === s.usable
    ? '可达 + Google 通 + Gemini 未被拒'
    : `其中严格可用 ${s.strict_usable}`;
  $('m-total').textContent = s.total;
  $('m-total-hint').textContent = `链路可达 ${s.reachable}`;
  $('m-google').textContent = s.google_ok;
  $('m-google-hint').textContent = s.total
    ? `${Math.round(s.google_ok / s.total * 100)}% 成功率` : '–';
  $('m-gemini').textContent = s.gemini_available;
  $('m-gemini-hint').textContent =
    `可能可用 ${s.gemini_likely} · 受限 ${s.gemini_blocked}`;
  $('m-ag').textContent = s.antigravity_available;
  $('m-ag-hint').textContent = `占比 ${s.total ? Math.round(s.antigravity_available / s.total * 100) : 0}%`;
  $('m-lat').textContent = s.avg_latency ? s.avg_latency : '–';
  $('m-lat-hint').textContent = s.min_latency ? `最快 ${s.min_latency} ms` : '无可用数据';

  // 地区分布 chips
  const wrap = $('geo-chips');
  const entries = Object.entries(s.countries || {});
  wrap.innerHTML = entries.length
    ? entries.map(([cc, n]) =>
        `<button type="button" class="chip" data-cc="${esc(cc)}" aria-pressed="false">
           <span class="chip__cc">${esc(cc)}</span><span class="chip__n">${n}</span>
         </button>`).join('')
    : '<span class="dim">暂无数据</span>';
  wrap.querySelectorAll('.chip').forEach(btn => {
    btn.addEventListener('click', () => {
      const cc = btn.dataset.cc;
      const sel = $('f-country');
      sel.value = (sel.value === cc) ? '' : cc;
      syncChips();
      loadNodes();
    });
  });
  syncChips();
}

function syncChips() {
  const cur = $('f-country').value;
  document.querySelectorAll('.chip').forEach(b => {
    b.setAttribute('aria-pressed', String(b.dataset.cc === cur));
  });
}

async function loadFacets() {
  const f = await api('/api/facets');
  state.facets = f;

  const fill = (sel, list, labeler, keepAll) => {
    const cur = sel.value;
    sel.innerHTML = (keepAll ? '<option value="">全部</option>' : '');
    list.forEach(item => {
      const o = document.createElement('option');
      o.value = item.value;
      o.textContent = labeler(item);
      sel.appendChild(o);
    });
    if (cur && [...sel.options].some(o => o.value === cur)) sel.value = cur;
  };

  fill($('f-country'), f.countries, i => `${i.value} (${i.count})`, true);
  fill($('f-protocol'), f.protocols, i => `${i.value} (${i.count})`, true);

  const gemLabels = {
    available: '可用', likely: '可能可用', blocked: '被限制',
    unavailable: '不可用', unknown: '未知',
  };
  fill($('f-gemini'), f.gemini,
       i => `${gemLabels[i.value] || i.value} (${i.count})`, true);
}

// ---------------------------------------------------------------- 表格渲染

function render() {
  const tb = $('tbody');
  const empty = $('empty');
  const items = state.items;

  updateCountText();

  if (!items.length) {
    tb.innerHTML = '';
    empty.hidden = false;
    $('empty-text').textContent = state.meta && !state.meta.exists
      ? '尚无检测结果，请先运行 python main.py'
      : '没有符合当前筛选条件的节点';
    updateSelectionUI();
    return;
  }
  empty.hidden = true;

  const frag = document.createDocumentFragment();
  items.forEach(r => {
    const tr = document.createElement('tr');
    const id = r.node_id || '';
    if (state.selected.has(id)) tr.classList.add('is-selected');

    const tag = r.tag || `${r.server}:${r.port}`;
    tr.innerHTML = `
      <td class="col-check">
        <input type="checkbox" data-id="${esc(id)}"
               ${state.selected.has(id) ? 'checked' : ''}
               aria-label="选择节点 ${esc(tag)}">
      </td>
      <td>
        <div class="node-cell">
          <span class="node-cell__tag" title="${esc(tag)}">${esc(tag)}</span>
          <span class="node-cell__host" title="${esc(r.server)}:${esc(r.port)}">
            ${esc(r.server)}:${esc(r.port)}</span>
        </div>
      </td>
      <td><span class="proto">${esc(r.protocol || '–')}</span></td>
      <td class="col-ip mono">${r.ip ? esc(r.ip) : '<span class="dim">–</span>'}</td>
      <td>${geoLabel(r.country)}</td>
      <td>${badgeFor(r.google)}</td>
      <td>${riskBadge(r)}</td>
      <td>${badgeFor(r.gemini)}</td>
      <td>${badgeFor(r.antigravity)}</td>
      <td class="col-num">${fmtLatency(r.latency)}</td>
    `;
    frag.appendChild(tr);
  });

  tb.innerHTML = '';
  tb.appendChild(frag);
  updateSelectionUI();
}

/** 更新"显示 N 个节点 · 已选 M"文案.
 *  已选是全局的 (跨筛选保留), 显示数是当前视图的, 两者含义不同, 分别标注. */
function updateCountText() {
  const shown = state.items.length;
  const sel = state.selected.size;
  const selPart = sel > 0
    ? ` · 已选 <strong>${sel}</strong> 个${sel > shown ? '（含非当前视图）' : ''}`
    : ' · 已选 <strong>0</strong>';
  $('result-count').innerHTML = `显示 <strong>${shown}</strong> 个节点${selPart}`;
}

function updateSelectionUI() {
  const n = state.selected.size;
  $('btn-clear').disabled = n === 0;
  $('btn-copy-selected').disabled = n === 0;

  // 全选状态: 只反映当前列表
  const all = $('check-all');
  const visible = state.items.map(r => r.node_id).filter(Boolean);
  const selectedVisible = visible.filter(id => state.selected.has(id));
  all.checked = visible.length > 0 && selectedVisible.length === visible.length;
  all.indeterminate = selectedVisible.length > 0 && selectedVisible.length < visible.length;
  // 有勾选时导出按钮语义变化
  const btn = $('btn-export');
  btn.textContent = '';
  const icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" aria-hidden="true"><path d="M12 3v12m0 0 4-4m-4 4-4-4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>';
  btn.innerHTML = n > 0 ? icon + `导出所选 (${n})` : icon + '导出潜在可用 URI';
}

async function loadNodes() {
  try {
    const data = await api('/api/nodes?' + currentQuery());
    state.items = data.items || [];
    // 注意: 这里【不能】按当前筛选结果裁剪 state.selected.
    // 勾选是"节点级"的, 与当前视图无关 —— 否则切一次筛选就会把
    // 之前勾选的节点悄悄清掉, 导致计数与实际不符 (曾出现过此 bug).
    // 只清理确实已从 data.json 消失的节点.
    render();
  } catch (e) {
    toast('加载失败: ' + e.message, 'err');
  }
}

async function refreshAll() {
  await Promise.all([loadMeta(), loadSummary(), loadFacets()]);
  await loadNodes();
}

// ---------------------------------------------------------------- 导出

function exportUrl(scope, ids) {
  const p = new URLSearchParams({ scope });
  if (ids && ids.length) p.set('ids', ids.join(','));
  return '/api/export?' + p.toString();
}

async function fetchUris(scope, ids) {
  const r = await fetch(exportUrl(scope, ids), { headers: { 'Accept': 'text/plain' } });
  if (!r.ok) {
    let d = r.status + ' ' + r.statusText;
    try { const j = await r.json(); if (j.detail) d = j.detail; } catch (_) {}
    throw new Error(d);
  }
  return await r.text();
}

async function copyToClipboard(text) {
  // 优先用 Clipboard API; 非 HTTPS/localhost 或旧浏览器回退到 execCommand
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (_) { /* 落到回退方案 */ }

  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.cssText = 'position:absolute;left:-9999px;top:0';
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
  document.body.removeChild(ta);
  return ok;
}

async function doExport() {
  const ids = [...state.selected];
  const useSel = ids.length > 0;
  try {
    const text = await fetchUris(useSel ? 'all' : 'usable', useSel ? ids : null);
    const n = text.trim() ? text.trim().split('\n').length : 0;
    if (n === 0) { toast('没有符合条件的节点', 'err'); return; }

    const ok = await copyToClipboard(text);
    // 同时触发下载, 便于直接存成文件
    const a = document.createElement('a');
    a.href = exportUrl(useSel ? 'all' : 'usable', useSel ? ids : null) + '&download=true';
    a.download = '';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);

    toast(ok ? `已复制 ${n} 条 URI 并开始下载` : `已下载 ${n} 条 URI (复制失败, 请手动)`,
          ok ? 'ok' : 'err');
  } catch (e) {
    toast('导出失败: ' + e.message, 'err');
  }
}

// ---------------------------------------------------------------- 重新检测

/** 打开确认条, 显示本次将要检测的范围 */
function openRetestConfirm() {
  if (state.retest.running) return;
  const n = state.selected.size;
  const scope = n > 0
    ? `已勾选 <strong>${n}</strong> 个节点`
    : '未勾选任何节点, 将检测 <strong>节点文件中的全部节点</strong>';
  $('confirm-text').innerHTML =
    `${scope}。检测会为每个节点启动一个独立内核进程并发起真实网络请求，` +
    `期间可以随时停止。`;

  // 并发数用后端 .env 的默认值
  if (state.meta && state.meta.concurrency) {
    $('rt-concurrency').value = state.meta.concurrency;
  }
  $('retest-confirm').hidden = false;
  $('btn-rt-start').focus();
}

function closeRetestConfirm() {
  $('retest-confirm').hidden = true;
}

async function startRetest() {
  const ids = [...state.selected];
  const body = {};
  if (ids.length) body.node_ids = ids;
  const c = parseInt($('rt-concurrency').value, 10);
  if (c > 0) body.concurrency = c;

  $('btn-rt-start').disabled = true;
  try {
    const r = await fetch('/api/retest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      let d = r.status + ' ' + r.statusText;
      try { const j = await r.json(); if (j.detail) d = j.detail; } catch (_) {}
      throw new Error(d);
    }
    closeRetestConfirm();
    enterRetestMode();
    subscribeRetest();
    toast(ids.length ? `开始检测 ${ids.length} 个节点` : '开始检测全部节点', 'ok');
  } catch (e) {
    toast('启动失败: ' + e.message, 'err');
  } finally {
    $('btn-rt-start').disabled = false;
  }
}

/** 切换到运行中状态 */
function enterRetestMode() {
  state.retest.running = true;
  state.retest.total = 0;
  state.retest.done = 0;
  $('retest-progress').hidden = false;
  $('rt-count').textContent = '0 / 0';
  $('rt-bar').style.width = '0%';
  $('rt-log').textContent = '';
  $('rt-status').textContent = '启动中…';
  $('rt-status').className = 'prog__status';
  $('btn-retest').classList.add('is-busy');
  $('btn-retest-label').textContent = '检测中…';
  $('btn-retest').disabled = true;
}

function exitRetestMode(status) {
  state.retest.running = false;
  $('btn-retest').classList.remove('is-busy');
  $('btn-retest-label').textContent = '重新检测';
  $('btn-retest').disabled = false;
  if (state.retest.es) {
    state.retest.es.close();
    state.retest.es = null;
  }
  const map = {
    done: ['已完成', 'prog__status--ok'],
    cancelled: ['已停止', 'prog__status--warn'],
    failed: ['失败', 'prog__status--bad'],
  };
  const [text, cls] = map[status] || ['已结束', ''];
  $('rt-status').textContent = text;
  $('rt-status').className = 'prog__status ' + cls;
  $('btn-rt-stop').disabled = true;
}

/** 订阅 SSE 实时进度 */
function subscribeRetest() {
  if (state.retest.es) state.retest.es.close();
  const es = new EventSource('/api/retest/events');
  state.retest.es = es;

  es.addEventListener('progress', (ev) => {
    let d;
    try { d = JSON.parse(ev.data); } catch (_) { return; }
    applyRetestProgress(d);
  });

  es.addEventListener('done', (ev) => {
    let d = {};
    try { d = JSON.parse(ev.data); } catch (_) {}
    applyRetestProgress(d);
    exitRetestMode(d.status || 'done');

    const s = d.summary || {};
    if (d.status === 'failed') {
      toast('检测失败: ' + (d.error || '未知错误'), 'err');
    } else {
      const tail = s.usable != null ? `，可用 ${s.usable} 个` : '';
      toast((d.status === 'cancelled' ? '已停止，' : '检测完成，')
            + `结果已写回${tail}`, d.status === 'cancelled' ? 'err' : 'ok');
    }
    // 结果已落盘, 刷新表格与统计
    refreshAll();
  });

  es.onerror = () => {
    // 连接断了: 若任务仍在跑, 回退到轮询
    if (state.retest.running) {
      es.close();
      state.retest.es = null;
      pollRetest();
    }
  };
}

/** SSE 不可用时的降级: 轮询状态 */
async function pollRetest() {
  while (state.retest.running) {
    await new Promise(r => setTimeout(r, 1200));
    try {
      const d = await api('/api/retest/status?since=' + state.retest.seen);
      applyRetestProgress(d);
      if (['done', 'cancelled', 'failed'].includes(d.status)) {
        exitRetestMode(d.status);
        refreshAll();
        break;
      }
    } catch (_) { /* 继续重试 */ }
  }
}

function applyRetestProgress(d) {
  if (typeof d.total === 'number') state.retest.total = d.total;
  if (typeof d.done === 'number') state.retest.done = d.done;

  const total = state.retest.total || 0;
  const done = state.retest.done || 0;
  $('rt-count').textContent = `${done} / ${total}`;
  const pct = total ? Math.round((done / total) * 100) : 0;
  $('rt-bar').style.width = pct + '%';
  $('rt-bar-outer').setAttribute('aria-valuenow', String(pct));
  if (d.elapsed != null) $('rt-elapsed').textContent = d.elapsed + 's';
  if (d.status === 'running') {
    $('rt-status').textContent = '运行中';
    $('rt-status').className = 'prog__status';
  }

  // 追加日志 (最多保留 200 行, 避免长跑后 DOM 过大)
  if (Array.isArray(d.log) && d.log.length) {
    const el = $('rt-log');
    el.textContent = d.log.join('\n');
    el.scrollTop = el.scrollHeight;
  }

  // 把新结果实时落进表格对应行
  if (Array.isArray(d.results) && d.results.length) {
    d.results.forEach(paintResult);
  }
}

/** 用单条新结果更新表格里对应的行 (不重建整表) */
function paintResult(row) {
  const tr = document.querySelector(
    `#nodes-table tbody tr input[data-id="${CSS.escape(row.node_id || '')}"]`
  );
  if (!tr) return;
  const line = tr.closest('tr');
  if (!line) return;
  const tds = line.querySelectorAll('td');
  if (tds.length < 9) return;
  line.classList.add('is-fresh');

  const r = row;
  if (r.ip) tds[3].innerHTML = `<span class="mono">${esc(r.ip)}</span>`;
  tds[4].innerHTML = geoLabel(r.country);
  tds[5].innerHTML = badgeFor(r.google);
  tds[6].innerHTML = riskBadge(r);
  tds[7].innerHTML = badgeFor(r.gemini);
  tds[8].innerHTML = badgeFor(r.antigravity);
  tds[9].innerHTML = fmtLatency(r.latency);
  // 同步内存里的这条数据
  const item = state.items.find(x => x.node_id === r.node_id);
  if (item) Object.assign(item, r);
}

async function stopRetest() {
  $('btn-rt-stop').disabled = true;
  try {
    await fetch('/api/retest/cancel', { method: 'POST' });
    toast('正在停止…', 'ok');
  } catch (e) {
    toast('停止失败: ' + e.message, 'err');
    $('btn-rt-stop').disabled = false;
  }
}

/** 页面刷新后自动接上正在跑的作业 */
async function resumeRetestIfRunning() {
  try {
    const d = await api('/api/retest/status?since=0');
    if (d.status === 'running') {
      enterRetestMode();
      applyRetestProgress(d);
      subscribeRetest();
    }
  } catch (_) { /* 忽略 */ }
}

// ---------------------------------------------------------------- 事件绑定

function bind() {
  // 搜索防抖
  let t = null;
  $('f-search').addEventListener('input', () => {
    clearTimeout(t);
    t = setTimeout(loadNodes, 220);
  });

  ['f-country', 'f-protocol', 'f-gemini', 'f-risk'].forEach(id => {
    $(id).addEventListener('change', () => { syncChips(); loadNodes(); });
  });
  $('f-usable').addEventListener('change', loadNodes);
  $('f-sort').addEventListener('change', () => {
    state.sort = $('f-sort').value;
    state.order = 'asc';
    document.querySelectorAll('th.sortable').forEach(th => th.setAttribute('aria-sort', 'none'));
    loadNodes();
  });

  // 列头排序
  document.querySelectorAll('th.sortable').forEach(th => {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      if (state.sort === key) {
        state.order = state.order === 'asc' ? 'desc' : 'asc';
      } else {
        state.sort = key;
        state.order = 'asc';
      }
      $('f-sort').value = key;
      document.querySelectorAll('th.sortable').forEach(o =>
        o.setAttribute('aria-sort', o === th
          ? (state.order === 'asc' ? 'ascending' : 'descending') : 'none'));
      loadNodes();
    });
  });

  // 全选 / 单选
  $('check-all').addEventListener('change', (e) => {
    const on = e.target.checked;
    state.items.forEach(r => {
      if (!r.node_id) return;
      if (on) state.selected.add(r.node_id); else state.selected.delete(r.node_id);
    });
    render();
  });

  $('tbody').addEventListener('change', (e) => {
    const cb = e.target.closest('input[type="checkbox"][data-id]');
    if (!cb) return;
    const id = cb.dataset.id;
    if (cb.checked) state.selected.add(id); else state.selected.delete(id);
    cb.closest('tr').classList.toggle('is-selected', cb.checked);
    updateSelectionUI();
    updateCountText();   // 计数文案要跟着变, 否则要等下次 render 才更新
  });

  $('btn-clear').addEventListener('click', () => {
    state.selected.clear();
    render();
  });

  $('btn-copy-selected').addEventListener('click', async () => {
    const ids = [...state.selected];
    if (!ids.length) return;
    try {
      const text = await fetchUris('all', ids);
      const n = text.trim() ? text.trim().split('\n').length : 0;
      const ok = await copyToClipboard(text);
      toast(ok ? `已复制 ${n} 条 URI` : '复制失败, 请用导出按钮下载', ok ? 'ok' : 'err');
    } catch (e) { toast('复制失败: ' + e.message, 'err'); }
  });

  $('btn-export').addEventListener('click', doExport);

  // ---- 重新检测
  $('btn-retest').addEventListener('click', openRetestConfirm);
  $('btn-rt-cancel-confirm').addEventListener('click', closeRetestConfirm);
  $('btn-rt-start').addEventListener('click', startRetest);
  $('btn-rt-stop').addEventListener('click', stopRetest);
  // Esc 关闭确认条
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('retest-confirm').hidden) closeRetestConfirm();
  });

  $('btn-reload').addEventListener('click', async () => {
    try {
      await fetch('/api/reload', { method: 'POST' });
      await refreshAll();
      toast('已重新载入结果', 'ok');
    } catch (e) { toast('重载失败: ' + e.message, 'err'); }
  });

  // 快捷键: / 聚焦搜索, Esc 清除搜索
  document.addEventListener('keydown', (e) => {
    if (e.key === '/' && document.activeElement !== $('f-search')) {
      e.preventDefault(); $('f-search').focus();
    } else if (e.key === 'Escape' && document.activeElement === $('f-search')) {
      $('f-search').value = ''; loadNodes(); $('f-search').blur();
    }
  });
}

// ---------------------------------------------------------------- 启动

(async function init() {
  bind();
  await refreshAll();
  await resumeRetestIfRunning();
})();
