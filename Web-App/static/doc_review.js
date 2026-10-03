// Review input documents (INFO\DOC-REVIEW-REPROCESS-PLAN.md): the document list (doc_review.html)
// and the feedback / re-process boxes (doc_review_show.html). Uses api(), run(), pollJob(), esc()
// and showAlert() from app.js.

const params = new URLSearchParams(location.search);

// ------------------------------------------------------------------ the list
if (document.getElementById('rows')) {
  const live = document.getElementById('bk-live');
  const sandbox = document.getElementById('bk-sandbox');
  const which = document.getElementById('bk-which');
  const books = (params.get('books') || '').split(',');
  live.checked = books.includes('live');
  sandbox.checked = books.includes('sandbox');
  if (params.get('sandbox')) which.value = params.get('sandbox');
  const selected = new Set(params.getAll('d'));
  let docs = [];

  const statusClass = { posted: 'success', support: 'info', rejected: 'secondary', needs_review: 'danger',
                        proposed: 'warning', extracted: 'light' };

  function query() {
    const b = [live.checked && 'live', sandbox.checked && 'sandbox'].filter(Boolean);
    const q = new URLSearchParams();
    if (b.length) q.set('books', b.join(','));
    if (sandbox.checked && which.value !== 'all') q.set('sandbox', which.value);
    return q;
  }

  function fillFilter(id, values) {
    const sel = document.getElementById(id);
    const keep = sel.value;
    sel.length = 1;
    [...new Set(values)].filter(Boolean).sort().forEach(v => sel.add(new Option(v, v)));
    sel.value = [...sel.options].some(o => o.value === keep) ? keep : '';
  }

  function visible() {
    const q = document.getElementById('f-q').value.trim().toLowerCase();
    const t = document.getElementById('f-type').value, s = document.getElementById('f-status').value;
    const m = document.getElementById('f-month').value;
    return docs.filter(d => (!q || (d.title + ' ' + d.filename).toLowerCase().includes(q))
      && (!t || d.type_label === t) && (!s || d.status_label === s) && (!m || d.month === m));
  }

  function render() {
    const rows = visible();
    document.getElementById('rows').innerHTML = rows.map(d => `<tr>
      <td><input class="form-check-input pick" type="checkbox" data-key="${esc(d.key)}" ${selected.has(d.key) ? 'checked' : ''}></td>
      <td>${d.books_kind === 'sandbox' ? `<span class="badge" style="background:#e8710a">${esc(d.books)}</span>` : '<span class="badge text-bg-primary">live</span>'}</td>
      <td>${esc(d.title)}</td><td class="small">${esc(d.type_label)}</td><td class="small text-nowrap">${esc(d.date)}</td>
      <td class="num small">${esc(d.total ?? '')}</td>
      <td><span class="badge text-bg-${statusClass[d.status] || 'light'} border">${esc(d.status_label)}</span></td>
      <td class="small text-truncate" style="max-width:22em" title="${esc(d.filename)}">${esc(d.filename)}${d.pages.length > 1 ? ` (p.${d.pages.join(',')})` : ''}</td></tr>`).join('')
      || '<tr><td colspan="8" class="text-body-secondary small">No documents.</td></tr>';
    document.getElementById('shown').textContent = rows.length;
    const all = document.getElementById('sel-all');
    all.checked = rows.length > 0 && rows.every(d => selected.has(d.key));
    counter();
  }

  function counter() {
    // Only documents still listed count (the books may have been unticked).
    const keys = new Set(docs.map(d => d.key));
    [...selected].forEach(k => { if (!keys.has(k)) selected.delete(k); });
    document.getElementById('sel-count').textContent = selected.size;
    document.querySelectorAll('.rev-btn').forEach(b => b.disabled = !selected.size);
  }

  async function load() {
    const q = query();
    history.replaceState(null, '', '/doc-review' + (q.toString() ? '?' + q : ''));
    which.disabled = !sandbox.checked;
    const any = live.checked || sandbox.checked;
    document.getElementById('empty').classList.toggle('d-none', any);
    document.getElementById('list').classList.toggle('d-none', !any);
    if (!any) { docs = []; counter(); return; }
    document.getElementById('loading').classList.remove('d-none');
    try {
      docs = (await api('/api/doc-review/documents?' + q, undefined, 'GET')).documents;
    } catch (e) { showAlert(e.message); docs = []; }
    document.getElementById('loading').classList.add('d-none');
    fillFilter('f-type', docs.map(d => d.type_label));
    fillFilter('f-status', docs.map(d => d.status_label));
    fillFilter('f-month', docs.map(d => d.month));
    render();
  }

  [live, sandbox, which].forEach(el => el.addEventListener('change', load));
  ['f-q', 'f-type', 'f-status', 'f-month'].forEach(id => document.getElementById(id).addEventListener('input', render));
  document.getElementById('rows').addEventListener('change', e => {
    if (!e.target.classList.contains('pick')) return;
    e.target.checked ? selected.add(e.target.dataset.key) : selected.delete(e.target.dataset.key);
    render();
  });
  document.getElementById('sel-all').addEventListener('change', e => {
    visible().forEach(d => e.target.checked ? selected.add(d.key) : selected.delete(d.key));
    render();
  });
  document.querySelectorAll('.rev-btn').forEach(b => b.addEventListener('click', () => {
    // Keep the table's order.
    const keys = docs.map(d => d.key).filter(k => selected.has(k));
    if (keys.length > MAX_DOCS && !confirm(`You selected ${keys.length} documents; only the first ${MAX_DOCS} are shown at a time. Continue?`)) return;
    const q = query();
    keys.forEach(k => q.append('d', k));
    location.href = '/doc-review/show?' + q;
  }));
  load();
}

