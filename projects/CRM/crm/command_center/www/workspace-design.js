/* Shared presentation of CRM facts. This never changes task or installation state. */
window.WorkspaceDesign = (() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icons = {info:'→', warning:'Ⅱ', danger:'!', success:'✓', neutral:'−'};
  function badge(label, tone = 'neutral') {
    if (!Object.hasOwn(icons, tone)) tone = 'neutral';
    return `<span class="workspace-status" data-tone="${tone}"><span aria-hidden="true">${icons[tone]}</span>${escape(label)}</span>`;
  }
  const queue = t => t.followup_state || t.queue || (t.title?.startsWith('Pending review: ') ? 'parked' : t.needs_decision ? 'decisions' : t.work_type === 'waiting_on_client' ? 'client' : (t.blocked_reason || t.context?.blocked_reason) ? 'blocked' : 'next');
  function task(t, now) {
    if (t.status === 'completed') return badge('Completed', 'success');
    if (t.status !== 'open') return badge(t.status === 'cancelled' ? 'Cancelled' : t.status);
    const state = queue(t);
    if (t.review_at && Date.parse(t.review_at) <= Date.parse(now || new Date().toISOString())) return badge('Review due', 'warning');
    const waiting = {parked:'Parked for later', decisions:'Decision needed', client:'Waiting on client', blocked:'Blocked'};
    if (waiting[state]) return badge(state==='client'&&t.waiting_on?'Waiting on '+t.waiting_on:waiting[state], state === 'blocked' ? 'danger' : 'warning');
    return Date.parse(t.due_at) < Date.parse(now || new Date().toISOString()) ? badge('Overdue', 'danger') : badge('Open', 'info');
  }
  function installation(record) {
    if (/publishing off/i.test(record?.evidence || '')) return badge('Publishing off', 'warning');
    return record?.state === 'built' ? badge('Recorded installed', 'success') : record?.state === 'in_progress' ? badge('In progress', 'info') : badge(record?.state === 'recommended' ? 'Proposed build' : 'Possible build');
  }
  return {badge, task, installation, queue};
})();
