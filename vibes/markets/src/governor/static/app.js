'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const paths = {
  grid: '<path d="M3 3h6v6H3zM15 3h6v6h-6zM3 15h6v6H3zM15 15h6v6h-6z"/>',
  terminal: '<path d="M3 4h18v16H3zM7 9l3 3-3 3M13 15h4"/>',
  blocks: '<path d="M9 3h6v6H9zM3 15h6v6H3zM15 15h6v6h-6zM12 9v3M6 15v-3h12v3"/>',
  wallet: '<path d="M3 5h16v4M3 5v15h18V9H3m13 4h5v4h-5z"/>',
  shield: '<path d="M12 3l8 3v7l-3 5-5 3-5-3-3-5V6zM8 12l3 3 5-6"/>',
  plus: '<path d="M12 4v16M4 12h16"/>',
  arrow: '<path d="M6 18 18 6M6 6h12v12"/>',
  sun: '<path d="M8 8h8v8H8zM12 1v3M12 20v3M1 12h3M20 12h3M4 4l2 2M18 18l2 2M4 20l2-2M18 6l2-2"/>',
  moon: '<path d="M20 14A9 9 0 0 1 10 3 9 9 0 1 0 20 14Z"/>',
  clock: '<path d="M7 3h10l4 4v10l-4 4H7l-4-4V7zM12 7v6h5"/>',
  download: '<path d="M12 3v12M7 10l5 5 5-5M4 17v4h16v-4"/>',
  refresh: '<path d="M20 9V3l-3 3a8 8 0 0 0-13 5M4 15v6l3-3a8 8 0 0 0 13-5M20 9h-6M4 15h6"/>',
  check: '<path d="m5 12 5 5L20 7"/>',
  stop: '<path d="M7 3h10l4 4v10l-4 4H7l-4-4V7zM8 8l8 8M16 8l-8 8"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  copy: '<path d="M8 8h13v13H8zM16 8V3H3v13h5"/>',
  code: '<path d="m7 6-5 6 5 6M17 6l5 6-5 6M14 3l-4 18"/>',
};
const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.grid}</svg>`;
const esc = (value = '') => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const storage = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* Private mode. */ } },
};
function money(value = '0') {
  const units = BigInt(value);
  const decimals = (units % 1000000n).toString().padStart(6, '0').replace(/0+$/, '').padEnd(3, '0');
  return `${units / 1000000n}.${decimals}`;
}
function time(value) {
  return value ? new Date(value).toLocaleTimeString([], {hour12:false, hour:'2-digit', minute:'2-digit', second:'2-digit'}) : '--:--:--';
}
function date(value) {
  return value ? new Date(value).toLocaleDateString([], {month:'short', day:'2-digit'}) : 'Local';
}
function badge(status = 'READY') {
  const normalized = String(status).toLowerCase().replace(/[^a-z_]/g, '');
  return `<span class="status-badge ${normalized}">${status === 'RUNNING' ? '<span class="square-dot"></span>' : ''}${esc(status.replaceAll('_', ' '))}</span>`;
}

let state = null;
let report = null;
let selected = storage.get('governor.session');
let wallet = null;
let walletLoading = false;
let filter = 'all';
let search = '';
let refreshing = false;
let submitting = false;
let discoveryQuery = '';
let discoveryError = '';
let discoverySubmitting = false;
let lastRender = '';
let connected = false;
let toastTimer;
const labels = {overview:'Overview', sessions:'Agent sessions', services:'Services', wallet:'Wallet', policy:'Spending policy'};
const titles = {overview:'Your agents. Your rules.', sessions:'Every mission, accounted for.', services:'Tools for the task.', wallet:'Your devnet vault.', policy:'Set the boundaries.'};
function route() {
  const [view, id] = location.hash.slice(1).split('/');
  return {view: Object.hasOwn(labels, view) ? view : 'overview', id: id || null};
}
function currentBudget() {
  return report?.budget || {available: state?.policy.session_cap || '0', settled:'0', held:'0', session_cap:state?.policy.session_cap || '0', per_call_cap:state?.policy.per_call_cap || '0'};
}
function codexSession() { return report?.client?.runner === 'codex'; }
function livePayment() { return report?.budget.payment_mode === 'solana-devnet'; }
function disabled() { return state?.active_session || submitting || discoverySubmitting || !state || !connected ? 'disabled' : ''; }
function toast(message) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4500);
}
async function api(path, options = {}) {
  const response = await fetch(path, {...options, signal:AbortSignal.timeout(35000), headers:{...options.headers}});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'The request could not be completed.');
  return data;
}
function setTheme(theme) {
  theme = theme === 'dark' ? 'dark' : 'light';
  document.documentElement.dataset.theme = theme;
  storage.set('governor.theme', theme);
  $$('[data-theme-choice]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.themeChoice === theme)));
  $('meta[name="theme-color"]').content = theme === 'dark' ? '#141414' : '#f5f4ef';
}
function sessionPicker() {
  const sessions = state.sessions.filter(s => s.compatible);
  if (!sessions.length) return '<span class="fine-print">NO SESSION YET</span>';
  return `<div class="session-select"><label for="session-select">SESSION</label><select id="session-select" aria-label="Selected session">${sessions.map(s => `<option value="${esc(s.session_id)}" ${s.session_id === selected ? 'selected' : ''}>${esc(s.session_id)}</option>`).join('')}</select></div>`;
}
function stats() {
  const b = currentBudget();
  const cap = BigInt(b.session_cap);
  const settledBlocks = cap ? Number(BigInt(b.settled) * 20n / cap) : 0;
  const heldBlocks = cap ? Number(BigInt(b.held) * 20n / cap) : 0;
  const denied = report?.events.filter(e => e.kind === 'DENIED' || (e.kind === 'PAYMENT_REFUSED')).length || 0;
  return `<div class="stat-grid">
    <article class="stat-card highlight"><div class="stat-top">AVAILABLE BUDGET ${icon('wallet')}</div><div class="stat-value">${money(b.available)}<small>USDC</small></div><div class="stat-caption">of ${money(b.session_cap)} USDC session cap</div><div class="meter" aria-label="${money(b.settled)} settled, ${money(b.held)} held">${Array.from({length:20},(_,i)=>`<i class="${i < settledBlocks ? 'filled' : i < settledBlocks + heldBlocks ? 'held' : ''}"></i>`).join('')}</div></article>
    <article class="stat-card"><div class="stat-top">SETTLED SPEND ${icon('arrow')}</div><div class="stat-value">${money(b.settled)}<small>USDC</small></div><div class="stat-caption">${report?.attempts.filter(a=>a.status === 'SETTLED').length || 0} ${livePayment() ? 'Devnet transfers' : 'simulated payments'} settled</div></article>
    <article class="stat-card"><div class="stat-top">FUNDS ON HOLD ${icon('clock')}</div><div class="stat-value">${money(b.held)}<small>USDC</small></div><div class="stat-caption">${BigInt(b.held) ? 'Reserved until settlement is known' : 'No outstanding reservations'}</div></article>
    <article class="stat-card"><div class="stat-top">PAYMENTS BLOCKED ${icon('shield')}</div><div class="stat-value">${String(denied).padStart(2,'0')}<small>REFUSED</small></div><div class="stat-caption">Before payment authorization</div></article>
  </div>`;
}
function runwayPanel() {
  const r = report?.runway || {state:'UNKNOWN',reason:'NO_OBSERVATIONS'};
  const planned = r.tasksRemaining !== null && r.tasksRemaining !== undefined;
  const completed = r.tasksCompleted || 0;
  const total = completed + (r.tasksRemaining || 0);
  const warning = [...(report?.events || [])].reverse().find(e=>e.kind === 'RUNWAY_STATE_CHANGED' && e.data.to === 'SHORTFALL')?.data.forecast;
  const reasons = {
    INSUFFICIENT_SAMPLES:`Learning from completed work. ${r.sampleCount || 0} of 3 cost samples collected.`,
    TASKS_REMAINING_UNKNOWN:'No caller task list. Affordable calls are shown; completion cost and shortfall are unknown.',
    INSUFFICIENT_TYPE_SAMPLES:`More samples needed for: ${(r.unknownTaskTypes || []).join(', ')}.`,
    NO_OBSERVATIONS:'Add a caller task list to forecast the cost of finishing.',
    NO_OBSERVED_SPEND:'Observed work has no payment cost. No finite task runway can be estimated.',
    FORECAST_UNAVAILABLE:'Forecast unavailable. The payment ledger still enforces its limits.',
    PLAN_COMPLETE:'All caller-listed items are complete. Spending policy held throughout.',
  };
  const explanation = reasons[r.reason] || (r.state === 'SHORTFALL' ? `Projected shortfall: ${money(r.shortfall)} USDC. Choose a route before the cap is exhausted.` : r.state === 'TIGHT' ? 'The remaining work is close to the budget. Prefer approved cheaper routes.' : 'The p90 estimate leaves room inside the remaining budget.');
  const unit = r.sampleUnit === 'settled_payment' ? 'calls' : 'tasks';
  const options = r.options || [];
  const optionText = o => {
    switch(o.action) {
      case 'route-local': return o.requiresApproval ? 'Request permission for local extraction; quality impact is unmeasured.' : `Route ${o.taskIds.length} approved items locally. Estimated payment savings: ${money(o.savings)} USDC. Quality impact unmeasured.`;
      case 'reduce-scope': return `Request dropping ${o.dropTasks} lower-priority items. Estimated savings: ${money(o.savings)} USDC.`;
      case 'prioritized-subset': return `Request completing the first ${o.taskIds.length} affordable items, then stopping deliberately.`;
      case 'raise-cap': return `Request an additional ${money(o.needed)} USDC. The current cap remains unchanged.`;
      default: return 'Ask approved providers for quotes before choosing a cheaper route.';
    }
  };
  return `<section class="panel runway-panel" aria-label="Budget runway"><div class="panel-head"><h2>${icon('arrow')} Budget runway <span class="advisory-label">ADVISORY</span></h2><div class="runway-heading-actions">${badge(r.state)}<button class="text-button" data-action="runway-demo" ${disabled()}>Try demo ${icon('arrow')}</button></div></div><div class="runway-content"><div class="runway-summary"><div class="eyebrow">${planned ? `${completed} OF ${total} TASKS COMPLETE` : `${completed} OBSERVED ${unit.toUpperCase()}`}</div><p>${esc(explanation)}</p>${planned ? `<div class="task-progress" aria-label="${completed} of ${total} tasks complete">${Array.from({length:Math.min(total,30)},(_,i)=>`<i class="${i < Math.floor(completed * Math.min(total,30) / (total || 1)) ? 'done' : ''}"></i>`).join('')}</div>` : ''}</div><div class="runway-metric"><span>PROJECTED REMAINING COST</span><strong>${r.projected ? `${money(r.projected.p50)} → ${money(r.projected.p90)}` : '—'}</strong><small>${r.projected ? 'USDC / p50 → p90 scenario' : 'Awaiting workload and sufficient samples'}</small></div><div class="runway-metric"><span>AFFORDABLE AT P90</span><strong>${r.runwayTasks ?? '—'} <small>${unit}</small></strong><small>${r.burnRate ? `${money(r.burnRate.p90)} USDC / ${unit === 'calls' ? 'call' : 'task'}` : 'No supported estimate yet'}</small></div></div>${options.length ? `<div class="runway-options"><span class="eyebrow">PLANNING OPTIONS · NO AUTOMATIC CAP OR SCOPE CHANGES</span><ul>${options.map(o=>`<li>${esc(optionText(o))}</li>`).join('')}</ul></div>` : ''}${warning && r.state !== 'SHORTFALL' ? `<div class="runway-history">Earlier warning: ${warning.tasksCompleted} tasks done, ${money(warning.remaining)} USDC left, ${money(warning.projected.p90)} projected at p90. Short by ${money(warning.shortfall)} USDC. ${r.reason === 'PLAN_COMPLETE' ? `Finished all ${completed} items with ${money(r.remaining)} USDC remaining.` : 'See runway events for the subsequent decisions.'}</div>` : ''}<div class="panel-foot"><span>OBSERVED COSTS · PAYMENT BUDGET ONLY · ${r.mixedTaskTypes ? 'SEPARATE ESTIMATES PER TASK TYPE' : 'MINIMUM 3 SAMPLES'}</span><span>THE LEDGER ALWAYS DECIDES</span></div></section>`;
}
function advertisedPrice(amount, compatible) {
  return typeof amount === 'string' && /^\d+$/.test(amount) ? compatible ? `${money(amount)} USDC / CALL` : `${amount} ATOMIC UNITS / CALL` : 'Price not supplied';
}
function discoveryPanel() {
  const d = report?.discovery;
  if (codexSession()) {
    const candidates=d?.candidates || [];
    return `<section class="panel discovery-panel"><div class="panel-head"><h2>${icon('blocks')} Codex vendor search</h2>${badge(d?.status || 'IDLE')}</div><div class="discovery-content"><p>Codex searches Bazaar and assesses the listings itself. ${d?.status === 'SEARCHING' ? 'Searching the registry now…' : 'Discovered sellers are advisory; the purchase allowlist stays fixed.'}</p>${d?.query ? `<p class="fine-print">QUERY / ${esc(d.query)}</p>` : ''}${(d?.errors || []).map(e=>`<p class="form-error">${esc(e)}</p>`).join('')}${candidates.map(c=>`<details><summary>${esc(c.description || c.resource || c.id)} · ${esc(advertisedPrice(c.amount,c.compatible))}</summary><pre class="codex-tool-output">${esc(JSON.stringify(c,null,2))}</pre></details>`).join('')}</div><div class="panel-foot"><span>${candidates.length} LISTINGS · ASSESSMENT BY CODEX</span><span>NO GEMINI SCOUT</span></div></section>`;
  }
  if (!d || d.status === 'IDLE') return `<section class="panel discovery-panel"><div class="panel-head"><h2>${icon('blocks')} Vendor scout</h2><span class="status-badge">READY</span></div><div class="discovery-empty"><strong>Find a vendor for the mission<span class="orange">_</span></strong><p>A Gemini scout can search Bazaar in parallel, compare advertised prices and explain its shortlist.</p><a class="text-button" href="#services">Explore Bazaar ${icon('arrow')}</a></div></section>`;
  const live = ['PLANNING','SEARCHING','RANKING'].includes(d.status);
  const stages = ['PLANNING','SEARCHING','RANKING'];
  const stage = stages.indexOf(d.status);
  const events = report.events || [];
  const queries = [...new Set([...(d.queries || []), ...events.filter(e=>['DISCOVERY_SEARCH_STARTED','DISCOVERY_SEARCH_FINISHED'].includes(e.kind)).map(e=>e.data.query)].filter(q=>typeof q === 'string'))];
  const explanations = {PLANNING:'The scout is turning your task into focused searches.', SEARCHING:'Search branches are querying Bazaar concurrently.', RANKING:'Comparing task fit, network compatibility, advertised price and usage signals.', COMPLETED:'Search complete. The recommendation is advisory.', NO_MATCH:'No suitable vendor was found within this search. Try a more specific task or another query.', FAILED:'The scout could not complete this search. Review the details below and retry.', CANCELLED:'The scout stopped before completing its search.'};
  const candidates = Array.isArray(d.candidates) ? [...d.candidates].sort((a,b)=>Number(b.id === d.selected_id)-Number(a.id === d.selected_id)) : [];
  const chosen = candidates.find(c=>c.id === d.selected_id);
  const branch = query => {
    const finish = [...events].reverse().find(e=>e.kind === 'DISCOVERY_SEARCH_FINISHED' && e.data.query === query);
    const started = events.some(e=>e.kind === 'DISCOVERY_SEARCH_STARTED' && e.data.query === query);
    const label = finish ? (finish.data.status === 'FAILED' ? 'FAILED' : `${finish.data.count ?? 0} FOUND`) : live && started ? 'SEARCHING' : live ? 'QUEUED' : 'STOPPED';
    return `<li><span class="branch-node ${finish?.data.status === 'FAILED' ? 'failed' : finish ? 'done' : live && started ? 'active' : ''}"></span><span>${esc(query)}</span><small>${esc(label)}</small></li>`;
  };
  const candidateCard = c => `<article class="vendor-card ${c.id === d.selected_id ? 'recommended' : ''}"><div class="vendor-top"><span class="eyebrow">${c.id === d.selected_id ? 'SCOUT RECOMMENDATION' : 'BAZAAR LISTING'}</span>${badge(c.compatible ? c.within_budget ? 'WITHIN_BUDGET' : 'OVER_BUDGET' : 'INCOMPATIBLE')}</div><h3>${esc(c.description || 'Undescribed endpoint')}</h3><p class="vendor-url">${esc(c.method || 'HTTP')} ${esc(c.resource)}</p><div class="vendor-metrics"><div><span>ADVERTISED PRICE</span><strong>${esc(advertisedPrice(c.amount,c.compatible))}</strong></div><div><span>ESTIMATED TASK FIT</span><strong>${Number.isFinite(c.task_fit) ? Math.max(0,Math.min(100,c.task_fit)) + '/100' : 'Unrated'}</strong></div><div><span>WEIGHTED RANK SCORE</span><strong>${Number.isFinite(c.score) ? Math.max(0,Math.min(100,c.score)) + '/100' : 'Unrated'}</strong></div></div><p class="vendor-network">${esc(c.compatible ? 'Solana Devnet · configured USDC' : c.network || 'Network unspecified')} · ${Number.isFinite(c.calls_30d) ? c.calls_30d : 0} calls / ${Number.isFinite(c.payers_30d) ? c.payers_30d : 0} payers in 30 days</p>${(c.reasons || []).length ? `<ul class="vendor-reasons">${c.reasons.map(reason=>`<li>${esc(reason)}</li>`).join('')}</ul>` : ''}${(c.issues || []).length ? `<div class="vendor-issues">${c.issues.map(issue=>`<p>${esc(issue)}</p>`).join('')}</div>` : ''}</article>`;
  return `<section class="panel discovery-panel" aria-label="Vendor discovery"><div class="panel-head"><h2>${icon('blocks')} Vendor scout <span class="advisory-label">BAZAAR</span></h2><span role="status">${badge(d.status)}</span></div><div class="discovery-content"><div class="scout-stage-list" aria-label="Discovery stages">${stages.map((name,i)=>`<span class="scout-stage ${stage === i ? 'active' : stage > i || d.status === 'COMPLETED' || d.status === 'NO_MATCH' ? 'done' : ''}"><i>${i+1}</i>${name === 'PLANNING' ? 'Plan searches' : name === 'SEARCHING' ? 'Search in parallel' : 'Rank vendors'}</span>`).join('')}</div><p class="scout-query">${esc(d.query)}</p><p class="scout-explanation">${esc(explanations[d.status] || 'Waiting for discovery updates.')}</p>${queries.length ? `<ul class="search-branches" aria-label="Search branches">${queries.map(branch).join('')}</ul>` : ''}${d.summary ? `<div class="scout-recommendation"><span class="eyebrow">${chosen ? 'WHY THIS VENDOR' : 'SCOUT ASSESSMENT'}</span><p>${esc(d.summary)}</p></div>` : ''}${d.partial_results ? '<p class="scout-alert">Search coverage is limited or a branch failed; this is not the entire market.</p>' : ''}${(d.errors || []).length ? `<div class="scout-alert" role="status">${d.errors.map(error=>`<p>${esc(typeof error === 'string' ? error : error.message || 'A discovery step failed.')}</p>`).join('')}</div>` : ''}${candidates.length ? `<div class="vendor-list-heading"><span class="eyebrow">${candidates.length} CANDIDATE${candidates.length === 1 ? '' : 'S'} COMPARED</span><span class="fine-print">TASK FIT IS AN ESTIMATE</span></div><p class="scout-ranking-rule">Ranking: task fit 70%, price 20%, usage 10%. A recommendation needs at least 60/100 task fit, the configured network and USDC token, and room within the current per-call and session budgets.</p><div class="vendor-grid">${candidates.slice(0,4).map(candidateCard).join('')}</div>${candidates.length > 4 ? `<details class="more-vendors"><summary>View ${candidates.length-4} more candidates</summary><div class="vendor-grid">${candidates.slice(4).map(candidateCard).join('')}</div></details>` : ''}` : live ? '<div class="scout-wait"><span class="cursor"></span> Waiting for vendor results…</div>' : ''}</div><div class="scout-usage"><span>SCOUT TOKENS</span><span>${esc(d.usage?.input_tokens ?? 0)} input / ${esc(d.usage?.output_tokens ?? 0)} output / ${esc(d.usage?.thought_tokens ?? 0)} thought</span><small>Gemini inference is billed separately from USDC.</small></div><div class="panel-foot discovery-foot"><span>ADVERTISED PRICES · SELLER QUALITY UNVERIFIED</span><span>DISCOVERED SELLERS: PURCHASES NOT CONNECTED</span></div></section>`;
}
function overview() {
  return `<section class="hero" aria-labelledby="hero-title"><span class="corner tl" aria-hidden="true">+</span><span class="corner tr" aria-hidden="true">+</span><span class="corner bl" aria-hidden="true">+</span>
    <div class="hero-copy"><div class="hero-label"><span class="square-dot"></span> AUTONOMY, WITH A HARD LIMIT.</div><h2 id="hero-title">LET IT RUN.<br><span>SET THE LIMIT.</span></h2><p>Give your AI agent room to work.<br>Keep every payment inside your rules.</p><div class="hero-actions"><button class="button button-primary" data-action="launch" ${disabled()}>Launch an agent ${icon('arrow')}</button><button class="text-button" data-action="demo" ${disabled()}>${icon('terminal')} Run sandbox demo</button></div></div>
    <div class="hero-art"><img src="/assets/guardian.png" width="1254" height="1254" alt="Clay robot guardian holding an orange shield"><span class="art-label">YOUR FRIENDLY BUDGET ENFORCER / 001</span></div></section>
    <div class="section-top"><h2 class="section-title">Session at a glance <small>${livePayment() ? 'REAL DEVNET USDC' : 'SIMULATED USDC'}</small></h2>${sessionPicker()}</div>
    ${stats()}${runwayPanel()}${discoveryPanel()}
    <div class="main-grid"><section class="panel"><div class="panel-head"><h2>${icon('terminal')} Agent session</h2>${badge(report?.status || 'READY')}</div>${sessionBody()}<div class="panel-foot"><span>${esc(report ? report.session_id : 'WAITING FOR YOUR FIRST MISSION')}</span><a class="text-button" href="${report ? '#sessions/' + esc(report.session_id) : '#sessions'}">View session ${icon('arrow')}</a></div></section>
    <section class="panel"><div class="panel-head"><h2>${icon('shield')} Spending guardrails</h2><span class="status-badge">ENFORCED</span></div><div class="policy-preview"><img src="/assets/vault.png" alt="Clay vault with an orange door" width="1254" height="1254"><div><div class="policy-line"><span>Session cap</span><strong>${money(state.policy.session_cap)} USDC</strong></div><div class="policy-line"><span>Per-call cap</span><strong>${money(state.policy.per_call_cap)} USDC</strong></div><div class="policy-line"><span>Payment mode</span><strong class="orange">${livePayment() ? "Real Devnet" : "Sandbox"}</strong></div></div></div><div class="policy-note">Limits checked before authorization. Every decision recorded.</div><div class="panel-foot"><span>POLICY LIVES OUTSIDE THE MODEL</span><a class="text-button" href="#policy">View policy ${icon('arrow')}</a></div></section></div>
    ${activity()}`;
}
function sessionBody() {
  if (!report) return '<div class="empty"><strong>Ready when you are<span class="orange">_</span></strong>Launch an agent or try the sandbox demo.<br>Your session and spending decisions will appear here.</div>';
  const model = codexSession() ? 'CODEX / CLI PLUGIN' : report.events.find(e=>e.kind === 'RUN_STARTED')?.data.model || state.model;
  const finish = [...report.events].reverse().find(e=>e.kind === 'RUN_FINISHED');
  const calls = codexSession() ? (report.tool_results || []).length : finish?.data.tool_calls ?? report.events.filter(e=>e.kind === 'TOOL_RESULT').length;
  return `<div class="session-body"><div class="session-meta"><span>${esc(model)}</span><span>${date(report.events[0]?.time)} / ${time(report.events[0]?.time)}</span></div><p class="session-task">${esc(report.task)}</p><div class="session-details"><div><span>Tool calls</span>${calls}</div><div><span>Payment adapter</span>${livePayment() ? "Real Devnet x402" : "Simulated"}</div><div><span>Model turns</span>${codexSession() ? 'Managed by Codex' : finish?.data.model_turns ?? report.events.filter(e=>e.kind === 'MODEL_RESPONSE').length}</div><div><span>Budget gate</span>Enforced</div></div></div>`;
}
function eventMessage(event) {
  const d = event.data;
  switch(event.kind) {
    case 'CLIENT_REQUEST': return 'Codex CLI connected. Reasoning stays in Codex.';
    case 'CODEX_TOOL_REQUEST': return `Codex → ${d.name} / call ${d.call_id}`;
    case 'CODEX_TOOL_RESULT': return `${d.name} → ${d.result?.code || 'complete'} / call ${d.call_id}`;
    case 'CODEX_MESSAGE': return `${d.role === 'user' ? 'You' : 'Codex'}: ${d.text.slice(0, 180)}`;
    case 'CODEX_FINISHED': return `Codex recorded its final result: ${d.status}`;
    case 'SESSION_CREATED': return 'New session created. Spending policy attached.';
    case 'SESSION_RESUMED': return 'Session resumed. Existing holds preserved.';
    case 'TASK_PLAN': return `Caller task list attached: ${d.items.length} items, in priority order.`;
    case 'LOCAL_TASK_COMPLETED': return `${d.task_id} completed with caller-approved local extraction.`;
    case 'RUNWAY_CHECK': return `Runway ${d.forecast.state} / ${d.forecast.reason || 'advisory check'}${d.forecast.shortfall && d.forecast.shortfall !== '0' ? ' / short by ' + money(d.forecast.shortfall) + ' USDC' : ''}`;
    case 'RUNWAY_STATE_CHANGED': return `${d.from || 'INITIAL'} → ${d.to} / ${d.forecast.tasksCompleted ?? 0} tasks complete / ${d.forecast.projected ? money(d.forecast.projected.p90) + ' USDC projected at p90' : 'projection unavailable'}`;
    case 'DISCOVERY_STARTED': return `Vendor scout started → ${d.query || 'current mission'}`;
    case 'DISCOVERY_PLAN': return `Search plan → ${(d.queries || []).join(' / ')}`;
    case 'DISCOVERY_SEARCH_STARTED': return `Searching Bazaar → ${d.query || ''}`;
    case 'DISCOVERY_SEARCH_FINISHED': return `${d.query || 'Search'} → ${d.status === 'FAILED' ? 'search unavailable' : (d.count ?? 0) + ' candidates'}`;
    case 'DISCOVERY_MODEL_RESPONSE': return `Scout ${d.stage || 'assessment'} / ${d.discovery?.usage?.input_tokens || 0} input, ${d.discovery?.usage?.output_tokens || 0} output, ${d.discovery?.usage?.thought_tokens || 0} thought tokens (cumulative)`;
    case 'DISCOVERY_CANDIDATES': return `Comparing ${(d.candidates || []).length} vendors against task fit and spending limits.`;
    case 'DISCOVERY_RECOMMENDED': return d.summary || 'Vendor recommendation recorded. No payment authorized.';
    case 'DISCOVERY_FINISHED': return `Scout finished → ${d.status || d.discovery?.status || 'results saved'}`;
    case 'DISCOVERY_FAILED': return d.error || d.message || 'Vendor scout failed. No discovery purchase was made.';
    case 'DISCOVERY_CANCELLED': return 'Vendor scout stopped. Available results are preserved.';
    case 'RUN_STARTED': return `Agent started → ${d.model}`;
    case 'MODEL_RESPONSE': return `Model turn ${d.turn} / ${d.cumulative_usage?.input_tokens || 0} input tokens`;
    case 'TOOL_RESULT': return `${d.name} → ${d.code}`;
    case 'RESERVED': return `${money(d.amount)} USDC held → ${d.service}`;
    case 'AUTHORIZING': return livePayment() ? 'Budget gate passed. Devnet transaction signing starting.' : 'Budget gate passed. Simulated authorization starting.';
    case 'SETTLED': return `${money(d.amount)} USDC settled / ${money(d.budget.available)} remaining`;
    case 'DENIED': return `${d.service} → ${d.code}${d.amount ? ' / ' + money(d.amount) + ' USDC refused' : ''}`;
    case 'PAYMENT_REFUSED': return `${d.service || d.service_id || 'Service'} → ${d.code || d.reason || 'Payment refused'}`;
    case 'RELEASED': return `Unsigned hold released → ${d.reason}`;
    case 'RUN_FINISHED': return `Session ${d.status.toLowerCase().replaceAll('_',' ')}. Audit saved.`;
    default: return Object.entries(d).filter(([,v])=>typeof v !== 'object').map(([k,v])=>`${k}: ${v}`).join(' / ') || 'Decision recorded in the audit.';
  }
}
function activity() {
  let events = report?.events || [];
  if (filter === 'payments') events = events.filter(e=>['RESERVED','AUTHORIZING','SETTLED','RELEASED','DENIED','PAYMENT_REFUSED','PAYMENT_PENDING','PAYMENT_UNCERTAIN','SETTLEMENT_MISMATCH'].includes(e.kind) || e.kind.startsWith('X402_'));
  if (filter === 'blocked') events = events.filter(e=>e.kind === 'DENIED' || e.kind === 'PAYMENT_REFUSED');
  if (filter === 'runway') events = events.filter(e=>e.kind.startsWith('RUNWAY_'));
  if (filter === 'discovery') events = events.filter(e=>e.kind.startsWith('DISCOVERY_'));
  return `<section class="activity terminal" aria-label="Agent activity"><div class="terminal-head"><div class="terminal-title"><span class="terminal-dots" aria-hidden="true"><i></i><i></i><i></i></span><h2 class="section-title">Activity stream</h2></div><div class="terminal-filter" role="group" aria-label="Activity filter">${['all','payments','blocked','runway','discovery'].map(f=>`<button data-filter="${f}" class="${filter === f ? 'active' : ''}" aria-pressed="${filter === f}">${f.toUpperCase()}</button>`).join('')}</div></div><div class="terminal-log" tabindex="0" aria-label="Audit events">${events.length ? events.map(e=>`<div class="log-line"><span class="log-time">${time(e.time)}</span><span class="log-kind ${e.kind.includes('DENIED') || e.kind.includes('REFUSED') || e.kind.includes('ERROR') || ['SHORTFALL','TIGHT'].includes(e.data.to) ? 'warn' : ''}">${esc(e.kind)}</span><span class="log-data">${esc(eventMessage(e))}</span></div>`).join('') : `<div class="terminal-empty"><span class="prompt">governor@local:~$</span> ${report ? 'No matching events.' : 'awaiting mission'}<br>${report ? 'Choose another filter to inspect the session.' : 'Runtime ready. Budget gate initialized.<br>Launch an agent to see its decisions here.'}<br><span class="prompt">&gt;</span> <span class="cursor"></span></div>`}</div><div class="terminal-foot"><span>${report?.status === 'RUNNING' ? '● RUNNING' : '○ IDLE'} / ${events.length} EVENTS</span><span>${livePayment() ? 'REAL DEVNET TRANSFERS · VERIFIED RECEIPTS' : 'PAYMENTS SIMULATED · NO ON-CHAIN TRANSACTIONS'}</span></div></section>`;
}
function liveReceipts() {
  if (!livePayment()) return '';
  const rows=report.attempts.filter(a=>['SETTLED','AUTHORIZING'].includes(a.status));
  return `<section class="panel"><div class="panel-head"><h2>${icon('wallet')} Devnet payment receipts</h2><a class="text-button" href="http://127.0.0.1:8788" target="_blank" rel="noopener noreferrer">Vendor console ↗</a></div><div class="discovery-content">${rows.length ? rows.map(a=>`<div class="policy-line"><span>${esc(a.service)} · ${money(a.amount)} USDC</span>${a.status==='SETTLED' ? `<a class="text-button" href="https://explorer.solana.com/tx/${encodeURIComponent(a.result.receipt)}?cluster=devnet" target="_blank" rel="noopener noreferrer">Confirmed transaction ↗</a>` : `<button class="text-button" data-reconcile="${esc(a.attempt_id)}">Recheck receipt · no new payment</button>`}</div>`).join('') : '<p>No confirmed payment yet. Follow the quote and signing events below.</p>'}</div></section>`;
}
function sessionsView() {
  const id = route().id;
  if (id) {
    if (!report || report.session_id !== id) return '<div class="empty">This session is unavailable. Return to the session list or inspect its policy using the CLI.</div>';
    return `<div class="toolbar"><a class="text-button" href="#sessions">← All sessions</a><div class="wallet-actions"><a class="button button-outline compact" href="/api/sessions/${esc(id)}?format=json" download>${icon('download')} Audit JSON</a><a class="button button-outline compact" href="/api/sessions/${esc(id)}?format=csv" download>${icon('download')} Expenses CSV</a></div></div><section class="panel"><div class="panel-head"><h2>${esc(id)}</h2>${badge(report.status)}</div>${sessionBody()}</section><div class="section-top"><h2 class="section-title">Session budget <small>${livePayment() ? 'REAL DEVNET USDC' : 'SIMULATED USDC'}</small></h2></div>${stats()}${runwayPanel()}${discoveryPanel()}${report.result?.answer ? `<section class="answer"><h2>${icon('terminal')} Agent response</h2><p>${esc(report.result.answer)}</p></section>` : ''}${codexConversation()}${codexTools()}${liveReceipts()}${attempts()}${activity()}`;
  }
  const sessions = state.sessions.filter(s=>(s.task + s.session_id).toLowerCase().includes(search.toLowerCase()));
  return `<p class="subheading">Every task has its own budget, persistent holds, and a record of every decision.</p><div class="toolbar"><input id="session-search" type="search" placeholder="Search sessions…" aria-label="Search sessions" value="${esc(search)}"><span class="fine-print">${state.sessions.length} LOCAL SESSIONS</span></div><div class="session-list">${sessions.length ? sessions.map(s=>`<button class="session-row" data-session="${esc(s.session_id)}" ${s.compatible ? '' : 'disabled'}><span class="row-icon">${icon('terminal')}</span><span><span class="row-title">${esc(s.task)}</span><span class="row-sub">${esc(s.session_id)} ${s.compatible ? '' : '· Policy changed — inspect with CLI'}</span></span><span class="row-date">${date(s.created_at)}</span>${icon('chevron')}</button>`).join('') : `<div class="panel empty"><img src="/assets/guardian.png" alt=""><strong>${search ? 'No matching sessions.' : 'A clean slate.'}</strong>${search ? 'Try another search.' : 'Your next idea starts with a mission.'}${search ? '' : '<br><button class="text-button" data-action="launch">Launch your first agent ↗</button>'}</div>`}</div>`;
}
function codexConversation() {
  const messages = report?.conversation || [];
  if (!codexSession() || !messages.length) return '';
  return `<section class="panel"><div class="panel-head"><h2>Terminal conversation</h2><span class="status-badge">${messages.length} MESSAGES</span></div><div class="discovery-content">${messages.map(m=>`<details open><summary>${m.role === 'user' ? 'You' : 'Codex'}</summary><pre class="codex-tool-output">${esc(m.text)}</pre></details>`).join('')}</div></section>`;
}
function codexTools() {
  if (!codexSession()) return '';
  const results=report.tool_results || [];
  return `<section class="panel"><div class="panel-head"><h2>Codex tool results</h2><span class="status-badge">${esc(report.status)}</span></div><div class="discovery-content"><p>Codex owns this task. Governor checks each service call against the budget. ${report.status === 'WAITING' ? 'Waiting for Codex’s next tool call or final result.' : ''}</p>${results.map(r=>`<details><summary>${esc(r.name)} · ${esc(r.call_id)} · ${esc(r.result.code)}</summary><pre class="codex-tool-output">${esc(JSON.stringify(r.result,null,2))}</pre></details>`).join('')}</div></section>`;
}
function attempts() {
  if (!report.attempts.length) return '';
  return `<section class="panel attempts"><div class="panel-head"><h2>Payment attempts</h2><span class="status-badge">${livePayment() ? "REAL DEVNET" : "SIMULATED"}</span></div><table><thead><tr><th>SERVICE</th><th>USDC</th><th>STATUS</th><th>RECEIPT / REASON</th></tr></thead><tbody>${report.attempts.map(a=>`<tr><td>${esc(a.service)}</td><td>${money(a.amount)}</td><td>${badge(a.status)}</td><td>${esc(a.result.code || a.result.receipt || 'Settlement not confirmed')}</td></tr>`).join('')}</tbody></table></section>`;
}
function vendorCard() {
  const v=state.vendor;
  return `<section class="panel"><div class="panel-head"><h2>${icon('wallet')} Local demo vendor</h2><span class="status-badge">REAL DEVNET USDC</span></div><div class="discovery-content"><p>Buy a three-sentence extractive summary for <strong>0.002 USDC</strong>. This uses a real x402 quote, budget check, signature and confirmed wallet transfer.</p><p class="fine-print">${v?.configured ? 'Receiver: ' + esc(v.address) : 'Start governor-vendor to configure the local merchant.'}</p><div class="wallet-actions"><button class="button button-primary compact" data-action="vendor-demo" ${disabled() || (!v?.configured ? 'disabled' : '')}>Pay 0.002 USDC · one-call demo</button><button class="button button-outline compact" data-action="vendor-agent" ${disabled() || (!v?.configured ? 'disabled' : '')}>Use Gemini + Devnet payment</button><a class="text-button" href="http://127.0.0.1:8788" target="_blank" rel="noopener noreferrer">Open vendor console ↗</a></div></div></section>`;
}
function servicesView() {
  const names = {summary:'Document summary', 'price-change':'The moving price', overpriced:'Over the limit', timeout:'The lost response'};
  const details = {summary:'A paid extractive summary. Returns the opening sentences of your document.', 'price-change':'An adversarial seller that advertises one price and asks for more at checkout.', overpriced:'A deliberately expensive service for exercising the per-call spending cap.', timeout:'A payment with an unknown settlement outcome. Its funds stay held.'};
  return `${vendorCard()}<p class="subheading">Find paid APIs with a Gemini scout. It searches Bazaar in parallel and ranks vendors for your task, network and spending limits.</p><section class="panel bazaar-search-panel"><div class="panel-head"><h2>${icon('blocks')} Search Bazaar</h2><span class="status-badge">SOLANA DEVNET</span></div><form id="discovery-form" class="discovery-form"><label for="discovery-query">WHAT SHOULD THE VENDOR DO?</label><div class="discovery-search-row"><input id="discovery-query" type="search" maxlength="400" required placeholder="e.g. Summarize a document within 0.003 USDC" value="${esc(discoveryQuery)}"><button class="button button-primary" type="submit" ${disabled()}>${icon('blocks')} ${discoverySubmitting ? 'Starting…' : 'Find vendors'}</button></div><p class="fine-print">Search does not buy anything. Gemini inference is billed separately. ${state.active_session ? 'A session is active; wait for it to finish before starting a standalone search.' : 'The search plan, parallel branches and recommendation appear live in the session.'}</p>${discoveryError ? `<p class="form-error" role="alert">${esc(discoveryError)}</p>` : ''}</form></section>${discoveryPanel()}<div class="section-top"><h2 class="section-title">Local sandbox services <small>MOCK PAYMENTS</small></h2></div><p class="subheading sandbox-description">An allowlisted sandbox catalog. Try a service to see how your agent handles a purchase, a refusal, or an uncertain settlement.</p><div class="service-grid">${state.services.map(s=>`<article class="service-card"><div class="service-card-top"><span class="service-icon">${icon(s.id === 'summary' ? 'code' : s.id === 'timeout' ? 'clock' : 'shield')}</span><span class="status-badge">${s.id === 'summary' ? 'SANDBOX SERVICE' : 'TEST SCENARIO'}</span></div><h2>${names[s.id] || esc(s.id)}</h2><p>${details[s.id] || esc(s.description)}</p><div class="service-price"><span>${money(s.advertised_amount)} <small>USDC / CALL · ADVERTISED</small></span><button class="text-button" data-service="${esc(s.id)}" ${disabled()}>Try it ${icon('arrow')}</button></div></article>`).join('')}</div><div class="info-banner">Local sandbox services use the simulated payment adapter. Their purchases do not move wallet funds. Gemini model costs are billed separately.</div>`;
}
function walletView() {
  const w = wallet;
  const verified = w?.status === 'verified';
  const message = !w ? 'Check your on-chain USDC and SOL balances.' : w.status === 'not_configured' ? 'No local devnet wallet is configured yet.' : w.status === 'unavailable' ? 'The RPC could not verify your balance. Refresh to try again.' : 'Balance verified on Solana Devnet.';
  return `<p class="subheading">Your development wallet, with live on-chain balances. Choose a Devnet payment mode to transfer USDC to the local demo vendor. Sandbox modes remain simulated.</p><div class="wallet-layout"><section class="wallet-card"><img class="wallet-art" src="/assets/vault.png" alt="Clay vault with an orange door"><span class="eyebrow">DEVNET USDC BALANCE</span><div class="wallet-value"><h2>${verified ? money(w.usdc_atomic) : '—'}</h2><span class="muted">USDC / TEST FUNDS</span></div><div class="wallet-address">${esc(w?.address || 'WALLET ADDRESS NOT LOADED')}</div><div class="wallet-actions"><button class="button button-primary compact" data-action="wallet-refresh" ${walletLoading ? 'disabled' : ''}>${icon('refresh')} ${walletLoading ? 'Checking…' : 'Refresh balance'}</button>${w?.address ? `<button class="button button-outline compact" data-action="copy-address">${icon('copy')} Copy address</button>` : ''}</div><p class="fine-print" id="wallet-status" role="status">${esc(message)}</p></section><section class="panel"><div class="panel-head"><h2>${icon('wallet')} Wallet details</h2>${badge(verified ? 'VERIFIED' : 'DEVNET')}</div><div class="wallet-facts"><div><span>Network</span>Solana Devnet</div><div><span>SOL balance</span>${verified ? esc(w.sol) + ' SOL' : 'Not checked'}</div><div><span>Last checked</span>${verified ? time(w.checked_at) : '—'}</div><div><span>Agent payment signing</span>Local vendor · Devnet modes</div><div><span>Custody</span>Local keypair</div></div><div class="panel-foot">${w?.address ? `<a class="text-button" target="_blank" rel="noopener noreferrer" href="https://explorer.solana.com/address/${encodeURIComponent(w.address)}?cluster=devnet">View on Solana Explorer ${icon('arrow')}</a>` : 'Configure the wallet with governor-wallet init.'}</div></section></div><div class="info-banner">The session budget is a spending allowance, separate from the wallet balance. Simulated settlements do not reduce these on-chain funds.</div>`;
}
function policyView() {
  const p = state.policy;
  return `<p class="subheading">Hard limits enforced by Governor before payment authorization. The agent can read these rules; it cannot change them.</p><div class="policy-grid"><section class="policy-card"><span class="eyebrow">01 / TOTAL EXPOSURE</span><div class="policy-number">${money(p.session_cap)} <small>USDC</small></div><h2>Session spending cap</h2><p>Settled payments plus outstanding holds must stay inside this allowance.</p></section><section class="policy-card"><span class="eyebrow">02 / SINGLE PURCHASE</span><div class="policy-number">${money(p.per_call_cap)} <small>USDC</small></div><h2>Per-call spending cap</h2><p>Any purchase above this price is refused before authorization.</p></section></div><section class="panel policy-table"><div class="panel-head"><h2>${icon('shield')} Runtime boundaries</h2><span class="status-badge">READ ONLY</span></div><div class="policy-line"><span>Maximum model turns</span><strong>${p.max_turns}</strong></div><div class="policy-line"><span>Maximum tool calls</span><strong>${p.max_tool_calls}</strong></div><div class="policy-line"><span>Run deadline</span><strong>${p.run_timeout_seconds} seconds</strong></div><div class="policy-line"><span>Ambiguous settlements</span><strong>Hold retained</strong></div><div class="policy-line"><span>Payment adapters</span><strong>Sandbox + local vendor Devnet</strong></div><div class="policy-line"><span>Gemini backend</span><strong>${esc(state.backend === 'vertex' ? 'Google Cloud' : 'Developer API')}</strong></div><div class="policy-line"><span>Model</span><strong>${esc(state.model)}</strong></div></section><div class="info-banner">Policy is loaded from the operator configuration when Governor starts. Changing it requires restarting the local server; sessions created with a different policy cannot be resumed under the new limits.</div>`;
}
function render(force = false) {
  const {view} = route();
  const signature = JSON.stringify([state, report, view, route().id, filter, search, wallet, walletLoading, submitting, connected, discoveryQuery, discoveryError, discoverySubmitting]);
  if (!force && signature === lastRender) return;
  lastRender = signature;
  const log = $('.terminal-log');
  const scroll = log?.scrollTop || 0;
  const atBottom = !log || log.scrollHeight - log.scrollTop - log.clientHeight < 30;
  $('#breadcrumb').textContent = labels[view];
  $('#page-title').textContent = titles[view];
  $('#page-eyebrow').textContent = {overview:'THE CONTROL ROOM', sessions:'MISSION LOG', services:'BAZAAR / VENDOR DISCOVERY', wallet:'FUNDS & NETWORK', policy:'THE RULES OF ENGAGEMENT'}[view];
  $$('nav [data-view]').forEach(link => {link.classList.toggle('active', link.dataset.view === view); if(link.dataset.view === view) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current');});
  $('#new-session').disabled = !!disabled();
  if (!state) { $('#view-content').innerHTML = '<div class="loading">Connecting to Governor<span class="cursor"></span></div>'; return; }
  const focusedInput = ['session-search','discovery-query'].includes(document.activeElement?.id) ? {id:document.activeElement.id,start:document.activeElement.selectionStart,end:document.activeElement.selectionEnd} : null;
  const expandedVendors = $('.more-vendors')?.open;
  $('#view-content').innerHTML = ({overview, sessions:sessionsView, services:servicesView, wallet:walletView, policy:policyView}[view])();
  const nextLog = $('.terminal-log');
  if(nextLog) nextLog.scrollTop = atBottom ? nextLog.scrollHeight : scroll;
  if (focusedInput) { const input = $('#' + focusedInput.id); input?.focus({preventScroll:true}); input?.setSelectionRange(focusedInput.start,focusedInput.end); }
  if (expandedVendors && $('.more-vendors')) $('.more-vendors').open = true;
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    state = await api('/api/state');
    const requested = route().id;
    const ids = state.sessions.filter(s=>s.compatible).map(s=>s.session_id);
    selected = requested || (ids.includes(selected) ? selected : state.active_session || ids[0] || null);
    report = selected ? await api('/api/sessions/' + encodeURIComponent(selected)) : null;
    if(selected) storage.set('governor.session', selected);
    connected = true;
    $('#connection-error').hidden = true;
  } catch (error) {
    connected = false;
    $('#connection-error').textContent = `${error.message} Displayed data may be stale. Retrying the local connection…`;
    $('#connection-error').hidden = false;
  } finally {
    refreshing = false;
    render();
  }
}
function openLauncher(task = '') {
  if (!state || state.active_session || submitting) return toast('Wait for the current run or local connection.');
  $('#launch-error').hidden = true;
  $('#run-mode').value = 'gemini';
  $('#task').value = task;
  $('#task-list').value = '';
  $('#allow-local').checked = false;
  $('#discover-vendors').checked = true;
  $('#launch-policy').innerHTML = `<div><span>SESSION CAP</span> ${money(state.policy.session_cap)} USDC</div><div><span>PER CALL</span> ${money(state.policy.per_call_cap)} USDC</div>`;
  modeChanged();
  $('#launch-dialog').showModal();
  $('#task').focus();
}
function modeChanged() {
  const demo = !['gemini','gemini-devnet'].includes($('#run-mode').value);
  $('#task-list').disabled = demo;
  $('#allow-local').disabled = demo;
  $('#discover-vendors').disabled = demo;
  $('#task').disabled = demo;
  $('#task').required = !demo;
  $$('.task-presets button').forEach(button => {button.disabled = demo;});
  $('#mode-note').textContent = ['vendor-demo','gemini-devnet'].includes($('#run-mode').value) ? 'REAL DEVNET PAYMENT: purchases transfer wallet USDC to the local demo vendor (0.002 USDC/call). The vendor-demo mode makes exactly one purchase. Gemini inference, when selected, is billed separately.' : $('#run-mode').value === 'runway-demo' ? 'Ten pre-approved items: pay for three, detect the p90 shortfall, then finish locally. Includes a separate hard-cap refusal. No API calls or wallet transactions.' : demo ? 'A scripted scenario exercises purchases, cap refusals, a lost settlement response, and a local fallback. No API calls or wallet transactions.' : 'Gemini inference uses your configured Google credentials. Model costs are separate from the simulated USDC budget.';
}
async function startRun(mode, task = '') {
  if(submitting) return;
  submitting = true;
  $('#submit-run').disabled = true;
  $('#launch-error').hidden = true;
  render();
  try {
    const payload = {mode,task};
    if (['gemini','gemini-devnet'].includes(mode)) payload.discover = $('#discover-vendors').checked;
    if (['gemini','gemini-devnet'].includes(mode) && $('#task-list').value.trim()) {
      payload.task_list = $('#task-list').value.split('\n').map(line=>line.trim()).filter(Boolean).map((line,i)=>{
        const separator = line.indexOf('|');
        return {id:'task-' + String(i+1).padStart(2,'0'),type:separator < 0 ? 'summary' : line.slice(0,separator).trim(),text:separator < 0 ? line : line.slice(separator+1).trim(),allow_local:$('#allow-local').checked};
      });
    }
    const result = await api('/api/runs', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    selected = result.session_id;
    storage.set('governor.session',selected);
    $('#launch-dialog').close();
    location.hash = 'sessions/' + selected;
    toast(['vendor-demo','gemini-devnet'].includes(mode) ? 'Devnet run started. Follow the real payment in this session.' : mode !== 'gemini' ? 'Sandbox started. Watch the spending gate at work.' : 'Agent launched. Your spending rules are active.');
    await refresh();
  } catch(error) {
    if ($('#launch-dialog').open) { $('#launch-error').textContent = error.message; $('#launch-error').hidden = false; }
    else toast(error.message);
  } finally {
    submitting = false;
    $('#submit-run').disabled = false;
    render();
  }
}
async function startDiscovery() {
  if (discoverySubmitting || submitting) return;
  const query = discoveryQuery.trim();
  if (!query) { discoveryError = 'Describe the service you need.'; render(); return; }
  discoverySubmitting = true;
  discoveryError = '';
  render();
  try {
    const result = await api('/api/discovery', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query})});
    selected = result.session_id;
    storage.set('governor.session', selected);
    location.hash = 'sessions/' + selected;
    toast('Vendor scout launched. Watch its searches and shortlist live.');
    await refresh();
  } catch (error) {
    discoveryError = error.message;
    if (route().view !== 'services') toast(error.message);
  } finally {
    discoverySubmitting = false;
    render();
  }
}
async function refreshWallet() {
  if(walletLoading) return;
  walletLoading = true;
  render();
  try {wallet = await api('/api/wallet');}
  catch(error) {toast(error.message); wallet = {...wallet,status:'unavailable'};}
  finally {walletLoading = false; render();}
}
document.addEventListener('click', async event => {
  const button = event.target.closest('button');
  if (!button || button.disabled) return;
  if(button.dataset.themeChoice) return setTheme(button.dataset.themeChoice);
  if(button.dataset.filter) {filter = button.dataset.filter; return render();}
  if(button.dataset.session) {location.hash = 'sessions/' + button.dataset.session; return;}
  if(button.dataset.reconcile) {
    button.disabled=true;
    try { const result=await api('/api/reconcile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:report.session_id,attempt_id:button.dataset.reconcile})}); toast(result.code==='SETTLED' ? 'On-chain receipt verified; budget reconciled.' : 'Payment is still unconfirmed. Hold retained.'); await refresh(); } catch(error){toast(error.message);} finally{button.disabled=false;} return;
  }
  if(button.dataset.service) return openLauncher(`Use the ${button.dataset.service} service to summarize this text: Autonomous agents can buy services. Governor enforces a hard spending budget. If the payment is refused or pending, use the local fallback and explain the outcome.`);
  if(button.dataset.preset) {
    $('#task').value = {
      budget:'Call get_budget exactly once, then report the available atomic USDC budget in one sentence. Do not purchase anything.',
      summary:'Use the summary service for this text: Agents buy services. Governor enforces a budget. Preserve funds on ambiguous failures. Report the result and remaining budget.',
      refusal:'Try the overpriced service for this text: Spending limits must hold. If it is refused, summarize locally and explain the refusal. Do not retry the denied purchase.',
    }[button.dataset.preset];
    $('#task').focus(); return;
  }
  switch(button.dataset.action) {
    case 'launch': return openLauncher();
    case 'demo': return startRun('demo');
    case 'runway-demo': return startRun('runway-demo');
    case 'vendor-demo': return startRun('vendor-demo');
    case 'vendor-agent':
      openLauncher('Use vendor-summary to summarize this text: Agents buy services. Governor checks the budget before signing. The vendor receives real Devnet USDC. Report the confirmed payment and remaining allowance.');
      $('#run-mode').value='gemini-devnet'; $('#discover-vendors').checked=false; modeChanged(); return;
    case 'wallet-refresh': return refreshWallet();
    case 'copy-address':
      try {await navigator.clipboard.writeText(wallet.address); toast('Wallet address copied.');}
      catch {toast('Could not copy. Select the wallet address to copy it manually.');}
      return;
  }
});
document.addEventListener('change', event => {
  if(event.target.id === 'session-select') {selected = event.target.value; report = null; refresh();}
});
document.addEventListener('input', event => {
  if(event.target.id === 'discovery-query') { discoveryQuery = event.target.value; return; }
  if(event.target.id === 'session-search') {search = event.target.value; render();}
});
document.addEventListener('submit', event=>{if(event.target.id === 'discovery-form') {event.preventDefault(); if(event.target.reportValidity()) startDiscovery();}});
$('#new-session').addEventListener('click',()=>openLauncher());
$('#close-dialog').addEventListener('click',()=>$('#launch-dialog').close());
$('#run-mode').addEventListener('change', modeChanged);
$('#launch-form').addEventListener('submit', event=>{event.preventDefault(); if(!$('#launch-form').reportValidity()) return; startRun($('#run-mode').value, $('#task').value.trim());});
window.addEventListener('hashchange',()=>{filter='all';render();refresh();if(route().view === 'wallet' && !wallet) refreshWallet();});
$$('[data-icon]').forEach(element => {element.innerHTML = icon(element.dataset.icon);});
setTheme(storage.get('governor.theme') || 'light');
render();
refresh();
if(route().view === 'wallet') refreshWallet();
setInterval(()=>{if(!document.hidden) refresh();},1500);
document.addEventListener('visibilitychange',()=>{if(!document.hidden) refresh();});
