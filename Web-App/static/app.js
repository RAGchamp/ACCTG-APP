// Shared browser helpers: JSON calls, background-job polling (the analyzer's pattern), alerts.

async function api(url, data, method) {
  const opts = { method: method || 'POST', headers: {} };
  if (data instanceof FormData) { opts.body = data; }
  else if (data !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(data); }
  const res = await fetch(url, opts);
  let out;
  try { out = await res.json(); } catch (e) { out = { ok: false, error: `${res.status} ${res.statusText}` }; }
  if (!out.ok) throw new Error(out.error || 'Request failed');
  return out;
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function showAlert(msg, kind) {
  const box = document.getElementById('alerts');
  const div = document.createElement('div');
  div.className = `alert alert-${kind || 'danger'} alert-dismissible fade show`;
  div.innerHTML = `${esc(msg)}<button type="button" class="btn-close" data-bs-dismiss="alert"></button>`;
  box.prepend(div);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function run(url, data, okMsg, reload) {
  try {
    const out = await api(url, data);
    if (out.job) return pollJob(out.job, okMsg, reload !== false);
    if (okMsg) showAlert(okMsg, 'success');
    if (reload !== false) setTimeout(() => location.reload(), okMsg ? 500 : 0);
    return out;
  } catch (e) { showAlert(e.message); }
}

function pollJob(id, okMsg, reload) {
  const box = document.getElementById('job-box');
  const card = document.createElement('div');
  card.className = 'card shadow mb-2';
  card.innerHTML = `<div class="card-body py-2 small"><div class="d-flex justify-content-between"><b>Working…</b>
    <a href="#" class="small">cancel</a></div><div class="progress my-1" style="height:6px"><div class="progress-bar progress-bar-striped progress-bar-animated" style="width:100%"></div></div>
    <div class="text-body-secondary job-text">Starting</div></div>`;
  card.querySelector('a').onclick = (e) => { e.preventDefault(); api(`/api/jobs/${id}/cancel`, {}); };
  box.appendChild(card);
  return new Promise(resolve => {
    const tick = async () => {
      let job;
      try { job = (await api(`/api/jobs/${id}`, undefined, 'GET')).job; } catch (e) { setTimeout(tick, 2000); return; }
      const p = job.progress || {};
      const bar = card.querySelector('.progress-bar');
      if (p.total) { bar.classList.remove('progress-bar-animated'); bar.style.width = `${Math.round(100 * p.done / p.total)}%`; }
      card.querySelector('.job-text').textContent = p.text || job.kind;
      if (job.status === 'running') { setTimeout(tick, 1500); return; }
      card.remove();
      if (job.status === 'error') { showAlert(job.error); resolve(job); return; }
      const errs = (job.result && job.result.errors) || [];
      if (errs.length) showAlert(errs.join(' · '), 'warning');
      else if (okMsg) showAlert(okMsg, 'success');
      resolve(job);
      if (reload) setTimeout(() => location.reload(), errs.length ? 2500 : 600);
    };
    tick();
  });
}

async function switchBusiness(name) {
  await run('/api/business/switch', { name }, null, false);
  location.href = '/';
}
