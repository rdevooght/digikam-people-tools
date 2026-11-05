/* People — a dependency-free, local digiKam collection explorer. */
(function () {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const A = window.PeopleAnalytics;
  const data = window.DIGIKAM_DATA;
  const number = new Intl.NumberFormat('en');
  const fmt = value => number.format(value);
  const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const normalize = value => String(value).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const percent = (value, digits = 1) => value === null || value === undefined ? '—' : `${(value * 100).toFixed(digits).replace(/\.0$/, '')}%`;
  const colors = [
    ['#ece7fc', '#8a72b5'], ['#e3eff9', '#668caf'], ['#fae9df', '#bd8b6d'],
    ['#e2f0e9', '#6d9b85'], ['#f7e5ed', '#b77d98'], ['#e7eafa', '#7b87bc'],
    ['#f5edda', '#af985e'], ['#e1eff0', '#6b9b9e'], ['#efe6f7', '#a17bb7'],
    ['#e8eddf', '#8b9c6b'], ['#fae6e3', '#bd8580'], ['#e5eaf1', '#7d91aa'],
  ];
  const iconPaths = {
    photos: '<rect x="3" y="3" width="14" height="14" rx="3"/><path d="m3 13 4-4 5 5 2-2 3 3"/><circle cx="12.5" cy="7.5" r="1"/>',
    people: '<circle cx="8" cy="6" r="3"/><path d="M2 17c0-8 12-8 12 0M14 4a3 3 0 0 1 0 6M16 12c2 0 3 2 3 5"/>',
    tag: '<path d="M3 3h6l8 8-6 6-8-8V3Z"/><circle cx="6.5" cy="6.5" r="1"/>',
    calendar: '<rect x="3" y="4" width="14" height="13" rx="2"/><path d="M6 2v4M14 2v4M3 8h14M6 11h2M12 11h2M6 14h2"/>',
    chart: '<path d="M3 3v14h14M6 13V9M10 13V5M14 13V7"/>',
    table: '<rect x="3" y="3" width="14" height="14" rx="2"/><path d="M3 8h14M3 12h14M9 3v14"/>',
    download: '<path d="M10 2v10m-4-4 4 4 4-4M3 13v4h14v-4"/>',
    arrow: '<path d="M3 10h14m-5-5 5 5-5 5"/>',
  };
  const icon = name => `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${iconPaths[name] || ''}</svg>`;
  let index = null;
  let dataError = null;
  try { if (data) index = A.buildIndex(data); } catch (error) { dataError = error.message; }
  const state = { selected: [], grain: 'month', year: 'all', shareMode: 'photos', view: 'chart', search: '', sort: 'count', showAllCompanions: false };
  let currentRows = [];
  let resizeObserver = null;
  let lastChartWidth = 0;

  function restoreHash() {
    if (!index) return;
    const params = new URLSearchParams(location.hash.slice(1));
    state.selected = [...new Set((params.get('people') || '').split(',').map(Number))].filter(id => index.byPerson.has(id));
    state.grain = params.get('grain') === 'year' ? 'year' : 'month';
    state.shareMode = params.get('share') === 'tags' ? 'tags' : 'photos';
    state.view = params.get('view') === 'table' ? 'table' : 'chart';
    const year = params.get('year');
    state.year = year && data.months.some(month => month.startsWith(year + '-')) ? year : 'all';
  }

  function updateHash() {
    const params = new URLSearchParams();
    if (state.selected.length) params.set('people', state.selected.join(','));
    if (state.grain !== 'month') params.set('grain', state.grain);
    if (state.year !== 'all') params.set('year', state.year);
    if (state.shareMode !== 'photos') params.set('share', state.shareMode);
    if (state.view !== 'chart') params.set('view', state.view);
    try { history.replaceState(null, '', location.pathname + location.search + (params.size ? '#' + params.toString() : '')); } catch (_) { /* Some file browsers restrict history. */ }
  }

  function initials(name) {
    const parts = name.trim().split(/\s+/);
    return (parts.length > 1 ? [...parts[0]][0] + [...parts[parts.length - 1]][0] : [...(parts[0] || '?')].slice(0, 2).join('')).toUpperCase();
  }

  function avatar(person, extra = '') {
    const [background, foreground] = colors[Math.abs(person.id) % colors.length];
    return `<span class="avatar ${extra}" style="--avatar-bg:${background};--avatar-ink:${foreground}" aria-hidden="true">${escapeHTML(initials(person.name))}</span>`;
  }

  function monthLabel(key, short = false) {
    if (key.length === 4) return key;
    return new Intl.DateTimeFormat('en', { month: short ? 'short' : 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(key + '-01T00:00:00Z'));
  }

  function announce(message) { $('#announcer').textContent = message; }

  function filteredPeople() {
    const query = normalize(state.search.trim());
    const people = data.people.filter(person => !query || normalize(person.name).includes(query) || normalize(person.path).includes(query));
    return people.sort((a, b) => state.sort === 'name' ? a.name.localeCompare(b.name) : state.sort === 'recent' ? (b.last || '').localeCompare(a.last || '') || b.count - a.count : b.count - a.count || a.name.localeCompare(b.name));
  }

  function renderPeopleList() {
    if (!index) { $('#people-list').innerHTML = '<div class="list-empty">Import your collection to see people here.</div>'; return; }
    const people = filteredPeople();
    $('#people-count').textContent = state.search ? `${people.length}/${data.people.length}` : data.people.length;
    $('#people-list').innerHTML = people.length ? people.map(person => `
      <div class="person-row" data-row-id="${person.id}">
        <input class="person-check" type="checkbox" value="${person.id}" aria-label="Include ${escapeHTML(person.name)} in group">
        <button class="person-open" data-person="${person.id}" title="${escapeHTML(person.path)} · ${fmt(person.count)} tagged photos">
          ${avatar(person)}<span class="person-name">${escapeHTML(person.name)}</span><span class="person-count ${person.count ? '' : 'zero'}">${fmt(person.count)}</span>
        </button>
      </div>`).join('') : `<div class="list-empty"><strong>${data.people.length ? 'No matching people' : 'No people tags yet'}</strong>${data.people.length ? 'Try a different name or clear your search.' : 'Check the People root in your export.'}</div>`;
    $('#select-visible').disabled = !people.length;
    syncPeopleRows();
  }

  function syncPeopleRows() {
    const selected = new Set(state.selected);
    document.querySelectorAll('[data-row-id]').forEach(row => {
      const id = Number(row.dataset.rowId);
      row.classList.toggle('is-selected', selected.has(id));
      row.querySelector('input').checked = selected.has(id);
      row.querySelector('button').setAttribute('aria-pressed', String(selected.has(id)));
    });
    const count = state.selected.length;
    $('#selection-count').textContent = count ? `${count} ${count === 1 ? 'person' : 'people'} selected` : 'No people selected';
    $('#clear-selection').hidden = !count;
    $('#view-group').disabled = !count;
    $('#view-group').textContent = count > 1 ? `View ${count} people together →` : count ? 'View person timeline →' : 'Select people to compare';
  }

  function scrollToDetail() {
    if (window.matchMedia('(max-width: 850px)').matches) $('#main-content').scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
  }

  function setSelection(ids, scroll = false) {
    state.selected = [...new Set(ids)].filter(id => index && index.byPerson.has(id));
    state.showAllCompanions = false;
    syncPeopleRows();
    renderMain();
    updateHash();
    const names = state.selected.map(id => index.peopleById.get(id).name);
    announce(names.length ? `Showing ${names.length > 1 ? 'photos containing all of ' : ''}${names.join(', ')}.` : 'Showing collection overview.');
    if (scroll) scrollToDetail();
  }

  function stat(label, value, note, iconName, className = '') {
    return `<div class="stat-card"><div class="stat-label">${icon(iconName)}${escapeHTML(label)}</div><div class="stat-value ${className}">${escapeHTML(value)}</div><div class="stat-note">${escapeHTML(note)}</div></div>`;
  }

  function emptyState(title, description, action = '') {
    return `<div class="empty-state"><div class="empty-icon" aria-hidden="true">◌</div><h3>${escapeHTML(title)}</h3><p>${escapeHTML(description)}</p>${action}</div>`;
  }

  function renderOverview() {
    const meta = data.meta;
    const top = data.people.filter(person => person.count > 0).slice(0, 8);
    const years = A.overviewTimeline(data, index);
    const maxCount = Math.max(1, ...years.map(row => row.count));
    const firstYear = data.months[0]?.slice(0, 4);
    const lastYear = data.months[data.months.length - 1]?.slice(0, 4);
    const dateSpan = firstYear ? firstYear === lastYear ? firstYear : `${firstYear}–${lastYear}` : 'No dates';
    if (!data.people.length) return `<div class="card setup-card"><div class="eyebrow">EMPTY EXPORT</div><h1>No people tags found.</h1><p>The export contains no people. Check your People tag hierarchy, or try <code>--person-property-only</code> with the correct root. The README explains how to inspect and select tags.</p><button class="primary-button" data-help>View import instructions</button></div>`;
    return `
      <div class="page-heading"><div class="eyebrow">COLLECTION OVERVIEW</div><h1>The people in your pictures.</h1><p>A collection is more than places and dates. Discover the people who fill its frames.</p></div>
      <div class="stats-grid four">
        ${stat('People', fmt(meta.peopleCount), 'Person tags in this export', 'people')}
        ${stat('Tagged photos', fmt(meta.taggedPhotos), `of ${fmt(meta.totalPhotos)} collection photos`, 'photos')}
        ${stat('Person tags', fmt(meta.tagAssignments), 'One per person, per photo', 'tag')}
        ${stat('Photo date span', dateSpan, `${fmt(meta.undatedPhotos)} tagged photos undated`, 'calendar', 'date-value')}
      </div>
      <div class="overview-grid">
        <section class="card"><div class="card-header"><div><h2 class="card-title">Familiar faces</h2><p class="card-subtitle">The most frequently tagged people</p></div><span class="pill">ALL TIME</span></div>
          <div class="top-people">${top.length ? top.map(person => `<button class="top-person" data-open-person="${person.id}">${avatar(person)}<span class="top-person-content"><span class="top-person-line"><span class="top-person-name">${escapeHTML(person.name)}</span><span class="top-person-count">${fmt(person.count)} photos</span></span><span class="top-person-track"><span style="width:${person.count / top[0].count * 100}%"></span></span></span></button>`).join('') : emptyState('No tagged photos', 'Your people tags exist, but none are assigned to photos yet.')}</div>
        </section>
        <section class="card guide-card"><div class="guide-illustration">${top.slice(0, 3).map((person, i) => `${i ? '<span class="guide-plus">+</span>' : ''}${avatar(person)}`).join('')}</div><h2>Some stories<br>are better together.</h2><p>Go beyond a single face. Explore the photos that bring your favorite people together.</p><ol class="guide-steps"><li><span class="step-number">1</span><span>Click a name to explore their timeline.</span></li><li><span class="step-number">2</span><span>Check several people to build a group.</span></li><li><span class="step-number">3</span><span>See shared moments and mutual company.</span></li></ol>${top.length ? `<button class="primary-button" data-open-person="${top[0].id}">Start with ${escapeHTML(top[0].name.split(' ')[0])} ${icon('arrow')}</button>` : ''}</section>
      </div>
      <section class="card"><div class="card-header"><div><h2 class="card-title">A collection over time</h2><p class="card-subtitle">Photos with at least one person tag, by photo date</p></div><span class="pill">YEARLY</span></div><div class="card-body">${years.length ? `<div class="year-chart" role="img" aria-label="Tagged photos by year: ${years.map(row => `${row.key}: ${row.count}`).join(', ')}">${years.map(row => `<div class="year-column" title="${row.key}: ${fmt(row.count)} tagged photos"><span class="year-value">${fmt(row.count)}</span><span class="year-bar" style="height:${Math.max(2, row.count / maxCount * 108)}px"></span><span class="year-label">${row.key}</span></div>`).join('')}</div><p class="overview-note">Choose a person to explore monthly counts, percentages, and the people beside them.</p>` : emptyState('No dated photos', 'Add capture dates in digiKam, then export again to see a timeline.')}</div></section>`;
  }

  function renderDetail() {
    const people = state.selected.map(id => index.peopleById.get(id));
    const group = people.length > 1;
    const summary = A.summarize(data, index, state.selected);
    const companions = A.companions(data, index, state.selected);
    currentRows = A.timeline(data, index, state.selected, state);
    const years = [...new Set(data.months.map(month => month.slice(0, 4)))];
    const title = group ? `${people[0].name.split(' ')[0]} + ${people.length - 1} ${people.length === 2 ? 'other' : 'others'}` : people[0].name;
    const span = summary.first ? summary.first.slice(0, 4) === summary.last.slice(0, 4) ? summary.first.slice(0, 4) : `${summary.first.slice(0, 4)}–${summary.last.slice(0, 4)}` : 'No dates';
    const visibleCompanions = state.showAllCompanions ? companions : companions.slice(0, 8);
    const maxCompanion = Math.max(1, ...companions.map(item => item.count));
    const shareLabel = state.shareMode === 'photos' ? 'tagged photos' : 'person tags';
    return `
      <div class="page-heading"><div class="eyebrow">${group ? `GROUP TIMELINE · ${people.length} PEOPLE` : 'PERSON TIMELINE'}</div><div class="heading-row"><div><h1>${escapeHTML(title)}</h1><p>${group ? 'Every selected person, in the same photo. A shared history of your collection.' : 'A life in photographs, one appearance at a time.'}</p></div><div class="heading-avatars">${people.slice(0, 4).map(person => avatar(person)).join('')}${people.length > 4 ? `<span class="avatar more">+${people.length - 4}</span>` : ''}</div></div>
      ${group ? `<div class="selected-chips">${people.map(person => `<span class="selected-chip">${escapeHTML(person.name)}<button data-remove-person="${person.id}" aria-label="Remove ${escapeHTML(person.name)} from group">×</button></span>`).join('')}</div>` : ''}</div>
      <div class="stats-grid">
        ${stat(group ? 'Photos together' : 'Tagged photos', fmt(summary.count), `of ${fmt(data.meta.taggedPhotos)} people-tagged photos`, 'photos')}
        ${stat('Share of tagged photos', percent(summary.share), 'Across all dates in this export', 'tag', 'teal')}
        ${stat('Photo date span', span, summary.undated ? `${fmt(summary.undated)} undated photos not plotted` : 'Based on the recorded photo dates', 'calendar', 'date-value')}
      </div>
      <section class="card timeline-card" aria-labelledby="timeline-title"><div class="card-header"><div><h2 class="card-title" id="timeline-title">Appearance over time</h2><p class="card-subtitle">By photo date, not the date a tag was added</p></div><div class="timeline-controls">
        <select class="year-select" id="year-filter" aria-label="Filter timeline by year"><option value="all">All years</option>${years.map(year => `<option value="${year}" ${state.year === year ? 'selected' : ''}>${year}</option>`).join('')}</select>
        <div class="segmented" aria-label="Timeline interval"><button data-grain="month" aria-pressed="${state.grain === 'month'}">Month</button><button data-grain="year" aria-pressed="${state.grain === 'year'}">Year</button></div>
        <div class="segmented view-toggle" aria-label="Timeline display"><button data-view="chart" aria-pressed="${state.view === 'chart'}" aria-label="Chart view" title="Chart view">${icon('chart')}</button><button data-view="table" aria-pressed="${state.view === 'table'}" aria-label="Table view" title="Table view">${icon('table')}</button></div>
      </div></div>
      <div class="chart-toolbar"><div class="legend"><span class="legend-item"><span class="legend-dot"></span>${group ? 'Photos together' : 'Tagged photos'}</span><span class="legend-item"><span class="legend-dot teal"></span>Share of ${shareLabel}</span></div><label class="share-control">Percentage of <select id="share-mode"><option value="photos" ${state.shareMode === 'photos' ? 'selected' : ''}>Tagged photos</option><option value="tags" ${state.shareMode === 'tags' ? 'selected' : ''}>Person tags</option></select></label></div>
      ${timelineBody(group)}
      <div class="chart-footer"><span>${summary.undated ? `${fmt(summary.undated)} undated photos not plotted. ` : ''}Percentages use all included people in each period.</span><button class="text-button" id="export-csv" ${currentRows.length ? '' : 'disabled'}>${icon('download')}Export CSV</button></div></section>
      <section class="card" aria-labelledby="companions-title"><div class="card-header"><div><h2 class="card-title" id="companions-title">In good company</h2><p class="card-subtitle">${group ? 'Other people in photos containing this entire group' : `People most often tagged in the same photos as ${escapeHTML(people[0].name.split(' ')[0])}`}</p></div><span class="pill">ALL DATES</span></div>
        ${visibleCompanions.length ? `<div class="companions-list">${visibleCompanions.map((item, i) => `<div class="companion-row"><span class="companion-rank">${String(i + 1).padStart(2, '0')}</span>${avatar(item.person)}<div class="companion-main"><button class="companion-name" data-open-person="${item.person.id}" title="Open ${escapeHTML(item.person.name)}">${escapeHTML(item.person.name)}</button><div class="companion-bar" aria-hidden="true"><span style="width:${item.count / maxCompanion * 100}%"></span></div></div><div class="companion-numbers"><span class="companion-count">${fmt(item.count)} <span class="muted">${item.count === 1 ? 'photo' : 'photos'}</span></span><span class="companion-share" title="Share of ${fmt(summary.count)} matching photos">${percent(item.share)}</span></div><button class="add-person" data-add-person="${item.person.id}" aria-label="Add ${escapeHTML(item.person.name)} to this group" title="Add to group">+</button></div>`).join('')}</div>${companions.length > 8 ? `<div class="companions-footer"><button class="text-button" id="toggle-companions">${state.showAllCompanions ? 'Show fewer people' : `Show all ${companions.length} companions`} <span aria-hidden="true">${state.showAllCompanions ? '↑' : '↓'}</span></button></div>` : ''}` : emptyState(summary.count ? 'Just this company' : 'No photos of this exact group', summary.count ? 'No other included people are tagged in these photos.' : 'Try removing a person from the group. Group counts require everyone to appear in the same photo.')}
      </section>`;
  }

  function timelineBody(group) {
    if (!currentRows.length) return emptyState('No dated photos to plot', 'This selection has no photos in the chosen date range. Try All years; undated photos still appear in the totals above.');
    const denominatorLabel = state.shareMode === 'photos' ? 'All tagged photos' : 'All person tags';
    return `<div class="chart-wrap" id="chart-wrap" tabindex="0" role="group" aria-label="Timeline chart. Use left and right arrow keys to inspect periods, or choose Table view for all values." ${state.view !== 'chart' ? 'hidden' : ''}><div class="chart-tooltip" id="chart-tooltip" role="tooltip" hidden></div></div>
      <div class="table-wrap" id="timeline-table" ${state.view !== 'table' ? 'hidden' : ''}><table class="data-table"><caption class="sr-only">${group ? 'Group' : 'Person'} appearances by ${state.grain}. Share of ${state.shareMode === 'photos' ? 'tagged photos' : 'person-tag occurrences'}.</caption><thead><tr><th scope="col">Period</th><th scope="col">${group ? 'Photos together' : 'Tagged photos'}</th><th scope="col">${denominatorLabel}</th><th scope="col">Share</th></tr></thead><tbody>${currentRows.map(row => `<tr><td>${escapeHTML(monthLabel(row.key))}</td><td>${fmt(row.count)}</td><td>${fmt(row.denominator)}</td><td class="share-cell">${percent(row.share)}</td></tr>`).join('')}</tbody></table></div>`;
  }

  function niceMax(value, minimum = 4) {
    const target = Math.max(value, minimum);
    const power = Math.pow(10, Math.floor(Math.log10(target)));
    const fraction = target / power;
    return ([1, 2, 4, 5, 10].find(step => step >= fraction) || 10) * power;
  }

  function renderChart() {
    const wrap = $('#chart-wrap');
    if (!wrap || !currentRows.length || state.view !== 'chart') return;
    const width = Math.max(300, Math.round(wrap.clientWidth));
    lastChartWidth = width;
    const height = 355, left = 43, right = 16, plotWidth = width - left - right;
    const countTop = 31, countHeight = 139, shareTop = 224, shareHeight = 87, bottom = shareTop + shareHeight;
    const rows = currentRows, step = plotWidth / rows.length;
    const barWidth = Math.max(1, Math.min(29, step * 0.66));
    const countMax = Math.ceil(niceMax(Math.max(0, ...rows.map(row => row.count))) / 4) * 4;
    const shareMax = Math.min(100, niceMax(Math.max(0, ...rows.map(row => (row.share || 0) * 100)), 5));
    const x = i => left + step * (i + 0.5);
    const countY = value => countTop + countHeight - value / countMax * countHeight;
    const shareY = value => shareTop + shareHeight - value * 100 / shareMax * shareHeight;
    const tickLabel = value => Number.isInteger(value) ? String(value) : value.toFixed(1);
    let svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="chart-svg-title chart-svg-desc"><title id="chart-svg-title">Photo appearances and percentage share</title><desc id="chart-svg-desc">${rows.length} ${state.grain} periods. Upper panel: matching photo counts. Lower panel: percentage of ${state.shareMode === 'photos' ? 'photos with people tags' : 'all person-tag occurrences'}. Exact values are available in Table view.</desc><defs><linearGradient id="share-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#168b80" stop-opacity=".13"/><stop offset="100%" stop-color="#168b80" stop-opacity=".015"/></linearGradient></defs>`;
    svg += `<text x="${left}" y="15" class="chart-panel-label">${state.selected.length > 1 ? 'PHOTOS TOGETHER' : 'TAGGED PHOTOS'}</text><text x="${left}" y="208" class="chart-panel-label">% OF ${state.shareMode === 'photos' ? 'TAGGED PHOTOS' : 'PERSON TAGS'}</text>`;
    for (const [top, panelHeight, maximum, isPercent] of [[countTop, countHeight, countMax, false], [shareTop, shareHeight, shareMax, true]]) {
      for (let tick = 0; tick <= 4; tick++) {
        const value = maximum * tick / 4;
        const y = top + panelHeight - panelHeight * tick / 4;
        svg += `<line x1="${left}" x2="${width - right}" y1="${y}" y2="${y}" class="${tick ? 'chart-grid' : 'chart-baseline'}"/><text x="${left - 10}" y="${y + 3}" text-anchor="end" class="chart-label">${tickLabel(value)}${isPercent ? '%' : ''}</text>`;
      }
    }
    if (state.grain === 'month') rows.forEach((row, i) => { if (i && row.key.endsWith('-01')) svg += `<line x1="${left + i * step}" x2="${left + i * step}" y1="${countTop}" y2="${bottom}" stroke="#f0f1f6" stroke-width="1"/>`; });
    rows.forEach((row, i) => {
      const barHeight = row.count / countMax * countHeight;
      svg += `<rect class="chart-bar" x="${x(i) - barWidth / 2}" y="${countTop + countHeight - barHeight}" width="${barWidth}" height="${barHeight}" rx="${Math.min(2, barWidth / 3)}"/>`;
    });
    const segments = [];
    let segment = [];
    rows.forEach((row, i) => {
      if (row.share === null) { if (segment.length) segments.push(segment); segment = []; }
      else segment.push([x(i), shareY(row.share)]);
    });
    if (segment.length) segments.push(segment);
    for (const points of segments) {
      const path = points.map((point, i) => `${i ? 'L' : 'M'}${point[0].toFixed(2)},${point[1].toFixed(2)}`).join(' ');
      svg += `<path d="${path} L${points[points.length - 1][0]},${bottom} L${points[0][0]},${bottom} Z" fill="url(#share-fill)"/><path d="${path}" class="chart-line"/>`;
      if (rows.length <= 50) for (const point of points) svg += `<circle cx="${point[0]}" cy="${point[1]}" r="2.7" fill="#168b80" stroke="white" stroke-width="1.3"/>`;
    }
    const labelEvery = Math.max(1, Math.ceil(rows.length / Math.max(3, Math.floor(plotWidth / 78))));
    rows.forEach((row, i) => {
      if (i % labelEvery !== 0 && i !== rows.length - 1) return;
      if (i !== rows.length - 1 && rows.length - 1 - i < labelEvery * 0.55) return;
      const label = state.grain === 'year' ? row.key : `${monthLabel(row.key, true).split(' ')[0]} ’${row.key.slice(2, 4)}`;
      svg += `<text x="${x(i)}" y="${bottom + 23}" text-anchor="middle" class="chart-label">${escapeHTML(label)}</text>`;
    });
    svg += `<line id="chart-guide" class="chart-guide" x1="0" x2="0" y1="${countTop}" y2="${bottom}" opacity="0"/><rect id="chart-overlay" x="${left}" y="${countTop}" width="${plotWidth}" height="${bottom - countTop}" fill="transparent"/></svg>`;
    const oldTooltip = $('#chart-tooltip');
    wrap.innerHTML = svg;
    wrap.appendChild(oldTooltip || Object.assign(document.createElement('div'), { className: 'chart-tooltip', id: 'chart-tooltip', hidden: true }));
    const tooltip = $('#chart-tooltip');
    const guide = $('#chart-guide');
    let activeIndex = 0;
    function showTooltip(i) {
      activeIndex = Math.max(0, Math.min(rows.length - 1, i));
      const row = rows[activeIndex];
      const denominatorName = state.shareMode === 'photos' ? 'All tagged photos' : 'All person tags';
      tooltip.innerHTML = `<strong>${escapeHTML(monthLabel(row.key))}</strong><div class="tooltip-row"><span>${state.selected.length > 1 ? 'Photos together' : 'Tagged photos'}</span><b>${fmt(row.count)}</b></div><div class="tooltip-row"><span>${denominatorName}</span><b>${fmt(row.denominator)}</b></div><div class="tooltip-row"><span>Percentage share</span><b>${percent(row.share)}</b></div>${state.shareMode === 'tags' ? `<div class="tooltip-row"><span>Selected person tags</span><b>${fmt(row.numerator)}</b></div>` : ''}`;
      tooltip.hidden = false;
      const center = x(activeIndex);
      guide.setAttribute('x1', center); guide.setAttribute('x2', center); guide.setAttribute('opacity', '1');
      tooltip.style.left = `${Math.max(5, Math.min(width - tooltip.offsetWidth - 5, center + 13))}px`;
      tooltip.style.top = '35px';
    }
    function hideTooltip() { tooltip.hidden = true; guide.setAttribute('opacity', '0'); }
    $('#chart-overlay').addEventListener('pointermove', event => {
      const rect = wrap.getBoundingClientRect();
      const pointerX = (event.clientX - rect.left) * width / rect.width;
      showTooltip(Math.floor((pointerX - left) / step));
    });
    $('#chart-overlay').addEventListener('pointerleave', hideTooltip);
    $('#chart-overlay').addEventListener('click', event => {
      const rect = wrap.getBoundingClientRect();
      showTooltip(Math.floor(((event.clientX - rect.left) * width / rect.width - left) / step));
    });
    wrap.onkeydown = event => {
      if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        showTooltip(event.key === 'Home' ? 0 : event.key === 'End' ? rows.length - 1 : activeIndex + (event.key === 'ArrowRight' ? 1 : -1));
        const row = rows[activeIndex];
        announce(`${monthLabel(row.key)}: ${row.count} photos, ${percent(row.share)} share.`);
      } else if (event.key === 'Escape') hideTooltip();
    };
    wrap.onblur = hideTooltip;
  }

  function renderSetup() {
    return `<div class="card setup-card"><div class="eyebrow">LOCAL, READ-ONLY, YOURS</div><h1>${dataError ? 'Let’s check that export.' : 'Bring your collection to life.'}</h1><p>${dataError ? escapeHTML(dataError) : 'Export the people tags from your digiKam database, then reload this page. No photo files or cloud service required.'}</p><pre><code>python extract.py /path/to/digikam4.db</code></pre><p>The exporter writes <code>site/data.js</code> beside this app. You can open <code>site/index.html</code> directly in your browser.</p><button class="primary-button" data-help>Read the import guide ${icon('arrow')}</button></div>`;
  }

  function renderMain() {
    if (resizeObserver) { resizeObserver.disconnect(); resizeObserver = null; }
    $('#content').innerHTML = !index ? renderSetup() : state.selected.length ? renderDetail() : renderOverview();
    if (index && state.selected.length && state.view === 'chart') {
      renderChart();
      const wrap = $('#chart-wrap');
      if (wrap && typeof ResizeObserver !== 'undefined') {
        resizeObserver = new ResizeObserver(() => {
          const width = Math.round(wrap.clientWidth);
          if (Math.abs(width - lastChartWidth) > 1) renderChart();
        });
        resizeObserver.observe(wrap);
      }
    }
  }

  function exportCSV() {
    if (!currentRows.length) return;
    const names = state.selected.map(id => index.peopleById.get(id).name).join(' + ') || 'All tagged photos';
    const safeNames = /^[=+@\-\t\r]/.test(names) ? "'" + names : names;
    const rows = [['Period', 'Selection', 'Matching photos', 'Selected person-tag occurrences', state.shareMode === 'photos' ? 'All tagged photos' : 'All person tags', 'Share (%)'], ...currentRows.map(row => [row.key, safeNames, row.count, row.numerator, row.denominator, row.share === null ? '' : (row.share * 100).toFixed(4)])];
    const csv = '\uFEFF' + rows.map(row => row.map(value => `"${String(value).replace(/"/g, '""')}"`).join(',')).join('\r\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8;' }));
    const link = document.createElement('a');
    link.href = url; link.download = `digikam-people-${state.selected.join('-') || 'collection'}-${state.grain}.csv`;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    announce('Timeline exported as CSV.');
  }

  function renderDataQuality() {
    if (!data?.meta) { $('#data-quality').innerHTML = '<p>No export is loaded yet. Run the extractor first.</p>'; return; }
    const meta = data.meta;
    const entries = [
      ['People scope', meta.peopleRoot], ['Date policy', meta.dateSource === 'auto' ? 'Capture date → album date' : meta.dateSource],
      ['Unknown-person tags', meta.includeUnknown ? 'Included' : 'Excluded'], ['Only person-property tags', meta.personPropertyOnly ? 'Yes' : 'No'],
      ['Capture dates used', fmt(meta.creationDatePhotos)], ['Album dates used', fmt(meta.albumDatePhotos)],
      ['Undated tagged photos', fmt(meta.undatedPhotos)], ['Collection photos', fmt(meta.totalPhotos)],
    ];
    $('#data-quality').innerHTML = `${meta.demo ? '<p><strong>This is synthetic sample data, not your photo collection.</strong></p>' : ''}<dl>${entries.map(([key, value]) => `<dt>${escapeHTML(key)}</dt><dd>${escapeHTML(value)}</dd>`).join('')}</dl>${meta.warnings?.length ? `<ul>${meta.warnings.map(warning => `<li>${escapeHTML(warning)}</li>`).join('')}</ul>` : ''}<p>No image filenames, album paths, or photo files are included in the export.</p>`;
  }

  function openHelp() { renderDataQuality(); $('#help-dialog').showModal(); }

  $('#people-search').addEventListener('input', event => { state.search = event.target.value; renderPeopleList(); });
  $('#people-sort').addEventListener('change', event => { state.sort = event.target.value; renderPeopleList(); });
  $('#people-list').addEventListener('change', event => {
    if (!event.target.matches('.person-check') || !index) return;
    const id = Number(event.target.value);
    setSelection(event.target.checked ? [...state.selected, id] : state.selected.filter(selected => selected !== id));
  });
  $('#people-list').addEventListener('click', event => {
    const button = event.target.closest('[data-person]');
    if (button && index) setSelection([Number(button.dataset.person)], true);
  });
  $('#select-visible').addEventListener('click', () => { if (index) setSelection([...state.selected, ...filteredPeople().map(person => person.id)], true); });
  $('#clear-selection').addEventListener('click', () => setSelection([]));
  $('#view-group').addEventListener('click', scrollToDetail);
  $('.skip-link').addEventListener('click', event => { event.preventDefault(); $('#main-content').focus(); $('#main-content').scrollIntoView({ block: 'start' }); });
  $('#brand-home').addEventListener('click', event => { event.preventDefault(); if (index) setSelection([]); window.scrollTo({ top: 0, behavior: 'smooth' }); });
  $('#content').addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.hasAttribute('data-help')) return openHelp();
    if (!index) return;
    if (button.dataset.openPerson) setSelection([Number(button.dataset.openPerson)], true);
    else if (button.dataset.addPerson) setSelection([...state.selected, Number(button.dataset.addPerson)], true);
    else if (button.dataset.removePerson) setSelection(state.selected.filter(id => id !== Number(button.dataset.removePerson)));
    else if (button.dataset.grain) { state.grain = button.dataset.grain; renderMain(); updateHash(); }
    else if (button.dataset.view) { state.view = button.dataset.view; renderMain(); updateHash(); }
    else if (button.id === 'export-csv') exportCSV();
    else if (button.id === 'toggle-companions') { state.showAllCompanions = !state.showAllCompanions; renderMain(); }
  });
  $('#content').addEventListener('change', event => {
    if (event.target.id === 'year-filter') { state.year = event.target.value; renderMain(); updateHash(); }
    else if (event.target.id === 'share-mode') { state.shareMode = event.target.value; renderMain(); updateHash(); }
  });
  $('#help-button').addEventListener('click', openHelp);
  $('#import-help').addEventListener('click', openHelp);
  $('#close-help').addEventListener('click', () => $('#help-dialog').close());
  $('#help-dialog').addEventListener('click', event => { if (event.target === $('#help-dialog')) { const rect = event.target.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) event.target.close(); } });
  document.addEventListener('keydown', event => {
    if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey && !$('#help-dialog').open && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) { event.preventDefault(); $('#people-search').focus(); }
  });
  window.addEventListener('hashchange', () => { restoreHash(); syncPeopleRows(); renderMain(); });
  window.addEventListener('resize', () => { if (!resizeObserver && state.view === 'chart') renderChart(); });

  if (data?.meta?.demo) $('#demo-banner').hidden = false;
  if (data?.meta?.generatedAt) $('#export-date').textContent = data.meta.demo ? 'Synthetic sample · No real photos' : `Exported ${new Intl.DateTimeFormat('en', { day: 'numeric', month: 'short', year: 'numeric' }).format(new Date(data.meta.generatedAt))}`;
  restoreHash();
  renderPeopleList();
  syncPeopleRows();
  renderMain();
})();
