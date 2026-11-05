/* Pure aggregation functions. Shared by the browser and the Node test suite. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.PeopleAnalytics = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  function buildIndex(data) {
    if (!data || data.version !== 1 || !Array.isArray(data.people) || !Array.isArray(data.photos) || !Array.isArray(data.months)) {
      throw new Error('Unsupported or missing data. Run extract.py to generate site/data.js.');
    }
    const byPerson = new Map(data.people.map(person => [person.id, []]));
    const taggedByMonth = new Array(data.months.length).fill(0);
    const tagsByMonth = new Array(data.months.length).fill(0);
    const allPhotoIndices = [];
    data.photos.forEach((photo, index) => {
      const [month, people] = photo;
      allPhotoIndices.push(index);
      for (const id of people) {
        if (!byPerson.has(id)) throw new Error(`Unknown person ID ${id} in photo data.`);
        byPerson.get(id).push(index);
      }
      if (month >= 0) {
        if (month >= data.months.length) throw new Error('Photo month is outside the exported month range.');
        taggedByMonth[month]++;
        tagsByMonth[month] += people.length;
      }
    });
    return { byPerson, taggedByMonth, tagsByMonth, allPhotoIndices, peopleById: new Map(data.people.map(p => [p.id, p])) };
  }

  function intersectSorted(left, right) {
    const result = [];
    let i = 0, j = 0;
    while (i < left.length && j < right.length) {
      if (left[i] === right[j]) { result.push(left[i]); i++; j++; }
      else if (left[i] < right[j]) i++;
      else j++;
    }
    return result;
  }

  function selectionPhotos(data, index, selectedIds) {
    const ids = [...new Set(selectedIds)].filter(id => index.byPerson.has(id));
    if (!ids.length) return index.allPhotoIndices;
    const lists = ids.map(id => index.byPerson.get(id)).sort((a, b) => a.length - b.length);
    return lists.reduce((common, list) => intersectSorted(common, list));
  }

  function periodKeys(data, grain, year) {
    const months = data.months.map((month, index) => ({ month, index })).filter(item => year === 'all' || item.month.startsWith(year + '-'));
    if (!months.length) return [];
    if (grain === 'month') return months.map(item => ({ key: item.month, months: [item.index] }));
    const years = new Map();
    for (const item of months) {
      const key = item.month.slice(0, 4);
      if (!years.has(key)) years.set(key, { key, months: [] });
      years.get(key).months.push(item.index);
    }
    return [...years.values()];
  }

  function timeline(data, index, selectedIds, options = {}) {
    const { grain = 'month', year = 'all', shareMode = 'photos' } = options;
    const photos = selectionPhotos(data, index, selectedIds);
    const selectedCount = new Set(selectedIds).size;
    const counts = new Array(data.months.length).fill(0);
    for (const photoId of photos) {
      const month = data.photos[photoId][0];
      if (month >= 0) counts[month]++;
    }
    return periodKeys(data, grain, year).map(period => {
      const count = period.months.reduce((sum, month) => sum + counts[month], 0);
      const denominator = period.months.reduce((sum, month) => sum + (shareMode === 'tags' ? index.tagsByMonth[month] : index.taggedByMonth[month]), 0);
      const numerator = shareMode === 'tags' ? count * selectedCount : count;
      return { key: period.key, count, numerator, denominator, share: denominator ? numerator / denominator : null };
    });
  }

  function companions(data, index, selectedIds, limit = Infinity) {
    const selected = new Set(selectedIds);
    const photos = selectionPhotos(data, index, selectedIds);
    const counts = new Map();
    for (const photoId of photos) {
      for (const personId of data.photos[photoId][1]) {
        if (!selected.has(personId)) counts.set(personId, (counts.get(personId) || 0) + 1);
      }
    }
    return [...counts].map(([id, count]) => ({
      person: index.peopleById.get(id), count,
      share: photos.length ? count / photos.length : 0,
    })).sort((a, b) => b.count - a.count || a.person.name.localeCompare(b.person.name) || a.person.id - b.person.id).slice(0, limit);
  }

  function overviewTimeline(data, index, grain = 'year') {
    return periodKeys(data, grain, 'all').map(period => ({
      key: period.key,
      count: period.months.reduce((sum, month) => sum + index.taggedByMonth[month], 0),
      people: period.months.reduce((sum, month) => sum + index.tagsByMonth[month], 0),
    }));
  }

  function summarize(data, index, selectedIds) {
    const photos = selectionPhotos(data, index, selectedIds);
    let undated = 0, first = Infinity, last = -Infinity;
    for (const photoId of photos) {
      const month = data.photos[photoId][0];
      if (month < 0) undated++;
      else { first = Math.min(first, month); last = Math.max(last, month); }
    }
    return {
      count: photos.length,
      share: index.allPhotoIndices.length ? photos.length / index.allPhotoIndices.length : 0,
      undated,
      first: Number.isFinite(first) ? data.months[first] : null,
      last: last >= 0 ? data.months[last] : null,
    };
  }

  return { buildIndex, intersectSorted, selectionPhotos, periodKeys, timeline, companions, overviewTimeline, summarize };
});