// ------------------------------------------------------------------ the boxes: feedback and re-processing
async function switchAndOpen(books, docId) {
  await run('/api/business/switch', { name: books }, null, false);
  location.href = `/review/${docId}`;
}

document.querySelectorAll('.docbox').forEach(box => {
  const books = box.dataset.books, doc = box.dataset.doc;
  const base = `/api/doc-review/${encodeURIComponent(books)}/${doc}`;
  const $ = sel => box.querySelector(sel);
  const action = () => (box.querySelector('.fb-act:checked') || {}).value || 'none';

  async function showImpact() {
    const a = action();
    $('.fb-json').classList.toggle('d-none', a !== 'corrected');
    if ($('.fb-live-row')) $('.fb-live-row').classList.toggle('d-none', a === 'none');
    const out = $('.fb-impact');
    if (a === 'none') { out.innerHTML = ''; $('.fb-together-row').classList.add('d-none'); return; }
    const together = $('.fb-together').checked;
    try {
      const imp = (await api(`${base}/impact?action=${a}&together=${together ? 1 : 0}`, undefined, 'GET')).impact;
      const docs = imp.documents.map(d => `#${d.id} ${esc(d.doc_type)} (${esc(d.status)})`).join(', ');
      let html = `<div>Re-queues: ${docs}${together && imp.dependants.length ? ', ' + imp.dependants.map(d => `#${d.id}`).join(', ') : ''}.</div>`;
      html += imp.entries.length ? `<div>Reverses ${imp.entries.length} posted entr${imp.entries.length > 1 ? 'ies' : 'y'}: ${imp.entries.map(e => `#${e.id} ${esc(e.date)}`).join(', ')}.</div>`
                                 : '<div>Nothing is posted, so nothing is reversed.</div>';
      if (imp.blocked) html += `<div class="text-danger">✗ ${esc(imp.blocked)}</div>`;
      out.innerHTML = `<div class="border rounded p-2 bg-body">${html}</div>`;
      $('.fb-together-row').classList.toggle('d-none', !imp.dependants.length);
    } catch (e) { out.innerHTML = `<div class="text-danger">${esc(e.message)}</div>`; }
  }

  box.querySelectorAll('.fb-act').forEach(r => r.addEventListener('change', showImpact));
  $('.fb-together').addEventListener('change', showImpact);

  $('.fb-save').addEventListener('click', async () => {
    const a = action();
    const data = {
      categories: [...box.querySelectorAll('.fb-cat:checked')].map(c => c.value),
      text: $('.fb-text').value, action: a, together: $('.fb-together').checked,
      higher_dpi: !!($('.fb-dpi') && $('.fb-dpi').checked), live_claude: !!($('.fb-live') && $('.fb-live').checked),
    };
    if (a === 'corrected') data.corrected_extraction = $('.fb-json').value;
    if (a !== 'none' && !confirm('Save the feedback and re-process this document? A posted document is un-posted (its entries reversed) and goes back to the Review queue.')) return;
    const btn = $('.fb-save');
    btn.disabled = true;
    try {
      const out = await api(`${base}/feedback`, data);
      $('.fb-result').innerHTML = `<span class="text-success">Saved <span class="mono">feedback\\${esc(out.file)}</span></span>`;
      if (out.blocked) $('.fb-result').innerHTML += `<div class="text-danger">Not re-processed: ${esc(out.blocked)}</div>`;
      if (out.job) await pollJob(out.job, 'Re-processed: the new proposal is waiting on the Review screen', true);
      else btn.disabled = false;
    } catch (e) { showAlert(e.message); btn.disabled = false; }
  });
});
