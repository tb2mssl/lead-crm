const $ = (s) => document.querySelector(s);
const cfg = JSON.parse($('#cfg').textContent);
const css = getComputedStyle(document.documentElement);
const color = (status) => css.getPropertyValue('--s-' + status).trim();
const accent = css.getPropertyValue('--accent').trim();
const money = (n) => cfg.currency + Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

Chart.defaults.font.family = "'Instrument Sans', system-ui, sans-serif";
Chart.defaults.color = '#5e7073';
if (matchMedia('(prefers-reduced-motion: reduce)').matches) Chart.defaults.animation = false;

let leads = [];
const charts = {};

/* ---------- API ---------- */
async function api(url, opts = {}) {
  const res = await fetch(url, { headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' }, ...opts });
  if (res.status === 401) { location.href = '/login'; throw new Error('Please log in.'); }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || 'Request failed');
  }
  return res.status === 204 ? null : res.json();
}

async function refresh() {
  const [list, stats] = await Promise.all([api('/api/leads'), api('/api/stats')]);
  leads = list;
  renderTable();
  renderStats(stats);
}

function toast(msg, isError = false) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'show' + (isError ? ' error' : '');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.className = ''), 2500);
}

/* ---------- table ---------- */
const row = (l) => `
  <tr data-id="${l.id}">
    <td><strong>${esc(l.name)}</strong><small>${esc(l.company)}</small></td>
    <td>${esc(l.email)}<small>${esc(l.phone)}</small></td>
    <td>${esc(l.source)}</td>
    <td><select class="pill s-${l.status}" aria-label="Status for ${esc(l.name)}">
      ${cfg.statuses.map((s) => `<option ${s === l.status ? 'selected' : ''}>${s}</option>`).join('')}
    </select></td>
    <td class="num">${money(l.value)}</td>
    <td>${esc(l.created_at)}</td>
    <td class="actions">
      <button class="link" data-act="edit">Edit</button>
      ${cfg.role === 'admin' ? '<button class="link danger" data-act="delete">Delete</button>' : ''}
    </td>
  </tr>`;

function renderTable() {
  const q = $('#search').value.trim().toLowerCase();
  const st = $('#statusFilter').value;
  const src = $('#sourceFilter').value, from = $('#dateFrom').value, to = $('#dateTo').value;
  const rows = leads.filter((l) =>
    (!st || l.status === st) && (!src || l.source === src) &&
    (!from || l.created_at >= from) && (!to || l.created_at <= to) &&
    (!q || [l.name, l.company, l.email].join(' ').toLowerCase().includes(q)));
  $('#count').textContent = `${rows.length} of ${leads.length}`;
  $('#tbody').innerHTML = rows.length
    ? rows.map(row).join('')
    : '<tr><td colspan="7" class="empty">No leads match. Clear the filters or add a lead.</td></tr>';
}

/* ---------- dashboard ---------- */
function draw(id, type, data, options = {}) {
  if (charts[id]) { charts[id].data = data; charts[id].update(); return; }
  charts[id] = new Chart($('#' + id), {
    type, data,
    options: { responsive: true, maintainAspectRatio: false, ...options },
  });
}

function renderStats(s) {
  $('#kTotal').textContent = s.total;
  $('#kOpen').textContent = money(s.open_value);
  $('#kWon').textContent = money(s.won_value);
  $('#kRate').textContent = s.win_rate + '%';

  $('#stageBar').innerHTML = cfg.statuses
    .filter((st) => s.by_status[st].n)
    .map((st) => `<button class="seg" data-status="${st}" style="flex:${s.by_status[st].n};background:${color(st)}"
      title="${st}: ${s.by_status[st].n}" aria-label="${st}: ${s.by_status[st].n} leads. Show in table">${s.by_status[st].n}</button>`)
    .join('') || '<span class="empty">No leads yet. Add your first lead.</span>';

  $('#stageLegend').innerHTML = cfg.statuses.map((st) => `
    <li><button data-status="${st}" style="--c:${color(st)}">
      <b>${st}</b><span>${s.by_status[st].n} leads</span><span>${money(s.by_status[st].value)}</span>
    </button></li>`).join('');

  const colors = cfg.statuses.map(color);
  const counts = cfg.statuses.map((st) => s.by_status[st].n);
  const values = cfg.statuses.map((st) => s.by_status[st].value);

  draw('cStatus', 'doughnut',
    { labels: cfg.statuses, datasets: [{ data: counts, backgroundColor: colors, borderColor: '#fbfcfb', borderWidth: 2 }] },
    { cutout: '62%', plugins: { legend: { position: 'right' } } });

  draw('cValue', 'bar',
    { labels: cfg.statuses, datasets: [{ data: values, backgroundColor: colors, borderRadius: 4 }] },
    { indexAxis: 'y', plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => money(c.raw) } } },
      scales: { x: { ticks: { callback: (v) => money(v) } }, y: { grid: { display: false } } } });

  draw('cSource', 'bar',
    { labels: s.sources.map((x) => x.source), datasets: [{ data: s.sources.map((x) => x.n), backgroundColor: accent, borderRadius: 4 }] },
    { plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, ticks: { precision: 0 } }, x: { grid: { display: false } } } });

  draw('cMonthly', 'line',
    { labels: s.monthly.map((m) => new Date(m.month + '-01T00:00').toLocaleString(undefined, { month: 'short', year: '2-digit' })),
      datasets: [{ data: s.monthly.map((m) => m.n), borderColor: accent, backgroundColor: 'rgba(34,50,74,.1)', fill: true, tension: .3, pointRadius: 4 }] },
    { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } });
}

