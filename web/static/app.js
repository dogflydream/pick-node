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
  empty: true,        // 是否"尚无任何检测结果" (用于区分首次使用与筛选无匹配)
  retest: { running: false, es: null, total: 0, done: 0 },  // 重新检测状态
  // ---- 分类 ----
  cats: [],           // 后端返回的分类列表 [{key,label,count,exists}]
  cat: '',            // 当前选中的分类 key
  catServices: [],    // 当前分类的服务 key 列表 (表格列)
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

/** 三个延迟口径的阈值不同, 分开着色:
 *    RTT  (链路往返)   : 通常几百 ms
 *    TTFB (首字节)     : 通常 1-3s
 *    latency (端到端)  : 通常 2-6s (含下载首页)
 */
function fmtRtt(ms) {
  if (!ms || ms <= 0) return '<span class="dim">–</span>';
  const cls = ms < 400 ? 'lat--fast' : ms < 1000 ? 'lat--mid' : 'lat--slow';
  return `<span class="mono ${cls}">${ms}</span><span class="dim mono"> ms</span>`;
}

function fmtTtfb(ms) {
  if (!ms || ms <= 0) return '<span class="dim">–</span>';
  const cls = ms < 1000 ? 'lat--fast' : ms < 2500 ? 'lat--mid' : 'lat--slow';
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

// ---------------------------------------------------------------- 分类

/** 加载分类列表并渲染页签 */
async function loadCategories() {
  try {
    const d = await api('/api/categories');
    state.cats = d.categories || [];
  } catch (_) {
    state.cats = [];
  }

  const nav = $('cats');
  const list = $('cats-list');
  if (!state.cats.length) {
    nav.hidden = true;
    return;
  }
  nav.hidden = false;

  // 保持当前选择; 若未选择则用第一个有数据的分类
  const withData = state.cats.filter(c => c.count > 0);
  if (!state.cat || !state.cats.some(c => c.key === state.cat)) {
    state.cat = (withData[0] || state.cats[0]).key;
  }

  list.innerHTML = state.cats.map(c => `
    <button type="button" class="cat${c.count ? '' : ' cat--empty'}"
            role="tab" data-cat="${esc(c.key)}"
            aria-selected="${c.key === state.cat}">
      <span class="cat__name">${esc(c.label)}</span>
      <span class="cat__n">${c.count}</span>
    </button>`).join('');

  list.querySelectorAll('.cat').forEach(btn => {
    btn.addEventListener('click', () => selectCategory(btn.dataset.cat));
  });

  const cur = state.cats.find(c => c.key === state.cat);
  $('cats-hint').textContent = cur && cur.count
    ? `${cur.file} · ${cur.count} 条`
    : (cur ? `还没有 ${cur.label} 的检测结果` : '');

  updateTableHead();
}

/** 表头随分类变化.
 *
 *  分类模式:  结论 + 明细            (2 列)
 *  非分类模式: Google + 风险 + Gemini + Antigravity  (4 列)
 *
 *  用 JS 重建 thead 的服务列, 避免"隐藏列"导致表头与单元格错位。
 */
function updateTableHead() {
  const tr = document.querySelector('#nodes-table thead tr');
  if (!tr) return;

  // 移除所有动态列 (标记 data-dyn)
  tr.querySelectorAll('th[data-dyn]').forEach(th => th.remove());

  // 找到"地区"列, 在它之后插入
  const anchor = [...tr.querySelectorAll('th')].find(
    th => (th.textContent || '').trim() === '地区');
  const insertAfter = el => anchor ? anchor.after(el) : tr.appendChild(el);

  if (state.cat) {
    const mk = (text, cls) => {
      const th = document.createElement('th');
      th.scope = 'col';
      th.textContent = text;
      th.dataset.dyn = '1';
      if (cls) th.className = cls;
      return th;
    };
    insertAfter(mk('结论', 'col-verdict'));
    insertAfter(mk('明细', 'col-svc'));
  } else {
    ['Google', '风险', 'Gemini', 'Antigravity'].forEach(t => {
      const th = document.createElement('th');
      th.scope = 'col';
      th.textContent = t;
      th.dataset.dyn = '1';
      insertAfter(th);
    });
  }
}

/** 切换分类: 重置选择 → 重新拉取该分类的数据 */
async function selectCategory(key) {
  if (key === state.cat) return;
  state.cat = key;
  state.selected.clear();
  // 选中态即时反馈
  document.querySelectorAll('.cat').forEach(b => {
    b.setAttribute('aria-selected', String(b.dataset.cat === key));
  });
  const cur = state.cats.find(c => c.key === state.cat);
  $('cats-hint').textContent = cur && cur.count
    ? `${cur.file} · ${cur.count} 条` : `还没有 ${cur ? cur.label : ''} 的检测结果`;

  await refreshAll();
}

/** 取当前分类下某条结果的服务明细 */
function svcOf(row) {
  const c = (row.categories || {})[state.cat];
  return (c && c.services) || {};
}

/** 当前分类的结论 */
function verdictOf(row) {
  const c = (row.categories || {})[state.cat];
  return c ? String(c.verdict || '') : '';
}

function catLabel(key) {
  const c = state.cats.find(x => x.key === key);
  return c ? c.label : key.toUpperCase();
}

// ---------------------------------------------------------------- 分类单元

/** 分类结论徽标 */
function verdictCell(row) {
  const v = verdictOf(row);
  const c = (row.categories || {})[state.cat] || {};
  const reason = String(c.reason || '');
  if (!v) {
    return '<td class="col-verdict"><span class="verdict verdict--unavailable"'
         + ' title="该节点没有此分类的检测结果">未测</span></td>';
  }
  const text = { available: '可用', blocked: '受限', unavailable: '不可用' }[v] || v;
  const cls = { available: 'available', blocked: 'blocked',
                unavailable: 'unavailable' }[v] || 'unavailable';
  return `<td class="col-verdict"><span class="verdict verdict--${cls}"`
       + ` title="${esc(reason)}">${esc(text)}</span></td>`;
}

/** 各服务的结论小标签 */
function servicesCell(row) {
  const svcs = svcOf(row);
  const c = (row.categories || {})[state.cat] || {};
  const reasons = c.reasons || {};
  const keys = Object.keys(svcs);
  if (!keys.length) return '<td class="col-svc"><span class="dim">–</span></td>';
  const html = keys.map(k => {
    const v = String(svcs[k]);
    const cls = v === 'available' ? 'ok'
              : v === 'blocked' ? 'bad'
              : v === 'likely' ? 'warn' : 'idle';
    const label = { available: '可用', blocked: '受限',
                    unavailable: '不可用', likely: '可能可用' }[v] || v;
    const tip = reasons[k] || '';
    return `<span class="svc svc--${cls}" title="${esc(k + ': ' + tip)}">`
         + `${esc(k)} ${esc(label)}</span>`;
  }).join('');
  return `<td class="col-svc"><div class="svc-row">${html}</div></td>`;
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
  // 分类模式下用分类结论筛选; 否则用旧的 Google 维度
  if (state.cat) {
    p.set('category', state.cat);
    const svc = $('f-svc');
    if (svc && svc.value) p.set('cat_verdict', svc.value);
  } else {
    if ($('f-gemini').value) p.set('gemini', $('f-gemini').value);
    if ($('f-ag').value) p.set('antigravity', $('f-ag').value);
    if ($('f-risk').value) p.set('risk', $('f-risk').value);
  }
  if ($('f-usable').checked) p.set('usable_only', 'true');
  p.set('sort', state.sort);
  p.set('order', state.order);
  return p.toString();
}

/** 带分类参数的 URL */
function withCat(path) {
  if (!state.cat) return path;
  return path + (path.includes('?') ? '&' : '?') + 'category=' + encodeURIComponent(state.cat);
}

async function loadMeta() {
  try {
    const m = await api(withCat('/api/meta'));
    state.meta = m;
    const el = $('data-meta');
    state.empty = !m.exists || m.count === 0;

    if (m.error && m.exists) {
      el.textContent = m.file + ' · ' + m.error;
      showAlert(m.error);
    } else if (state.empty) {
      // 全新启动: 还没有任何检测结果.
      // 这里【不】让用户去跑命令行 —— 面板自己就能导入并检测.
      el.textContent = `尚无检测结果 · 节点 ${m.node_count ?? 0} 个`;
      showOnboarding(m);
    } else {
      el.textContent = `${m.file} · ${m.count} 条 · 更新于 ${m.updated_at || '未知'}`;
      hideAlert();
      hideOnboarding();
    }
  } catch (e) {
    showAlert('无法读取数据源: ' + e.message);
  }
}

/** 首次使用引导: 告诉用户下一步该做什么, 并直接给按钮 */
function showOnboarding(m) {
  const box = $('onboarding');
  if (!box) return;
  const pending = m.node_count ?? 0;
  $('ob-text').innerHTML = pending > 0
    ? `已导入 <strong>${pending}</strong> 个节点，但还没有检测结果。点下面的按钮开始检测。`
    : '还没有节点，也没有检测结果。<strong>把代理链接粘贴进来就能开始</strong>，不需要命令行。';
  $('ob-import').textContent = pending > 0 ? '再导入一些节点' : '导入节点';
  $('ob-test').hidden = pending === 0;
  $('ob-test').textContent = `检测这 ${pending} 个节点`;
  box.hidden = false;
}

function hideOnboarding() {
  const box = $('onboarding');
  if (box) box.hidden = true;
}

function showAlert(msg) { const b = $('alert-box'); b.textContent = msg; b.hidden = false; }
function hideAlert() { $('alert-box').hidden = true; }

async function loadSummary() {
  const s = await api(withCat('/api/summary'));
  $('m-usable').textContent = s.usable;
  $('m-total').textContent = s.total;
  $('m-total-hint').textContent = `链路可达 ${s.reachable}`;

  // 分类模式: 指标卡展示该分类的结论分布; 非分类模式: 保留 Google 维度
  const cs = (s.categories || {})[state.cat];
  if (state.cat && cs) {
    $('m-usable-hint').textContent = `${catLabel(state.cat)} 结论为可用的节点`;
    $('m-google').textContent = cs.available || 0;
    $('m-google-hint').textContent = '该分类判定可用';
    $('m-gemini').textContent = cs.blocked || 0;
    $('m-gemini-hint').textContent = '被拒绝 (含 CF 封禁)';
    $('m-ag').textContent = cs.unavailable || 0;
    $('m-ag-hint').textContent = '连接失败等';
    // 服务级明细写进卡片标题, 让用户一眼看到测了哪些服务
    const svcNames = Object.keys(cs.services || {});
    if (svcNames.length) {
      $('m-usable-hint').textContent =
        `${catLabel(state.cat)} · 服务 ${svcNames.join(', ')}`;
    }
  } else {
    $('m-usable-hint').textContent = s.strict_usable === s.usable
      ? '可达 + Google 通 + Gemini 未被拒'
      : `其中严格可用 ${s.strict_usable}`;
    $('m-google').textContent = s.google_ok;
    $('m-google-hint').textContent = s.total
      ? `${Math.round(s.google_ok / s.total * 100)}% 成功率` : '–';
    $('m-gemini').textContent = s.gemini_available;
    $('m-gemini-hint').textContent =
      `可能可用 ${s.gemini_likely} · 受限 ${s.gemini_blocked}`;
    $('m-ag').textContent = s.antigravity_available;
    $('m-ag-hint').textContent = '跟随 Gemini';
  }

  $('m-rtt').textContent = s.avg_rtt ? s.avg_rtt : '–';
  $('m-rtt-hint').textContent = s.min_rtt ? `最快 ${s.min_rtt} ms` : '无可用数据';
  $('m-ttfb').textContent = s.avg_ttfb ? s.avg_ttfb : '–';
  $('m-ttfb-hint').textContent = s.min_ttfb ? `最快 ${s.min_ttfb} ms` : '不含下载页面';

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
  const f = await api(withCat('/api/facets'));
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
  fill($('f-ag'), f.antigravity || [],
       i => `${gemLabels[i.value] || i.value} (${i.count})`, true);

  // ---- 分类模式: 用「服务结论」筛选取代 Google 维度筛选 ----
  const fieldSvc = $('field-svc');
  const svcSel = $('f-svc');
  if (state.cat) {
    fieldSvc.hidden = false;
    // 统计当前分类下各结论的数量 (available / blocked / unavailable)
    const counts = { available: 0, blocked: 0, unavailable: 0 };
    (state.items || []).forEach(r => {
      const v = verdictOf(r);
      if (v in counts) counts[v] += 1;
    });
    // 数量以该分类全部结果为准 (不受当前筛选影响), 用 summary 更稳
    svcSel.innerHTML = '<option value="">全部</option>' + [
      ['available', '可用'],
      ['blocked', '受限/封禁'],
      ['unavailable', '不可用'],
    ].map(([v, label]) => `<option value="${v}">${label}</option>`).join('');
    $('f-svc-label').textContent = `${catLabel(state.cat)} 结论`;
  } else {
    fieldSvc.hidden = true;
  }
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
    // 区分两种"空": 真的还没测过 (引导用户去导入), 还是筛选筛没了
    const noResults = state.empty;
    $('empty-text').textContent = noResults
      ? '还没有检测结果 —— 点上方「导入节点」粘贴代理链接即可开始'
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
      ${state.cat ? verdictCell(r) + servicesCell(r)
                  : badgeFor(r.google) + riskBadge(r)
                    + badgeFor(r.gemini) + badgeFor(r.antigravity)}
      <td class="col-num">${fmtRtt(r.rtt_min_ms)}</td>
      <td class="col-num">${fmtTtfb(r.ttfb_ms)}</td>
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

// ---------------------------------------------------------------- 导入节点

/** 打开导入面板 */
function openImport() {
  $('import-panel').hidden = false;
  $('import-text').focus();
  $('import-panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function closeImport() {
  $('import-panel').hidden = true;
  resetImportPreview();
}

function resetImportPreview() {
  $('import-stats').hidden = true;
  $('import-details').hidden = true;
  $('import-detail-body').textContent = '';
  $('btn-import-submit').disabled = true;
  $('btn-import-submit').textContent = '导入';
  $('import-hint').textContent = '粘贴后会自动检查，确认无误再点「导入」。';
  $('import-hint').className = 'imp__hint';
}

/** 自动检查: 调后端预览, 告诉用户会新增多少 / 跳过多少 */
async function previewImport() {
  const text = $('import-text').value;
  if (!text.trim()) { resetImportPreview(); return; }

  try {
    const r = await fetch('/api/nodes/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!r.ok) throw new Error(r.status + ' ' + r.statusText);
    const d = await r.json();
    renderImportPreview(d);
  } catch (e) {
    $('import-hint').textContent = '检查失败：' + e.message;
    $('import-hint').className = 'imp__hint imp__hint--bad';
    $('btn-import-submit').disabled = true;
  }
}

function renderImportPreview(d) {
  $('import-stats').hidden = false;
  $('imp-new').textContent = d.new_count || 0;
  $('imp-dup').textContent = d.dup_count || 0;
  $('imp-bad').textContent = d.bad_count || 0;

  // 明细: 让想深究的人能看到具体是哪些行
  const parts = [];
  (d.new_samples || []).forEach(t => parts.push(`<span class="ok">[新增] ${esc(t)}</span>`));
  (d.dup_samples || []).forEach(t => parts.push(`[跳过] 重复: ${esc(t)}`));
  (d.bad_samples || []).forEach(x =>
    parts.push(`<span class="bad">[无效] ${esc(x.error)} — ${esc(x.line)}</span>`));
  const body = $('import-detail-body');
  body.innerHTML = parts.length ? parts.join('\n') : '（没有明细）';
  $('import-details').hidden = parts.length === 0;

  const n = d.new_count || 0;
  const btn = $('btn-import-submit');
  if (n > 0) {
    btn.disabled = false;
    btn.textContent = `导入 ${n} 个节点`;
    $('import-hint').textContent =
      `准备导入 ${n} 个新节点` +
      (d.dup_count ? `，${d.dup_count} 个已存在会自动跳过` : '') +
      (d.bad_count ? `，${d.bad_count} 行无法识别` : '') + '。';
    $('import-hint').className = 'imp__hint imp__hint--ok';
  } else {
    btn.disabled = true;
    btn.textContent = '导入';
    if (d.dup_count && !d.bad_count) {
      $('import-hint').textContent =
        `这 ${d.dup_count} 个节点都已经在列表里了，不需要重复导入。`;
      $('import-hint').className = 'imp__hint';
    } else if (d.bad_count) {
      $('import-hint').textContent =
        `没找到可识别的代理链接（${d.bad_count} 行无法解析）。请确认每行是一个完整链接。`;
      $('import-hint').className = 'imp__hint imp__hint--bad';
    } else {
      $('import-hint').textContent = '还没有内容，请粘贴代理链接。';
      $('import-hint').className = 'imp__hint';
    }
  }
}

async function submitImport() {
  const text = $('import-text').value;
  if (!text.trim()) return;
  const btn = $('btn-import-submit');
  btn.disabled = true;
  btn.textContent = '导入中…';
  try {
    const r = await fetch('/api/nodes/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!r.ok) {
      let msg = r.status + ' ' + r.statusText;
      try { const j = await r.json(); if (j.detail) msg = j.detail; } catch (_) {}
      throw new Error(msg);
    }
    const d = await r.json();
    const parts = [`已导入 ${d.added} 个节点`];
    if (d.dup_count) parts.push(`跳过 ${d.dup_count} 个重复的`);
    if (d.bad_count) parts.push(`${d.bad_count} 行无法识别`);
    toast(parts.join('，'), d.added > 0 ? 'ok' : 'err');

    // 清空并刷新统计; 导入的节点会自动参与下一次检测
    $('import-text').value = '';
    resetImportPreview();
    await refreshAll();

    if (d.added > 0) {
      $('import-hint').textContent =
        `已导入 ${d.added} 个节点（当前共 ${d.total_after} 个）。` +
        '点「重新检测」即可测试它们。';
      $('import-hint').className = 'imp__hint imp__hint--ok';
    }
  } catch (e) {
    toast('导入失败：' + e.message, 'err');
    btn.disabled = false;
    btn.textContent = '导入';
  }
}

/** 拖拽 .txt 文件进来 */
function bindImportDrop() {
  const drop = $('import-drop');
  const mask = $('import-dropmask');
  let depth = 0;

  ['dragenter', 'dragover'].forEach(ev =>
    drop.addEventListener(ev, (e) => {
      e.preventDefault();
      if (ev === 'dragenter') depth++;
      mask.hidden = false;
    }));

  ['dragleave', 'drop'].forEach(ev =>
    drop.addEventListener(ev, (e) => {
      e.preventDefault();
      if (ev === 'dragleave') { depth--; if (depth > 0) return; }
      depth = 0;
      mask.hidden = true;
    }));

  drop.addEventListener('drop', async (e) => {
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (!f) return;
    try {
      const text = await f.text();
      const cur = $('import-text').value.trim();
      $('import-text').value = cur ? cur + '\n' + text : text;
      toast(`已读取文件 ${f.name}`, 'ok');
      previewImport();
    } catch (err) {
      toast('读取文件失败：' + err.message, 'err');
    }
  });
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
  // 当前选中的分类 —— 后端只跑该分类的体检
  if (state.cat) body.categories = [state.cat];
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
  // 列数: 0=勾选 1=节点 2=协议 3=IP 4=地区 5=Google 6=风险
  //       7=Gemini 8=Antigravity 9=RTT 10=首字节 11=总耗时
  if (tds.length < 12) return;
  line.classList.add('is-fresh');

  const r = row;
  if (r.ip) tds[3].innerHTML = `<span class="mono">${esc(r.ip)}</span>`;
  tds[4].innerHTML = geoLabel(r.country);
  tds[5].innerHTML = badgeFor(r.google);
  tds[6].innerHTML = riskBadge(r);
  tds[7].innerHTML = badgeFor(r.gemini);
  tds[8].innerHTML = badgeFor(r.antigravity);
  tds[9].innerHTML = fmtRtt(r.rtt_min_ms);
  tds[10].innerHTML = fmtTtfb(r.ttfb_ms);
  tds[11].innerHTML = fmtLatency(r.latency);
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

  ['f-country', 'f-protocol', 'f-gemini', 'f-ag', 'f-risk', 'f-svc'].forEach(id => {
    const el = $(id);
    if (el) el.addEventListener('change', () => { syncChips(); loadNodes(); });
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

  // ---- 导入节点
  $('btn-import').addEventListener('click', openImport);
  $('btn-import-close').addEventListener('click', closeImport);

  // ---- 首次引导里的按钮
  const obImport = $('ob-import');
  if (obImport) obImport.addEventListener('click', openImport);
  const obTest = $('ob-test');
  if (obTest) {
    obTest.addEventListener('click', () => {
      // 有节点但没结果: 直接开测, 跳过勾选确认
      if (state.retest.running) return;
      startRetest();
    });
  }
  $('btn-import-clear').addEventListener('click', () => {
    $('import-text').value = '';
    resetImportPreview();
    $('import-text').focus();
  });
  $('btn-import-submit').addEventListener('click', submitImport);
  bindImportDrop();
  // 输入防抖自动检查 (250ms)
  let impTimer = null;
  $('import-text').addEventListener('input', () => {
    clearTimeout(impTimer);
    impTimer = setTimeout(previewImport, 250);
  });
  // Ctrl/Cmd + Enter 直接导入
  $('import-text').addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      if (!$('btn-import-submit').disabled) submitImport();
    }
  });

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
  await loadCategories();      // 先确定分类, 后续请求都带上它
  await refreshAll();
  await resumeRetestIfRunning();
})();
