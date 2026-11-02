/* Small, distinct symbols for installed systems. No decorative game buildings. */
window.StrategyArt = (() => {
  const paths = {
    website:'M3 4h18v16H3z M3 9h18 M7 6.5h.01 M10 6.5h.01 M8 13l-2 2 2 2 M16 13l2 2-2 2',
    brain:'M12 5c-2-4-7-2-7 2-4 1-3 7 0 8-1 4 5 6 7 2 2 4 8 2 7-2 3-1 4-7 0-8 0-4-5-6-7-2z M12 5v12 M5 7l3 2 M19 7l-3 2 M5 15l3-2 M19 15l-3-2',
    crm:'M4 4h16v16H4z M4 9h16 M9 9v11 M13 13h4 M13 16h4',
    factory:'M3 20V10l6 3V8l6 4V4h4l2 16z M7 16h1 M12 16h1 M17 16h1',
    calendar:'M4 5h16v16H4z M8 3v5 M16 3v5 M4 10h16 M8 14h2 M14 14h2 M8 18h2',
    chat:'M4 4h16v13H9l-5 4z M8 8h8 M8 12h5',
    phone:'M7 3l3 5-3 3c2 3 3 4 6 6l3-3 5 3c-1 6-7 4-12-1S2 4 7 3z',
    chart:'M4 3v17h17 M8 16v-5 M13 16V7 M18 16v-9',
    search:'M16 16l5 5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
    shop:'M4 10v10h16V10 M2 10l3-7h14l3 7 M2 10h20 M9 20v-6h6v6',
    link:'M10 7l2-2a5 5 0 0 1 7 7l-2 2 M14 17l-2 2a5 5 0 0 1-7-7l2-2 M8 16l8-8',
    file:'M5 3h9l5 5v13H5z M14 3v6h5 M8 13h8 M8 17h6',
    spark:'M12 2l3 7 7 3-7 3-3 7-3-7-7-3 7-3z',
    check:'M5 12l4 4L20 5',
    layers:'M12 3l10 6-10 6L2 9z M2 13l10 6 10-6 M2 17l10 6 10-6',
  };
  const types = {'second-brain':'brain','brain-starter':'brain','knowledge-intake':'brain','content':'factory','content-engine':'factory','blog-engine':'factory','booking-calendar':'calendar','ai-answering':'phone','ai-receptionist':'phone','online-ordering':'shop','google-analytics':'chart','dashboard':'chart','command-center':'chart','deal-finder':'search','research-worker':'search','website-scan':'search','lead-capture':'link','follow-up':'link','connected-workflows':'link','operations-observability':'chart','content-feedback':'factory','google-profile':'shop','website-chat':'chat','crm':'crm'};
  function icon(key, extra='') { const path=paths[types[key]||key]||paths.file;return `<svg class="st-icon ${extra}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${path}"/></svg>`; }
  return {icon};
})();