/* ---------- form + dialogs ---------- */
const fill = (sel, items) => (sel.innerHTML = items.map((i) => `<option>${i}</option>`).join(''));
const form = $('#leadForm');
fill(form.source, cfg.sources);
fill(form.status, cfg.statuses);
$('#sourceFilter').insertAdjacentHTML('beforeend', cfg.sources.map((s) => `<option>${s}</option>`).join(''));
$('#statusFilter').insertAdjacentHTML('beforeend', cfg.statuses.map((s) => `<option>${s}</option>`).join(''));

function openForm(lead) {
  form.reset();
  $('#dlgTitle').textContent = lead ? 'Edit lead' : 'Add lead';
  if (lead) for (const [k, v] of Object.entries(lead)) if (form.elements[k]) form.elements[k].value = v ?? '';
  $('#leadDlg').showModal();
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(form));
  const id = data.id;
  delete data.id;
  try {
    await api(id ? `/api/leads/${id}` : '/api/leads', { method: id ? 'PUT' : 'POST', body: JSON.stringify(data) });
    $('#leadDlg').close();
    toast(id ? 'Lead saved' : 'Lead added');
    await refresh();
  } catch (err) { toast(err.message, true); }
});

let pendingDelete = null;
$('#delDlg').addEventListener('close', async (e) => {
  if (e.target.returnValue !== 'ok' || !pendingDelete) return;
  try {
    await api(`/api/leads/${pendingDelete}`, { method: 'DELETE' });
    toast('Lead deleted');
    await refresh();
  } catch (err) { toast(err.message, true); }
  pendingDelete = null;
});

/* ---------- events ---------- */
$('#addBtn').onclick = () => openForm();
$('#cancelBtn').onclick = () => $('#leadDlg').close();
$('#search').oninput = renderTable;
$('#statusFilter').onchange = renderTable;
['#sourceFilter', '#dateFrom', '#dateTo'].forEach((id) => ($(id).onchange = renderTable));
$('#clearBtn').onclick = () => {
  $('#search').value = '';
  ['#statusFilter', '#sourceFilter', '#dateFrom', '#dateTo'].forEach((id) => ($(id).value = ''));
  renderTable();
};
// Excel download uses exactly the filters currently set on screen
$('#xlsBtn').onclick = () => {
  const p = new URLSearchParams({
    q: $('#search').value.trim(), status: $('#statusFilter').value, source: $('#sourceFilter').value,
    date_from: $('#dateFrom').value, date_to: $('#dateTo').value,
  });
  for (const [k, v] of [...p]) if (!v) p.delete(k);
  location.href = '/api/leads/export.xlsx' + (p.toString() ? '?' + p : '');
};

$('#tbody').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
  const lead = leads.find((l) => l.id === +btn.closest('tr').dataset.id);
  if (btn.dataset.act === 'edit') openForm(lead);
  else {
    pendingDelete = lead.id;
    $('#delText').textContent = `${lead.name}${lead.company ? ' at ' + lead.company : ''} will be removed permanently.`;
    $('#delDlg').returnValue = '';
    $('#delDlg').showModal();
  }
});

$('#tbody').addEventListener('change', async (e) => {
  if (!e.target.matches('select.pill')) return;
  try {
    await api(`/api/leads/${e.target.closest('tr').dataset.id}`, { method: 'PUT', body: JSON.stringify({ status: e.target.value }) });
    toast('Status saved');
    await refresh();
  } catch (err) { toast(err.message, true); refresh(); }
});

// clicking a stage in the bar or legend filters the table
document.addEventListener('click', (e) => {
  const b = e.target.closest('.seg, .legend button');
  if (!b) return;
  $('#statusFilter').value = b.dataset.status;
  renderTable();
  $('.leads').scrollIntoView({ behavior: 'smooth' });
});

refresh().catch((err) => toast(err.message, true));
