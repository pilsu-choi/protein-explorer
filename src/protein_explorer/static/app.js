(() => {
  const $ = (selector) => document.querySelector(selector);
  const input = $('#sequence-input');
  const predictButton = $('#predict-button');
  const searchButton = $('#search-button');
  const consent = $('#msa-consent');
  const compareButton = $('#compare-button');
  const allowed = new Set('ACDEFGHIKLMNPQRSTVWY');
  const state = { sequence: '', health: null, jobs: [], activeJob: null, search: [], selectedReference: null, viewers: {}, pollTimer: null, toastTimer: null, predicting: false, searching: false };

  function api(path, options) {
    return fetch(path, options).then(async (response) => {
      if (!response.ok) {
        let detail = `${response.status} ${response.statusText}`;
        try { const body = await response.json(); detail = body.detail || body.error || detail; } catch (_) { /* response may be plain text */ }
        throw new Error(detail);
      }
      return response;
    });
  }

  function normalizeSequence(value) {
    const lines = value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    const fasta = lines.some((line) => line.startsWith('>'));
    if (fasta && lines.filter((line) => line.startsWith('>')).length !== 1) return { error: 'Please enter one FASTA record at a time.' };
    const sequenceLines = fasta ? lines.filter((line) => !line.startsWith('>')) : lines;
    if (sequenceLines.some((line) => line.startsWith('>'))) return { error: 'FASTA headers must appear on their own line.' };
    const sequence = sequenceLines.join('').replace(/\s/g, '').toUpperCase();
    if (!sequence) return { sequence: '', error: '' };
    const bad = [...sequence].find((letter) => !allowed.has(letter));
    if (bad) return { sequence, error: `“${bad}” is not a standard amino acid letter.` };
    if (sequence.length < 30 || sequence.length > 300) return { sequence, error: 'Sequence length must be between 30 and 300 residues.' };
    return { sequence, error: '' };
  }

  function updateSequence() {
    const result = normalizeSequence(input.value);
    state.sequence = result.sequence || '';
    $('#residue-count').textContent = `${state.sequence.length} ${state.sequence.length === 1 ? 'residue' : 'residues'}`;
    const feedback = $('#sequence-feedback');
    feedback.textContent = result.error || '';
    feedback.classList.toggle('visible', Boolean(result.error));
    predictButton.disabled = state.predicting || Boolean(result.error) || !state.sequence || !consent.checked || !engineAvailable();
    searchButton.disabled = state.searching || Boolean(result.error) || !state.sequence || !corpusReady();
    if (state.activeJob && state.activeJob.status === 'succeeded') {
      setJobStatus(state.sequence === state.activeJob.sequence ? 'Prediction complete' : 'Previous prediction · input changed', 'good');
    }
    return result;
  }

  function engineAvailable() { return Boolean(state.health && state.health.engine && state.health.engine.available); }
  function corpusReady() { return Boolean(state.health && state.health.corpus && state.health.corpus.ready); }

  function showToast(message, kind = '') {
    const toast = $('#toast');
    toast.textContent = message;
    toast.className = `toast visible ${kind}`;
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => { toast.className = 'toast'; }, 4200);
  }

  function setJobStatus(label, kind = 'muted') {
    $('#job-status').textContent = label;
    const dot = $('#job-status-dot');
    dot.className = `status-dot ${kind}`;
  }

  function setEngine(available, version) {
    $('#engine-dot').className = `status-dot ${available ? 'good' : 'muted'}`;
    $('#engine-label').textContent = available ? `Engine ready${version ? ` · ${version}` : ''}` : 'Engine unavailable';
  }

  function setCorpus(corpus) {
    const count = Number(corpus && corpus.count);
    $('#corpus-count').textContent = Number.isFinite(count) ? count.toLocaleString() : '—';
    $('#corpus-model').textContent = corpus && corpus.ready ? (corpus.model || 'Local sequence index ready') : 'Local sequence index unavailable';
  }

  async function loadHealth() {
    try {
      const response = await api('/api/health');
      state.health = await response.json();
      setEngine(engineAvailable(), state.health.engine.version);
      setCorpus(state.health.corpus || {});
      updateSequence();
      if (!engineAvailable()) setJobStatus('Prediction engine unavailable', 'bad');
      else if (!corpusReady()) $('#search-state').textContent = 'The local search corpus is not ready yet.';
    } catch (error) {
      $('#engine-dot').className = 'status-dot bad';
      $('#engine-label').textContent = 'API unavailable';
      $('#corpus-count').textContent = '—';
      $('#corpus-model').textContent = 'Could not connect to the local API';
      $('#search-state').textContent = 'Search is unavailable until the local API connects.';
      showToast(`Could not connect to Protein Explorer: ${error.message}`, 'error');
    }
  }

  function jobArray(body) { return Array.isArray(body) ? body : Array.isArray(body.jobs) ? body.jobs : []; }

  async function loadJobs() {
    try {
      const response = await api('/api/jobs');
      state.jobs = jobArray(await response.json());
      const savedId = localStorage.getItem('protein-explorer-active-job');
      const saved = state.jobs.find((job) => job.id === savedId);
      const candidate = saved || state.jobs.find((job) => job.status === 'running' || job.status === 'queued') || state.jobs.find((job) => job.status === 'succeeded') || state.jobs[0];
      updateJobPicker();
      if (candidate) await selectJob(candidate.id);
      else setJobStatus('Waiting for sequence');
    } catch (error) {
      setJobStatus('Could not load recent runs', 'bad');
    }
  }

  function updateJobPicker() {
    const button = $('#job-switch');
    button.hidden = state.jobs.length < 2;
    const old = $('#job-menu');
    if (old) old.remove();
  }

  function toggleJobMenu() {
    const old = $('#job-menu');
    if (old) { old.remove(); return; }
    const menu = document.createElement('div');
    menu.id = 'job-menu'; menu.className = 'job-menu'; menu.setAttribute('role', 'listbox'); menu.setAttribute('aria-label', 'Recent prediction runs');
    state.jobs.slice(0, 8).forEach((job) => {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'job-menu-item'; button.setAttribute('role', 'option');
      button.setAttribute('aria-selected', String(state.activeJob && state.activeJob.id === job.id));
      const title = document.createElement('span'); title.textContent = `${(job.sequence || '').slice(0, 16)}${(job.sequence || '').length > 16 ? '…' : ''}`;
      const status = document.createElement('small'); status.textContent = job.status || 'unknown';
      button.append(title, status); button.addEventListener('click', () => { menu.remove(); selectJob(job.id); }); menu.append(button);
    });
    $('#job-switch').parentElement.append(menu);
  }

  async function selectJob(id) {
    try {
      const response = await api(`/api/jobs/${encodeURIComponent(id)}`);
      const job = await response.json();
      state.activeJob = job;
      localStorage.setItem('protein-explorer-active-job', job.id);
      if (job.sequence) { input.value = job.sequence; updateSequence(); }
      renderJob(job);
      if (job.status === 'queued' || job.status === 'running') beginPolling(job.id);
      else clearInterval(state.pollTimer);
      updateJobPicker();
      return job;
    } catch (error) {
      setJobStatus(`Could not load run: ${error.message}`, 'bad');
    }
  }

  function renderJob(job) {
    const status = String(job.status || 'unknown').toLowerCase();
    const labels = { queued: 'Queued · waiting to start', running: 'Prediction in progress', succeeded: 'Prediction complete', failed: 'Prediction failed' };
    setJobStatus(labels[status] || `Prediction ${status}`, status === 'succeeded' ? 'good' : status === 'failed' ? 'bad' : status === 'running' || status === 'queued' ? 'working' : 'muted');
    const summary = job.manifest && job.manifest.plddt;
    const summaryNode = $('#plddt-summary');
    if (summary && Number.isFinite(Number(summary.mean))) {
      summaryNode.hidden = false;
      summaryNode.textContent = `Mean pLDDT ${Number(summary.mean).toFixed(1)}`;
    } else summaryNode.hidden = true;
    $('#download-pdb').disabled = status !== 'succeeded';
    $('#download-report').disabled = status !== 'succeeded';
    if (status === 'succeeded') {
      $('#view-log').hidden = true;
      $('#prediction-placeholder').hidden = true;
      $('#prediction-legend').hidden = false;
      loadStructure(job).catch((error) => showToast(`Could not load predicted structure: ${error.message}`, 'error'));
    } else {
      $('#view-log').hidden = status !== 'failed';
      clearViewer('prediction');
      $('#prediction-placeholder').hidden = false;
      $('#prediction-legend').hidden = true;
      $('#prediction-placeholder').querySelector('.placeholder-title').textContent = status === 'failed' ? (job.error || 'Prediction could not be completed') : 'Your structure will appear here';
      $('#prediction-placeholder').querySelector('.placeholder-caption').textContent = status === 'failed' ? 'Review the run log for details' : 'Submit a sequence to begin prediction';
    }
  }

  function beginPolling(id) {
    clearInterval(state.pollTimer);
    state.pollTimer = setInterval(async () => {
      if (!state.activeJob || state.activeJob.id !== id) return;
      try {
        const response = await api(`/api/jobs/${encodeURIComponent(id)}`);
        const job = await response.json(); state.activeJob = job; renderJob(job);
        if (job.status !== 'running' && job.status !== 'queued') {
          clearInterval(state.pollTimer); await refreshJobsList();
          if (job.status === 'succeeded') showToast('Structure prediction is ready.');
          else loadJobLog(id);
        }
      } catch (_) { /* keep polling through a temporary local API error */ }
    }, 2500);
  }

  async function refreshJobsList() {
    try { const response = await api('/api/jobs'); state.jobs = jobArray(await response.json()); updateJobPicker(); }
    catch (_) { /* active job remains available */ }
  }

  async function loadJobLog(id) {
    try {
      const response = await api(`/api/jobs/${encodeURIComponent(id)}/log`);
      const body = await response.json();
      if (body.log) showToast(`Run log: ${body.log.slice(-240)}`, 'error');
    } catch (_) { showToast('Prediction failed. The run log is unavailable.', 'error'); }
  }

  function makeViewer(which) {
    if (state.viewers[which]) return state.viewers[which];
    const el = which === 'prediction' ? $('#prediction-viewer') : $('#reference-viewer');
    const library = window.$3Dmol || window['3Dmol'];
    if (!library || !library.createViewer) return null;
    state.viewers[which] = library.createViewer(el, { backgroundColor: '#e8eeea', antialias: true, cartoonQuality: 10 });
    return state.viewers[which];
  }

  function clearViewer(which) {
    const viewer = state.viewers[which];
    if (!viewer) return;
    viewer.clear(); viewer.render();
  }

  function plddtColor(atom) {
    const value = Number(atom.b || 0);
    if (value < 50) return '#e98170';
    if (value < 70) return '#efc26a';
    if (value < 90) return '#a5c779';
    return '#248b83';
  }

  function drawStructure(which, pdb, confidence) {
    const viewer = makeViewer(which);
    if (!viewer) {
      showToast('The 3D viewer library is unavailable. Refresh the page to try again.', 'error'); return false;
    }
    viewer.clear();
    viewer.addModel(pdb, 'pdb');
    if (confidence) viewer.setStyle({}, { cartoon: { colorfunc: plddtColor } });
    else viewer.setStyle({}, { cartoon: { color: '#248b83' } });
    viewer.zoomTo(); viewer.render();
    if (which === 'prediction') $('#prediction-placeholder').hidden = true;
    else $('#reference-placeholder').hidden = true;
    return true;
  }

  async function loadStructure(job) {
    if (state.renderingJobId === job.id) return;
    state.renderingJobId = job.id;
    try {
      const response = await api(`/api/jobs/${encodeURIComponent(job.id)}/structure`);
      const pdb = await response.text();
      if (!state.activeJob || state.activeJob.id !== job.id) return;
      drawStructure('prediction', pdb, true);
    } finally { if (state.renderingJobId === job.id) state.renderingJobId = null; }
  }

  async function loadReference(result) {
    state.selectedReference = result;
    $('.experimental-badge').textContent = String(result.structure_type || 'experimental').toUpperCase();
    const info = $('#reference-info');
    info.replaceChildren();
    const dot = document.createElement('span'); dot.className = 'reference-dot';
    const details = document.createElement('span');
    details.textContent = `${result.name || result.id} · ${result.organism || 'Organism unknown'} · cosine ${formatScore(result.score)}`;
    info.append(dot, details);
    try {
      const source = new URL(result.structure_url, window.location.href);
      if (source.protocol === 'https:') {
        const link = document.createElement('a');
        link.href = source.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
        link.textContent = 'Source ↗'; link.setAttribute('aria-label', 'Open experimental structure source');
        info.append(link);
      }
    } catch (_) { /* omit malformed source links */ }
    $('#reference-placeholder').hidden = false;
    $('#reference-placeholder').querySelector('.placeholder-title').textContent = 'Loading experimental structure…';
    $('#reference-placeholder').querySelector('.placeholder-caption').textContent = result.id || '';
    clearViewer('reference');
    try {
      const response = await api(`/api/proteins/${encodeURIComponent(result.id)}/structure`);
      const pdb = await response.text();
      if (state.selectedReference !== result) return;
      drawStructure('reference', pdb, false);
    } catch (error) {
      $('#reference-placeholder').hidden = false;
      $('#reference-placeholder').querySelector('.placeholder-title').textContent = 'Structure unavailable';
      $('#reference-placeholder').querySelector('.placeholder-caption').textContent = error.message;
      showToast(`Could not load reference structure: ${error.message}`, 'error');
    }
  }

  function formatScore(score) {
    const number = Number(score);
    return Number.isFinite(number) ? number.toFixed(3) : '—';
  }

  function renderResults(results) {
    const host = $('#search-results'); host.replaceChildren();
    state.search = results;
    if (!results.length) { $('#search-state').textContent = 'No similar proteins were found for this sequence.'; return; }
    $('#search-state').textContent = `${results.length} ${results.length === 1 ? 'reference' : 'references'} ranked by sequence cosine similarity`;
    results.forEach((result, index) => {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'result-row'; button.dataset.testid = 'search-result';
      button.setAttribute('aria-label', `View ${result.name || result.id}, cosine similarity ${formatScore(result.score)}`);
      const rank = document.createElement('span'); rank.className = 'result-rank'; rank.textContent = String(index + 1).padStart(2, '0');
      const body = document.createElement('span'); body.className = 'result-copy';
      const name = document.createElement('strong'); name.textContent = result.name || result.id || 'Unnamed protein';
      const details = document.createElement('small'); details.textContent = `${result.id || 'ID unknown'} · ${result.organism || 'Organism unknown'} · ${Number(result.length) || result.sequence && result.sequence.length || '?'} aa`;
      body.append(name, details);
      const score = document.createElement('span'); score.className = 'result-score'; score.textContent = formatScore(result.score);
      const scoreLabel = document.createElement('small'); scoreLabel.textContent = 'COSINE'; score.append(scoreLabel);
      const arrow = document.createElement('span'); arrow.className = 'result-arrow'; arrow.textContent = '↗';
      button.append(rank, body, score, arrow);
      button.addEventListener('click', () => {
        host.querySelectorAll('.result-row').forEach((row) => row.classList.remove('selected'));
        button.classList.add('selected'); loadReference(result);
      });
      host.append(button);
    });
  }

  async function runSearch() {
    const result = updateSequence();
    if (state.searching || result.error || !state.sequence || !corpusReady()) return;
    state.searching = true;
    searchButton.disabled = true; searchButton.classList.add('busy');
    $('#search-state').textContent = 'Searching the local sequence corpus…';
    $('#search-results').replaceChildren();
    try {
      const response = await api('/api/search', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sequence: state.sequence, limit: 10 }) });
      const body = await response.json(); renderResults(Array.isArray(body.results) ? body.results : []);
    } catch (error) {
      $('#search-state').textContent = `Search failed: ${error.message}`;
    } finally { state.searching = false; searchButton.classList.remove('busy'); updateSequence(); }
  }

  async function submitPrediction() {
    const result = updateSequence();
    if (state.predicting || result.error || !state.sequence || !consent.checked || !engineAvailable()) return;
    state.predicting = true;
    predictButton.disabled = true; predictButton.classList.add('busy');
    setJobStatus('Creating prediction run…', 'working');
    $('#prediction-placeholder').hidden = false;
    $('#prediction-placeholder').querySelector('.placeholder-title').textContent = 'Preparing your prediction';
    $('#prediction-placeholder').querySelector('.placeholder-caption').textContent = 'This can take a few minutes';
    clearViewer('prediction');
    try {
      const response = await api('/api/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sequence: state.sequence, settings: { num_models: 1, num_recycle: 3, msa_mode: 'mmseqs2_uniref_env' } }) });
      const job = await response.json();
      if (!job.id) throw new Error('The API did not return a run ID.');
      state.activeJob = job; localStorage.setItem('protein-explorer-active-job', job.id);
      renderJob(job); beginPolling(job.id); await refreshJobsList();
      showToast(job.cache_hit ? 'Loaded a previous prediction for this sequence.' : 'Prediction run started.');
    } catch (error) {
      setJobStatus(`Could not start prediction: ${error.message}`, 'bad');
      $('#prediction-placeholder').querySelector('.placeholder-title').textContent = 'Prediction could not start';
      $('#prediction-placeholder').querySelector('.placeholder-caption').textContent = error.message;
    } finally { state.predicting = false; predictButton.classList.remove('busy'); updateSequence(); }
  }

  function download(kind) {
    if (!state.activeJob) return;
    const anchor = document.createElement('a');
    anchor.href = `/api/jobs/${encodeURIComponent(state.activeJob.id)}/download?kind=${encodeURIComponent(kind)}`;
    anchor.download = ''; document.body.append(anchor); anchor.click(); anchor.remove();
  }

  function setComparisonMode(enabled) {
    document.body.classList.toggle('comparison-mode', enabled);
    compareButton.setAttribute('aria-pressed', String(enabled));
    compareButton.setAttribute('aria-label', enabled ? 'Disable comparison layout' : 'Enable comparison layout');
    requestAnimationFrame(() => Object.values(state.viewers).forEach((viewer) => {
      if (viewer) { viewer.resize(); viewer.render(); }
    }));
  }

  async function loadSample() {
    if (!state.health || !state.health.sample || !state.health.sample.sequence) {
      showToast('The 1UBQ sample is unavailable from the local API.', 'error'); return;
    }
    const sequence = String(state.health.sample.sequence).replace(/^>[^\n]*\n/, '').replace(/\s/g, '');
    input.value = `>1UBQ · ubiquitin · 76 residues\n${sequence}`;
    updateSequence();
    showToast('Loaded the official 1UBQ reference sequence.');
  }

  input.addEventListener('input', updateSequence);
  consent.addEventListener('change', updateSequence);
  $('#sample-button').addEventListener('click', loadSample);
  predictButton.addEventListener('click', submitPrediction);
  searchButton.addEventListener('click', runSearch);
  $('#refresh-button').addEventListener('click', async () => { await Promise.all([loadHealth(), loadJobs()]); showToast('Workspace refreshed.'); });
  $('#job-switch').addEventListener('click', toggleJobMenu);
  $('#download-pdb').addEventListener('click', () => download('pdb'));
  $('#download-report').addEventListener('click', () => download('bundle'));
  $('#view-log').addEventListener('click', () => { if (state.activeJob) loadJobLog(state.activeJob.id); });
  compareButton.addEventListener('click', () => setComparisonMode(compareButton.getAttribute('aria-pressed') !== 'true'));
  window.addEventListener('resize', () => Object.values(state.viewers).forEach((viewer) => { if (viewer) viewer.resize(); }));

  loadHealth().then(() => loadJobs());
})();
