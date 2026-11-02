/* Export to Excel and Import contacts (crm_io.py). Administrators only: the buttons show when the
   snapshot carries `data_io`, and the server checks again. The import writes nothing before its last step. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const plural = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;
  let imp = null;
  const limits = {max_rows: 5000, max_mb: 5};  // crm_io.MAX_ROWS / MAX_BYTES; the server enforces them

  async function post(path, body) {
    const r = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    if (!r.ok) { let msg = 'That did not work. Try again.'; try { msg = (await r.json()).error || msg; } catch (e) {} throw Error(msg); }
    return r;
  }
  const send = async body => (await post('api/crm/import', body)).json();
  const readB64 = file => new Promise((ok, bad) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(',')[1] || ''); r.onerror = () => bad(Error('The file could not be read.')); r.readAsDataURL(file); });
  const rowList = items => items.length ? `<ul class="io-rows">${items.slice(0, 50).map(x => `<li>Row ${x.row}: ${esc(x.name ? x.name + ' · ' : '')}${esc(x.reason)}</li>`).join('')}${items.length > 50 ? `<li>and ${items.length - 50} more</li>` : ''}</ul>` : '';
  const note = text => { const n = $('io-note'); if (n) n.textContent = text; };

  function open(title, html) {
    $('viewer-title').textContent = title;
    $('viewer-body').innerHTML = html + '<p id="io-note" role="status" aria-live="polite"></p>';
    $('viewer').classList.add('io-wide');
    $('viewer').addEventListener('close', () => { $('viewer').classList.remove('io-wide'); imp = null; }, {once: true});
    if (!$('viewer').open) $('viewer').showModal();
  }

  async function exportAll(b) {
    b.disabled = true;
    $('notice').textContent = 'Preparing the export…';
    try {
      const r = await post('api/crm/export', {});
      const name = (r.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/)?.[1] || 'crm-export.zip';
      const a = document.createElement('a'); a.href = URL.createObjectURL(await r.blob()); a.download = name;
      document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 60000);
      $('notice').textContent = `Downloaded ${name}: contacts, deals and open to-dos, one spreadsheet each. Open the ZIP, then open a file in Excel.`;
    } catch (err) { $('notice').textContent = err.message; }
    finally { b.disabled = false; }
  }

  function importStart() {
    imp = {};
    open('Import contacts', `<p>Choose a CSV file: a save from Excel or Google Sheets, or an export from another CRM. The first row must name the columns. At most ${limits.max_rows.toLocaleString()} rows and ${limits.max_mb} MB.</p>
      <p class="muted">Nothing is saved until the last step. An import only adds contacts: it never changes one that is already here.</p>
      <label for="io-file">CSV file</label><input id="io-file" type="file" accept=".csv,text/csv">
      <div class="actions"><button type="button" class="primary" data-io="read">Read the file</button></div><div id="io-step"></div>`);
  }

  async function importRead() {
    const file = $('io-file').files[0]; if (!file) throw Error('Choose a .csv file first.');
    if (file.size > limits.max_mb * 1e6) throw Error(`The file is ${(file.size / 1e6).toFixed(1)} MB. The limit is ${limits.max_mb} MB: split it into smaller files.`);
    imp = {file: await readB64(file), name: file.name};
    const r = await send({step: 'read', file: imp.file});
    const opts = [{id: 'skip', label: 'Skip this column'}, ...r.targets];
    $('io-step').innerHTML = `<h3>1. Match the columns</h3><p class="muted">${plural(r.rows, 'row')} in ${esc(file.name)}. The matches are guessed from the column names. Check them.</p>
      <div class="io-table"><table class="io-map"><thead><tr><th scope="col">Column in the file</th><th scope="col">Example</th><th scope="col">Goes to</th></tr></thead><tbody>${r.columns.map((col, i) => `<tr><td>${esc(col || '(no name)')}</td><td>${esc(r.sample.map(x => x[i]).find(Boolean) || '')}</td><td><select data-map="${i}" aria-label="Where ${esc(col)} goes">${opts.map(o => `<option value="${esc(o.id)}" ${o.id === r.guess[i] ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}</select></td></tr>`).join('')}</tbody></table></div>
      <h3>2. Where they came from</h3><p class="muted">Set once for the whole file. A column matched to a source field wins for its own row.</p>
      <label for="io-source_channel">Channel</label><input id="io-source_channel" maxlength="120" placeholder="Referral">
      <label for="io-source_campaign">Campaign</label><input id="io-source_campaign" maxlength="120" placeholder="Chamber list, October">
      <label for="io-source_ad">Ad or detail</label><input id="io-source_ad" maxlength="120">
      <label for="io-dups">When a row looks like a contact that is already here (same email, phone or name)</label><select id="io-dups"><option value="skip">Skip it</option><option value="create">Add it anyway, as a separate contact</option></select>
      <div class="actions"><button type="button" class="primary" data-io="preview">Check the file</button></div><div id="io-preview"></div>`;
  }

  const body = step => ({step, file: imp.file, mapping: [...document.querySelectorAll('[data-map]')].map(x => x.value), duplicates: $('io-dups').value,
    source: {source_channel: $('io-source_channel').value.trim(), source_campaign: $('io-source_campaign').value.trim(), source_ad: $('io-source_ad').value.trim()}});

  async function importPreview() {
    const b = body('preview'), r = await send(b);
    imp.save = {...b, step: 'save'};
    $('io-preview').innerHTML = `<h3>3. Check, then import</h3><p><strong>Nothing is saved yet.</strong></p>
      <ul><li><strong>${r.new}</strong> new contact${r.new === 1 ? '' : 's'}</li><li><strong>${r.duplicates.length}</strong> look like a contact already here: ${b.duplicates === 'skip' ? 'they will be skipped' : 'they will be added anyway'}</li><li><strong>${r.errors.length}</strong> row${r.errors.length === 1 ? '' : 's'} with a problem: not imported</li>${r.already ? `<li><strong>${r.already}</strong> already imported from this file</li>` : ''}</ul>
      ${r.new_rows.length ? '<details><summary>New contacts</summary>' + rowList(r.new_rows.map(x => ({...x, reason: 'new'}))) + '</details>' : ''}
      ${r.duplicates.length ? '<h4>Likely duplicates</h4>' + rowList(r.duplicates) : ''}${r.errors.length ? '<h4>Rows with a problem</h4>' + rowList(r.errors) + '<p class="muted">You can download these rows after the import.</p>' : ''}
      <div class="actions">${r.will_create ? `<button type="button" class="primary" data-io="save">Import ${plural(r.will_create, 'contact')}</button>` : '<p class="muted">Nothing to import. Change the matches or the duplicates choice, then check again.</p>'}</div>`;
  }

  async function importSave(b) {
    if (!imp?.save) throw Error('Check the file first.');
    b.disabled = true;
    const r = await send(imp.save); imp.save = null;
    const failed = r.failed_csv ? URL.createObjectURL(new Blob(['﻿' + r.failed_csv], {type: 'text/csv'})) : '';
    $('io-step').innerHTML = `<h3>Import finished</h3><ul><li><strong>${r.created}</strong> added</li><li><strong>${r.skipped.length}</strong> skipped</li><li><strong>${r.failed.length}</strong> failed</li></ul>${r.skipped.length ? '<h4>Skipped</h4>' + rowList(r.skipped) : ''}${r.failed.length ? '<h4>Failed</h4>' + rowList(r.failed) + `<p><a href="${failed}" download="${esc(imp.name.replace(/\.csv$/i, ''))}-failed-rows.csv">Download the failed rows</a>. The reason is in the last column. Fix them, then import that file.</p>` : ''}${r.backup ? '<p class="muted">A backup of the CRM was made before the first new contact.</p>' : ''}`;
    $('notice').textContent = `Import finished: ${plural(r.created, 'contact')} added.`;
    window.CRMSync?.refresh?.();
  }

  document.addEventListener('click', async e => {
    const b = e.target.closest('[data-io]'); if (!b) return;
    const step = b.dataset.io;
    try {
      if (step === 'export') return await exportAll(b);
      if (step === 'import') return importStart();
      note(step === 'save' ? 'Importing…' : 'Reading…');
      if (step === 'read') await importRead();
      else if (step === 'preview') await importPreview();
      else if (step === 'save') await importSave(b);
      note('');
    } catch (err) { note(err.message); b.disabled = false; }
  });
  // A change after the check makes the check stale: check again before importing.
  ['input', 'change'].forEach(type => document.addEventListener(type, e => {
    if (imp?.save && e.target.closest('#io-step') && !e.target.closest('#io-preview')) { imp.save = null; $('io-preview').innerHTML = '<p class="muted">Something changed. Check the file again.</p>'; }
    if (e.target.id === 'io-file' && imp) { imp = {}; $('io-step').innerHTML = ''; }
  }));
})();
