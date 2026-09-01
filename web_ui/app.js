const state = {
  settings: {}, patterns: [], allowed: [], blocks: [], characters: new Set(), stagedCharacters: new Set(),
  containmentRules: [], tldCatalog: [], domainSuffixGroups: {}, suffixGroup: 'common',
  domainSuffixes: new Set(['.com']), stagedSuffixes: new Set(['.com']),
  binding: 'independent', sourceMode: 'generator', importedDomains: [], importedFile: '',
  quickTemplates: [], sites: [], results: [], history: [], visibleHistory: [], selectedHistory: new Set(),
  ready: false, running: false, paused: false, previewTimer: null, saveTimer: null, historyRefreshTimer: null,
  contextDomain: '', templateRandomPosition: false,
};

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>'"]/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[char]));

function toast(message, type = '') {
  const element = $('toast');
  element.textContent = message;
  element.className = `toast ${type} show`;
  clearTimeout(element._timer);
  element._timer = setTimeout(() => { element.className = 'toast'; }, 4200);
}

function setPage(name) {
  document.querySelectorAll('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.page === name));
  document.querySelectorAll('.page').forEach(item => item.classList.toggle('active', item.id === `page-${name}`));
  const meta = {
    rules: ['生成规则', '用积木自由组合域名结构'],
    run: ['运行设置', '连接 Chrome、选择网站并设置查询方式'],
    results: ['可注册结果', '仅显示名称与目标完全一致且可注册的域名'],
    history: ['已查询记录', '查看、导出或删除历史记录，删除后可以重新查询'],
  }[name];
  $('pageTitle').textContent = meta[0];
  $('pageSubtitle').textContent = meta[1];
  if (name === 'history') refreshHistory();
}

function setStatus(message, kind = 'info') {
  const pill = $('readyPill');
  pill.textContent = message;
  pill.className = `pill ${kind}`;
}

function updateControls() {
  $('topStart').disabled = !(state.ready && !state.running);
  $('topPause').disabled = !state.running || state.paused;
  $('topResume').disabled = !state.running || !state.paused;
  $('topStop').disabled = !state.running;
  $('connectChrome').disabled = state.running;
  $('reconnectPage').disabled = state.running;
  $('deleteSelectedHistory').disabled = state.running || state.selectedHistory.size === 0;
  $('clearHistory').disabled = state.running || state.history.length === 0;
}

function showModal(id) { $(id).hidden = false; }
function hideModal(id) { $(id).hidden = true; }

function renderCharacters() {
  const pool = $('characterPool');
  pool.innerHTML = '';
  state.allowed.forEach(char => {
    const button = document.createElement('button');
    button.className = `char-chip${state.stagedCharacters.has(char) ? ' selected' : ''}`;
    button.textContent = char;
    button.dataset.char = char;
    pool.appendChild(button);
  });
}

function renderCharacterSummary() {
  const selected = [...state.characters];
  $('characterSummary').textContent = selected.length
    ? `已选择 ${selected.length} 个字符：${selected.join(' ')}`
    : '尚未选择字符';
}

function openCharacterPool() {
  state.stagedCharacters = new Set(state.characters);
  renderCharacters();
  showModal('characterModal');
}

function renderSuffixSummary() {
  const selected = [...state.domainSuffixes];
  const preview = selected.slice(0, 5).join('、');
  $('suffixSummary').textContent = selected.length
    ? `已选择 ${selected.length} 个：${preview}${selected.length > 5 ? '…' : ''}`
    : '尚未选择后缀';
}

function visibleSuffixEntries() {
  const source = state.domainSuffixGroups[state.suffixGroup]
    || state.tldCatalog.map(value => ({ value, label: value }));
  const query = $('suffixSearch').value.trim().toLowerCase();
  return source.filter(entry => (
    !query
    || String(entry.value).toLowerCase().includes(query)
    || String(entry.label).toLowerCase().includes(query)
  ));
}

function renderSuffixPool() {
  const visible = visibleSuffixEntries();
  const groupLabels = {
    common: '常用后缀', public: '其他公开后缀', country: '国家 / 地区后缀',
    idn: '国际化后缀', all: '全部 IANA',
  };
  $('suffixPoolCount').textContent = `${groupLabels[state.suffixGroup] || '后缀'} · 已选择 ${state.stagedSuffixes.size} 个 · 当前显示 ${visible.length} 个`;
  const pool = $('suffixPool');
  pool.classList.toggle('idn-layout', state.suffixGroup === 'idn');
  pool.innerHTML = visible.map(entry => `<button class="suffix-chip${state.stagedSuffixes.has(entry.value) ? ' selected' : ''}" data-suffix="${esc(entry.value)}" title="${esc(entry.value)}"><span dir="auto">${esc(entry.label)}</span></button>`).join('');
  document.querySelectorAll('[data-suffix-group]').forEach(button => {
    button.classList.toggle('active', button.dataset.suffixGroup === state.suffixGroup);
  });
}

function openSuffixPool() {
  state.stagedSuffixes = new Set(state.domainSuffixes);
  state.suffixGroup = 'common';
  $('suffixSearch').value = '';
  renderSuffixPool();
  showModal('suffixModal');
}

function blockLabel(kind) {
  return { fixed: '固定文字', common: '常用规律', custom: '自定义规律', unlimited: '不限随机' }[kind] || kind;
}

function blockColor(kind) {
  return ['common', 'custom', 'fixed', 'unlimited'].includes(kind) ? kind : 'common';
}

function renderBlocks() {
  const wrap = $('blockComposer');
  if (!state.blocks.length) {
    wrap.innerHTML = '<div class="empty-composer">还没有组合块，请从上方添加一个。</div>';
    schedulePreview();
    return;
  }
  wrap.innerHTML = '';
  state.blocks.forEach((block, index) => {
    if (index) {
      const plus = document.createElement('div');
      plus.className = 'block-plus';
      plus.textContent = '+';
      wrap.appendChild(plus);
    }
    const card = document.createElement('div');
    card.className = `block-card ${blockColor(block.kind)}`;
    let valueControl = '';
    if (block.kind === 'common') {
      valueControl = `<select class="block-value" data-index="${index}" data-field="value">${state.patterns.map(pattern => `<option value="${pattern}"${pattern === block.value ? ' selected' : ''}>${pattern}</option>`).join('')}</select>`;
    } else if (block.kind === 'custom') {
      valueControl = `<input class="block-value" data-index="${index}" data-field="value" value="${esc(block.value || 'ABCDDDD')}" placeholder="例如 ABCDDDD">`;
    } else if (block.kind === 'fixed') {
      valueControl = `<input class="block-value" data-index="${index}" data-field="value" value="${esc(block.value || '')}" placeholder="固定文字">`;
    } else {
      valueControl = `<input class="block-value" data-index="${index}" data-field="length" type="number" min="1" max="63" value="${Number(block.length) || 4}">`;
    }
    const hint = {
      fixed: block.random_position ? '固定文字随机插入前、中或后部' : '内容原样写入域名',
      common: block.random_position ? '常用规律随机插入前、中或后部' : '从常用规律中选择',
      custom: block.random_position ? '自定义规律随机插入前、中或后部' : '自由输入占位规律',
      unlimited: '选择“不限”随机部分位数',
    }[block.kind];
    const randomButton = block.kind === 'unlimited'
      ? '<button class="random-insert-button placeholder" disabled>随机插入</button>'
      : `<button class="random-insert-button${block.random_position ? ' active' : ''}" data-action="random" data-index="${index}" title="让整个组合块随机插入域名的前、中或后部">随机插入</button>`;
    card.innerHTML = `<div class="block-card-head"><span>⠿ ${blockLabel(block.kind)}</span><span class="block-actions"><button class="icon-btn" data-action="save-template" data-index="${index}" title="保存为快捷模板">☆</button><button class="icon-btn" data-action="duplicate" data-index="${index}" title="复制">▣</button><button class="icon-btn" data-action="delete" data-index="${index}" title="删除">⌫</button></span></div><div class="block-card-body"><select data-index="${index}" data-field="kind"><option value="fixed"${block.kind === 'fixed' ? ' selected' : ''}>固定文字</option><option value="common"${block.kind === 'common' ? ' selected' : ''}>常用规律</option><option value="custom"${block.kind === 'custom' ? ' selected' : ''}>自定义规律</option><option value="unlimited"${block.kind === 'unlimited' ? ' selected' : ''}>不限随机</option></select>${valueControl}<div class="block-hint">${hint}</div><div class="move-row"><button data-action="left" data-index="${index}"${index === 0 ? ' disabled' : ''}>← 左移</button>${randomButton}<button data-action="right" data-index="${index}"${index === state.blocks.length - 1 ? ' disabled' : ''}>右移 →</button></div></div>`;
    wrap.appendChild(card);
  });
  schedulePreview();
}

function addBlock(kind, value, template = {}) {
  const defaults = {
    fixed: { kind: 'fixed', value: value ?? 'abc', length: 1, random_position: false },
    common: { kind: 'common', value: value ?? 'AAA', length: 1, random_position: false },
    custom: { kind: 'custom', value: value ?? 'ABCDDDD', length: 1, random_position: false },
    unlimited: { kind: 'unlimited', value: '', length: 4, random_position: false },
  };
  state.blocks.push({ ...defaults[kind], ...template, kind });
  renderBlocks();
  scheduleSave();
}

function switchBlockKind(block, kind) {
  const old = block.kind;
  block.kind = kind;
  block.random_position = kind === 'unlimited' ? false : !!block.random_position;
  if (kind === 'common' && !state.patterns.includes(String(block.value || '').toUpperCase())) block.value = 'AAA';
  if (kind === 'custom' && old === 'common') block.value = String(block.value || 'AAA').toUpperCase();
  if (kind === 'fixed' && old !== 'fixed') block.value = '';
  if (kind === 'unlimited') block.length = Number(block.length) || 4;
}

function renderContainmentRules() {
  const wrap = $('containmentList');
  if (!state.containmentRules.length) {
    wrap.innerHTML = '<span class="hint">当前没有“至少包含”限制</span>';
    return;
  }
  wrap.innerHTML = state.containmentRules.map((rule, index) => `<span class="containment-chip">${esc(rule.value)} 至少 ${Number(rule.minimum)} 次<button data-remove-containment="${index}" title="删除">×</button></span>`).join('');
}

function addContainmentRule() {
  const value = $('containmentValue').value.trim().toLowerCase();
  const minimum = Number($('containmentMinimum').value) || 1;
  if (!/^[a-z0-9-]+$/.test(value)) {
    toast('请输入英文字母、数字或半角连字符 -', 'error');
    return;
  }
  state.containmentRules.push({ value, minimum });
  $('containmentValue').value = '';
  renderContainmentRules();
  schedulePreview();
  scheduleSave();
}

function rulePayload() {
  return {
    characters: [...state.characters],
    blocks: state.blocks.map(block => ({ ...block, length: Number(block.length) || 1, random_position: !!block.random_position })),
    containment_rules: state.containmentRules.map(rule => ({ value: rule.value, minimum: Number(rule.minimum) || 1 })),
    domain_suffixes: [...state.domainSuffixes],
    binding_mode: state.binding, source_mode: state.sourceMode, imported_domains: state.importedDomains,
  };
}

function previewDescription() {
  if (state.sourceMode === 'import') return `导入列表 ${state.importedDomains.length} 条`;
  return state.blocks.map(block => {
    if (block.kind === 'fixed') return block.value || '固定';
    if (block.kind === 'unlimited') return `随机${block.length}位`;
    return `${block.value || '规律'}${block.random_position ? '（随机位置）' : ''}`;
  }).join(' + ');
}

function schedulePreview() {
  clearTimeout(state.previewTimer);
  state.previewTimer = setTimeout(refreshPreview, 140);
}

async function refreshPreview() {
  if (!window.pywebview?.api) return;
  const result = await window.pywebview.api.preview(rulePayload());
  const bar = $('previewBar');
  if (result.ok) {
    bar.classList.remove('error');
    $('previewPieces').textContent = `${previewDescription()} →`;
    const firstGroup = result.sample_groups?.[0] || [];
    $('previewDomain').textContent = firstGroup.length > 1 ? firstGroup.join(' / ') : (result.samples[0] || '');
    $('previewLength').textContent = result.imported ? `共 ${Number(result.space).toLocaleString('zh-CN')} 条` : `名称长度 ${result.length} / 63`;
    $('footerSpace').textContent = `${Number(result.space).toLocaleString('zh-CN')} 个`;
  } else {
    bar.classList.add('error');
    $('previewPieces').textContent = result.message;
    $('previewDomain').textContent = '';
    $('previewLength').textContent = '';
    $('footerSpace').textContent = '—';
  }
}

function collectSettings() {
  return {
    ...state.settings, ...rulePayload(), quick_templates: state.quickTemplates, imported_file: state.importedFile,
    site_name: $('siteName').value.trim(), site_url: $('siteUrl').value.trim(), sites: state.sites,
    preferred_page_url: $('pageChoice').value, interval: $('interval').value, retry_interval: $('retryInterval').value,
    limit_tests_enabled: $('limitTestsEnabled').checked, limit_tests: $('limitTests').value,
    limit_found_enabled: $('limitFoundEnabled').checked, limit_found: $('limitFound').value,
    run_until_stopped: $('runUntilStopped').checked, excel_path: state.settings.excel_path || '',
    history_excel_path: $('historyExcelPath').value.trim(),
  };
}

function scheduleSave() {
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(async () => {
    if (window.pywebview?.api) {
      const payload = collectSettings();
      state.settings = payload;
      await window.pywebview.api.save_settings(payload);
    }
  }, 400);
}

function applySettings(settings) {
  state.settings = settings;
  state.characters = new Set(settings.characters || state.allowed);
  state.blocks = (settings.blocks || [{ kind: 'common', value: 'AAA', length: 1 }]).map(block => ({ ...block, length: Number(block.length) || 1, random_position: !!block.random_position }));
  state.containmentRules = (settings.containment_rules || []).map(rule => ({ value: rule.value, minimum: Number(rule.minimum) || 1 }));
  state.domainSuffixes = new Set(settings.domain_suffixes?.length ? settings.domain_suffixes : ['.com']);
  state.binding = settings.binding_mode || 'independent';
  state.importedDomains = settings.imported_domains || [];
  state.importedFile = settings.imported_file || '';
  state.sourceMode = settings.source_mode || 'generator';
  if (state.sourceMode === 'import' && !state.importedDomains.length) state.sourceMode = 'generator';
  state.quickTemplates = settings.quick_templates || [];
  state.sites = settings.sites || [];
  $('siteName').value = settings.site_name || '';
  $('siteUrl').value = settings.site_url || '';
  $('interval').value = settings.interval || '5';
  $('retryInterval').value = settings.retry_interval || '10';
  $('limitTestsEnabled').checked = !!settings.limit_tests_enabled;
  $('limitTests').value = settings.limit_tests || '10000';
  $('limitFoundEnabled').checked = !!settings.limit_found_enabled;
  $('limitFound').value = settings.limit_found || '100';
  $('runUntilStopped').checked = !!settings.run_until_stopped;
  $('historyExcelPath').value = settings.history_excel_path || '';
  renderSites(settings.site_name);
  renderCharacterSummary(); renderSuffixSummary(); renderBlocks(); renderContainmentRules(); renderBinding(); renderSourceMode(); renderQuickTemplates();
  $('footerInterval').textContent = `${settings.interval || 5} 秒`;
}

function renderBinding() {
  document.querySelectorAll('[data-binding]').forEach(button => button.classList.toggle('active', button.dataset.binding === state.binding));
}

function renderSites(selectedName) {
  $('siteSelect').innerHTML = state.sites.map(site => `<option value="${esc(site.name)}"${site.name === selectedName ? ' selected' : ''}>${esc(site.name)}</option>`).join('');
}

function renderSourceMode() {
  document.querySelectorAll('[data-source-mode]').forEach(button => button.classList.toggle('active', button.dataset.sourceMode === state.sourceMode));
  $('importPanel').hidden = state.sourceMode !== 'import';
  const generatorDisabled = state.sourceMode === 'import';
  document.querySelectorAll('.generator-only').forEach(element => {
    element.classList.toggle('source-disabled', generatorDisabled);
    element.inert = generatorDisabled;
    element.setAttribute('aria-disabled', String(generatorDisabled));
    element.title = generatorDisabled ? '当前使用导入域名；清空导入或切回随机生成后即可编辑' : '';
  });
  $('importCount').textContent = state.importedDomains.length ? `已导入 ${state.importedDomains.length.toLocaleString('zh-CN')} 个域名` : '尚未导入';
  $('importFile').textContent = state.importedFile || '支持 TXT、CSV、Excel';
  $('clearImportedDomains').disabled = !state.importedDomains.length;
  schedulePreview();
}

function renderQuickTemplates() {
  const wrap = $('quickPatterns');
  if (!state.quickTemplates.length) {
    wrap.innerHTML = '<span class="hint">还没有快捷模板，可点击“新增模板”或组合块上的 ☆ 保存。</span>';
    return;
  }
  wrap.innerHTML = state.quickTemplates.map((template, index) => `<span class="quick-template"><button class="quick-template-main" data-quick-index="${index}">${esc(template.name || template.value || blockLabel(template.kind))}</button><button class="quick-template-delete" data-delete-quick="${index}" title="删除模板">×</button></span>`).join('');
}

function openTemplateModal(block = null) {
  const source = block || { kind: 'custom', value: 'AA', length: 4 };
  state.templateRandomPosition = !!source.random_position;
  $('templateName').value = source.name || source.value || blockLabel(source.kind);
  $('templateKind').value = source.kind || 'custom';
  $('templateValue').value = source.value || '';
  $('templateLength').value = Number(source.length) || 4;
  showModal('templateModal');
}

function saveQuickTemplate() {
  const kind = $('templateKind').value;
  const value = $('templateValue').value.trim();
  const length = Number($('templateLength').value) || 1;
  const name = $('templateName').value.trim() || value || blockLabel(kind);
  if ((kind === 'common' || kind === 'custom') && !/^[A-Za-z]+$/.test(value)) { toast('规律只能使用英文字母占位符', 'error'); return; }
  if (kind === 'common' && !state.patterns.includes(value.toUpperCase())) { toast('请选择软件已有的常用规律，其他规律请保存为“自定义规律”', 'error'); return; }
  if (kind === 'fixed' && !/^[a-zA-Z0-9-]+$/.test(value)) { toast('固定文字只能包含字母、数字和半角连字符 -', 'error'); return; }
  state.quickTemplates.push({
    kind,
    value: kind === 'fixed' ? value.toLowerCase() : value.toUpperCase(),
    length,
    name,
    random_position: kind === 'unlimited' ? false : state.templateRandomPosition,
  });
  hideModal('templateModal');
  renderQuickTemplates();
  scheduleSave();
}

function siteDisplay(value) { try { return new URL(value).hostname; } catch { return value || '历史记录'; } }
function formatTime(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

function renderResults() {
  const query = $('resultSearch').value.trim().toLowerCase();
  const rows = state.results.filter(row => !query || Object.values(row).some(value => String(value).toLowerCase().includes(query)));
  $('resultFound').textContent = $('foundCount').textContent;
  const body = $('resultBody');
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="5" class="empty-row">还没有符合条件的可注册域名</td></tr>';
    return;
  }
  body.innerHTML = rows.map(row => `<tr><td class="domain-cell" data-domain="${esc(row.domain)}">${esc(row.domain)}</td><td>${esc(row.pattern)}</td><td>${esc(siteDisplay(row.site))}</td><td>${esc(formatTime(row.time))}</td><td><span class="status-ok">可注册</span></td></tr>`).join('');
}

function historyStatusClass(status) {
  if (status === 'exact_available') return 'available';
  if (status === 'exact_unavailable') return 'unavailable';
  if (status === 'query_failed' || status === 'query_started') return 'warning';
  return 'neutral';
}

function renderHistory() {
  const query = $('historySearch').value.trim().toLowerCase();
  const status = $('historyStatus').value;
  state.visibleHistory = state.history.filter(row => (!status || row.status === status) && (!query || [row.domain, row.status_label, row.pattern, row.site, row.detail].some(value => String(value || '').toLowerCase().includes(query))));
  $('historyTotal').textContent = state.history.length;
  $('historySelected').textContent = state.selectedHistory.size;
  $('historyFilterCount').textContent = `${state.visibleHistory.length} 条记录`;
  const visible = state.visibleHistory.map(row => row.domain);
  $('historyCheckAll').checked = visible.length > 0 && visible.every(domain => state.selectedHistory.has(domain));
  $('historyCheckAll').indeterminate = visible.some(domain => state.selectedHistory.has(domain)) && !$('historyCheckAll').checked;
  const body = $('historyBody');
  if (!state.visibleHistory.length) body.innerHTML = '<tr><td colspan="6" class="empty-row">没有符合当前筛选条件的已查询记录</td></tr>';
  else body.innerHTML = state.visibleHistory.map(row => `<tr><td class="select-col"><input type="checkbox" data-history-domain="${esc(row.domain)}"${state.selectedHistory.has(row.domain) ? ' checked' : ''}></td><td class="domain-cell">${esc(row.domain)}</td><td><span class="history-status ${historyStatusClass(row.status)}">${esc(row.status_label)}</span></td><td>${esc(row.pattern)}</td><td>${esc(siteDisplay(row.site))}</td><td>${esc(formatTime(row.time))}</td></tr>`).join('');
  updateControls();
}

function applyHistoryResponse(result) {
  state.history = result.history || [];
  state.results = result.results || state.results;
  const current = new Set(state.history.map(row => row.domain));
  state.selectedHistory = new Set([...state.selectedHistory].filter(domain => current.has(domain)));
  renderHistory(); renderResults();
}

async function refreshHistory() {
  if (!window.pywebview?.api) return;
  const result = await window.pywebview.api.history_state();
  if (result.ok) applyHistoryResponse(result);
}

function scheduleHistoryRefresh() {
  if (!$('page-history').classList.contains('active')) return;
  clearTimeout(state.historyRefreshTimer);
  state.historyRefreshTimer = setTimeout(refreshHistory, 120);
}

function appendLog(text) {
  const log = $('runLog');
  const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  log.textContent += `${log.textContent ? '\n' : ''}[${time}] ${text}`;
  log.scrollTop = log.scrollHeight;
}

async function connectChrome() {
  const url = $('siteUrl').value.trim();
  if (!url) { toast('请先填写查询网址', 'error'); return; }
  setStatus('正在连接 Chrome…', 'running');
  $('connectChrome').disabled = true;
  const result = await window.pywebview.api.connect_browser({ site_url: url, preferred_page_url: $('pageChoice').value });
  $('connectChrome').disabled = false;
  if (!result.ok) {
    state.ready = false; setStatus('Chrome 未连接', 'info'); $('connectionDetail').textContent = result.message;
    toast(result.message, 'error'); updateControls(); return;
  }
  state.ready = !!result.ready;
  $('connectionDetail').textContent = result.selected_url ? `已连接：${result.selected_url}` : result.message;
  $('pageChoice').innerHTML = (result.pages || []).map((page, index) => `<option value="${esc(page.url)}"${page.url === result.selected_url ? ' selected' : ''}>${index + 1}. ${esc(page.title || '未命名网页')} — ${esc(page.url)}</option>`).join('') || '<option value="">没有找到网页</option>';
  setStatus(result.message, state.ready ? 'ready' : 'info');
  if (result.verification_required) toast('请在 Chrome 中手动完成人机验证，然后再次连接。', 'error');
  updateControls(); scheduleSave();
}

async function startSearch() {
  const result = await window.pywebview.api.start_search(collectSettings());
  if (!result.ok) { toast(result.message, 'error'); return; }
  state.running = true; state.paused = false;
  $('checkedCount').textContent = '0'; $('foundCount').textContent = '0'; $('resultFound').textContent = '0';
  $('currentDomain').textContent = '正在连接…'; $('footerTask').textContent = '正在启动查询';
  setStatus('正在启动…', 'running'); updateControls(); appendLog('开始查询');
}

async function pauseSearch() {
  const result = await window.pywebview.api.pause_search();
  if (!result.ok) { toast(result.message, 'error'); return; }
  state.paused = true; updateControls(); setStatus('已暂停，可修改规则后继续', 'info');
}

async function resumeSearch() {
  const result = await window.pywebview.api.resume_search(collectSettings());
  if (!result.ok) { toast(result.message, 'error'); return; }
  state.paused = false; updateControls(); setStatus('新规则已提交，正在继续', 'running');
  appendLog('继续查询；已读取当前界面的最新规则');
}

async function stopSearch() {
  await window.pywebview.api.stop_search();
  $('footerTask').textContent = '正在停止；Chrome 保持原样'; setStatus('正在停止…', 'running');
}

window.handleBackendEvent = function handleBackendEvent(event) {
  const { type, payload } = event;
  if (type === 'status') { setStatus(payload.message, 'running'); appendLog(payload.message); }
  else if (type === 'current') {
    const target = payload.suffixes ? `${payload.domain}（${payload.suffixes}）` : payload.domain;
    $('currentDomain').textContent = target; $('footerTask').textContent = `正在查询 ${target}`; scheduleHistoryRefresh();
  }
  else if (type === 'progress') { $('checkedCount').textContent = payload.checked; $('foundCount').textContent = payload.found; $('resultFound').textContent = payload.found; appendLog(`已检测 ${payload.checked} 个`); scheduleHistoryRefresh(); }
  else if (type === 'found') { state.results.unshift({ domain: payload.domain, pattern: payload.pattern, site: $('siteUrl').value, time: payload.checked_at }); renderResults(); appendLog(`已保存 ${payload.domain}`); toast(`找到可注册域名：${payload.domain}`, 'success'); scheduleHistoryRefresh(); }
  else if (type === 'verification') { setStatus('需要人工验证', 'info'); appendLog(payload.message); toast('请在 Chrome 中手动完成人机验证，完成后软件会继续。', 'error'); }
  else if (type === 'finished') { state.running = false; state.paused = false; setStatus('准备就绪', 'ready'); $('footerTask').textContent = '任务已结束'; appendLog(payload.message); updateControls(); refreshHistory(); }
  else if (type === 'error') { state.running = false; state.paused = false; if (String(payload.message).includes('Chrome') || String(payload.message).includes('浏览器')) state.ready = false; setStatus('查询已停止', 'info'); appendLog(`错误：${payload.message}`); toast(payload.message, 'error'); updateControls(); refreshHistory(); }
};

function bindRuleEvents() {
  $('openCharacterPool').addEventListener('click', openCharacterPool);
  $('openSuffixPool').addEventListener('click', openSuffixPool);
  $('confirmCharacterPool').addEventListener('click', () => {
    state.characters = new Set(state.stagedCharacters); hideModal('characterModal'); renderCharacterSummary(); schedulePreview(); scheduleSave();
  });
  document.querySelectorAll('[data-char-action]').forEach(button => button.addEventListener('click', () => {
    const action = button.dataset.charAction;
    if (action === 'all') state.stagedCharacters = new Set(state.allowed);
    if (action === 'letters') state.stagedCharacters = new Set(state.allowed.filter(char => /[a-z]/.test(char)));
    if (action === 'numbers') state.stagedCharacters = new Set(state.allowed.filter(char => /[0-9]/.test(char)));
    if (action === 'clear') state.stagedCharacters.clear();
    renderCharacters();
  }));
  $('characterPool').addEventListener('click', event => {
    const button = event.target.closest('[data-char]');
    if (!button) return;
    const char = button.dataset.char;
    state.stagedCharacters.has(char) ? state.stagedCharacters.delete(char) : state.stagedCharacters.add(char);
    renderCharacters();
  });
  $('suffixSearch').addEventListener('input', renderSuffixPool);
  document.querySelectorAll('[data-suffix-group]').forEach(button => button.addEventListener('click', () => {
    state.suffixGroup = button.dataset.suffixGroup;
    $('suffixSearch').value = '';
    renderSuffixPool();
  }));
  $('suffixPool').addEventListener('click', event => {
    const button = event.target.closest('[data-suffix]');
    if (!button) return;
    const suffix = button.dataset.suffix;
    state.stagedSuffixes.has(suffix) ? state.stagedSuffixes.delete(suffix) : state.stagedSuffixes.add(suffix);
    renderSuffixPool();
  });
  document.querySelectorAll('[data-suffix-action]').forEach(button => button.addEventListener('click', () => {
    const action = button.dataset.suffixAction;
    if (action === 'visible') visibleSuffixEntries().forEach(entry => state.stagedSuffixes.add(entry.value));
    if (action === 'clear') state.stagedSuffixes.clear();
    renderSuffixPool();
  }));
  $('confirmSuffixPool').addEventListener('click', () => {
    if (!state.stagedSuffixes.size) { toast('请至少选择一个域名后缀', 'error'); return; }
    state.domainSuffixes = new Set(state.stagedSuffixes);
    hideModal('suffixModal'); renderSuffixSummary(); schedulePreview(); scheduleSave();
  });
  $('addContainment').addEventListener('click', addContainmentRule);
  $('containmentList').addEventListener('click', event => {
    const button = event.target.closest('[data-remove-containment]');
    if (!button) return;
    state.containmentRules.splice(Number(button.dataset.removeContainment), 1);
    renderContainmentRules(); schedulePreview(); scheduleSave();
  });
  document.querySelectorAll('[data-add-block]').forEach(button => button.addEventListener('click', () => addBlock(button.dataset.addBlock)));
  $('blockComposer').addEventListener('change', event => {
    const element = event.target.closest('[data-index][data-field]');
    if (!element) return;
    const block = state.blocks[Number(element.dataset.index)];
    if (element.dataset.field === 'kind') switchBlockKind(block, element.value);
    else if (element.dataset.field === 'length') block.length = Number(element.value) || 1;
    else if (element.dataset.field === 'random_position') block.random_position = element.checked;
    else block[element.dataset.field] = element.value;
    renderBlocks(); scheduleSave();
  });
  $('blockComposer').addEventListener('input', event => {
    const element = event.target.closest('[data-index][data-field]');
    if (!element || ['kind', 'random_position'].includes(element.dataset.field)) return;
    const block = state.blocks[Number(element.dataset.index)];
    block[element.dataset.field] = element.dataset.field === 'length' ? (Number(element.value) || 1) : element.value;
    schedulePreview(); scheduleSave();
  });
  $('blockComposer').addEventListener('click', event => {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const index = Number(button.dataset.index); const action = button.dataset.action;
    if (action === 'save-template') { openTemplateModal(state.blocks[index]); return; }
    if (action === 'delete') state.blocks.splice(index, 1);
    if (action === 'duplicate') state.blocks.splice(index + 1, 0, { ...state.blocks[index] });
    if (action === 'random' && state.blocks[index].kind !== 'unlimited') state.blocks[index].random_position = !state.blocks[index].random_position;
    if (action === 'left' && index > 0) [state.blocks[index - 1], state.blocks[index]] = [state.blocks[index], state.blocks[index - 1]];
    if (action === 'right' && index < state.blocks.length - 1) [state.blocks[index + 1], state.blocks[index]] = [state.blocks[index], state.blocks[index + 1]];
    renderBlocks(); scheduleSave();
  });
  document.querySelectorAll('[data-binding]').forEach(button => button.addEventListener('click', () => {
    state.binding = button.dataset.binding; renderBinding(); schedulePreview(); scheduleSave();
  }));
  document.querySelectorAll('[data-source-mode]').forEach(button => button.addEventListener('click', () => {
    state.sourceMode = button.dataset.sourceMode; renderSourceMode(); scheduleSave();
  }));
  $('importDomains').addEventListener('click', async () => {
    const result = await window.pywebview.api.import_domains([...state.domainSuffixes]);
    if (!result.ok) { toast(result.message, 'error'); return; }
    if (!result.path) return;
    state.importedDomains = result.domains; state.importedFile = result.path;
    renderSourceMode(); scheduleSave();
    const skipped = Number(result.skipped_count) || 0;
    toast(skipped ? `已导入 ${result.count} 个域名，忽略 ${skipped} 个无效单元格` : `已导入 ${result.count} 个域名`, 'success');
  });
  $('clearImportedDomains').addEventListener('click', () => {
    state.importedDomains = [];
    state.importedFile = '';
    state.sourceMode = 'generator';
    renderSourceMode();
    scheduleSave();
  });
  $('quickPatterns').addEventListener('click', event => {
    const deleteButton = event.target.closest('[data-delete-quick]');
    if (deleteButton) { state.quickTemplates.splice(Number(deleteButton.dataset.deleteQuick), 1); renderQuickTemplates(); scheduleSave(); return; }
    const button = event.target.closest('[data-quick-index]');
    if (!button) return;
    const template = state.quickTemplates[Number(button.dataset.quickIndex)];
    addBlock(template.kind, template.value, template);
  });
  $('newQuickTemplate').addEventListener('click', () => openTemplateModal());
  $('saveQuickTemplate').addEventListener('click', saveQuickTemplate);
}

function bindHistoryEvents() {
  $('historySearch').addEventListener('input', renderHistory);
  $('historyStatus').addEventListener('change', renderHistory);
  $('historyBody').addEventListener('change', event => {
    const input = event.target.closest('[data-history-domain]');
    if (!input) return;
    input.checked ? state.selectedHistory.add(input.dataset.historyDomain) : state.selectedHistory.delete(input.dataset.historyDomain);
    renderHistory();
  });
  $('historyCheckAll').addEventListener('change', event => {
    state.visibleHistory.forEach(row => event.target.checked ? state.selectedHistory.add(row.domain) : state.selectedHistory.delete(row.domain));
    renderHistory();
  });
  $('chooseHistoryExcel').addEventListener('click', async () => {
    const result = await window.pywebview.api.choose_history_excel($('historyExcelPath').value);
    if (!result.ok) { toast(result.message, 'error'); return; }
    if (result.path) { $('historyExcelPath').value = result.path; scheduleSave(); }
  });
  $('exportHistory').addEventListener('click', async () => {
    const result = await window.pywebview.api.export_history($('historyExcelPath').value);
    if (!result.ok) { toast(result.message, 'error'); return; }
    toast(`已导出 ${result.count} 条查询记录`, 'success');
  });
  $('openHistoryExcel').addEventListener('click', async () => {
    const result = await window.pywebview.api.open_history_excel($('historyExcelPath').value);
    if (!result.ok) { toast(result.message, 'error'); return; }
    toast(`已更新并打开 ${result.count} 条查询记录`, 'success');
  });
  $('deleteSelectedHistory').addEventListener('click', async () => {
    const count = state.selectedHistory.size;
    if (!count || !window.confirm(`确定删除选中的 ${count} 条已查询记录吗？\n删除后这些域名可以重新查询；已有 Excel 文件不会被删除。`)) return;
    const result = await window.pywebview.api.delete_history([...state.selectedHistory]);
    if (!result.ok) { toast(result.message, 'error'); return; }
    state.selectedHistory.clear(); applyHistoryResponse(result); toast(`已删除 ${result.deleted} 条记录，这些域名现在可以重新查询`, 'success');
  });
  $('clearHistory').addEventListener('click', async () => {
    if (!window.confirm(`确定清空全部 ${state.history.length} 条已查询记录吗？\n清空后所有域名都可以重新查询；已有 Excel 文件不会被删除。`)) return;
    const result = await window.pywebview.api.clear_history();
    if (!result.ok) { toast(result.message, 'error'); return; }
    state.selectedHistory.clear(); applyHistoryResponse(result); toast(`已清空 ${result.deleted} 条记录`, 'success');
  });
}

function bindContextMenu() {
  $('resultBody').addEventListener('contextmenu', event => {
    const cell = event.target.closest('[data-domain]');
    if (!cell) return;
    event.preventDefault(); state.contextDomain = cell.dataset.domain;
    const menu = $('domainContextMenu');
    menu.style.left = `${Math.min(event.clientX, window.innerWidth - 130)}px`;
    menu.style.top = `${Math.min(event.clientY, window.innerHeight - 55)}px`;
    menu.hidden = false;
  });
  $('copyDomain').addEventListener('click', async () => {
    const result = await window.pywebview.api.copy_text(state.contextDomain);
    $('domainContextMenu').hidden = true;
    result.ok ? toast(`已复制 ${state.contextDomain}`, 'success') : toast(result.message, 'error');
  });
  document.addEventListener('click', event => {
    if (!event.target.closest('#domainContextMenu')) $('domainContextMenu').hidden = true;
  });
}

function bindEvents() {
  document.querySelectorAll('.nav-item').forEach(button => button.addEventListener('click', () => setPage(button.dataset.page)));
  document.querySelectorAll('[data-close-modal]').forEach(button => button.addEventListener('click', () => hideModal(button.dataset.closeModal)));
  bindRuleEvents(); bindHistoryEvents(); bindContextMenu();
  $('siteSelect').addEventListener('change', () => {
    const site = state.sites.find(item => item.name === $('siteSelect').value);
    if (site) { $('siteName').value = site.name; $('siteUrl').value = site.url; state.ready = false; setStatus('网站已改变，请重新连接', 'info'); updateControls(); scheduleSave(); }
  });
  $('siteUrl').addEventListener('input', () => { state.ready = false; setStatus('网站已改变，请重新连接', 'info'); updateControls(); scheduleSave(); });
  ['siteName', 'interval', 'retryInterval', 'limitTestsEnabled', 'limitTests', 'limitFoundEnabled', 'limitFound', 'runUntilStopped', 'historyExcelPath', 'pageChoice'].forEach(id => $(id).addEventListener('change', () => {
    if (id === 'interval') $('footerInterval').textContent = `${$('interval').value || 0} 秒`;
    scheduleSave();
  }));
  $('saveSite').addEventListener('click', async () => {
    const result = await window.pywebview.api.save_site({ name: $('siteName').value, url: $('siteUrl').value });
    if (!result.ok) { toast(result.message, 'error'); return; }
    state.sites = result.sites; renderSites($('siteName').value); toast('网站已保存', 'success');
  });
  $('deleteSite').addEventListener('click', async () => {
    const result = await window.pywebview.api.delete_site($('siteName').value);
    if (!result.ok) { toast(result.message, 'error'); return; }
    state.sites = result.sites; const site = state.sites[0]; $('siteName').value = site.name; $('siteUrl').value = site.url; renderSites(site.name); toast('网站已删除', 'success');
  });
  $('connectChrome').addEventListener('click', connectChrome);
  $('reconnectPage').addEventListener('click', connectChrome);
  $('topStart').addEventListener('click', startSearch);
  $('topPause').addEventListener('click', pauseSearch);
  $('topResume').addEventListener('click', resumeSearch);
  $('topStop').addEventListener('click', stopSearch);
  $('resultOpenExcel').addEventListener('click', async () => {
    const result = await window.pywebview.api.open_excel(state.settings.excel_path || '');
    if (!result.ok) toast(result.message, 'error');
  });
  $('resultOpenFolder').addEventListener('click', () => window.pywebview.api.open_folder(state.settings.excel_path || ''));
  $('resultSearch').addEventListener('input', renderResults);
  $('homepageLink').addEventListener('click', () => window.pywebview.api.open_homepage());
}

async function initialize() {
  bindEvents();
  const initial = await window.pywebview.api.initial_state();
  state.patterns = initial.patterns; state.allowed = initial.allowed_characters; state.tldCatalog = initial.domain_suffix_catalog || ['.com'];
  state.domainSuffixGroups = initial.domain_suffix_groups || {
    common: state.tldCatalog.map(value => ({ value, label: value })),
  };
  state.results = initial.results || []; state.history = initial.history || [];
  $('appVersion').textContent = `全网域名筛选器 v${initial.version}`;
  $('sideVersion').textContent = `版本 ${initial.version}`;
  applySettings(initial.settings); renderResults(); renderHistory(); updateControls(); refreshPreview();
}

window.addEventListener('pywebviewready', initialize);
