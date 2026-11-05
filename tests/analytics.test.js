const test = require('node:test');
const assert = require('node:assert/strict');
const A = require('../site/analytics.js');

const data = {
  version: 1,
  months: ['2020-01', '2020-02', '2020-03', '2021-01'],
  people: [{ id: 1, name: 'Alice' }, { id: 2, name: 'Bob' }, { id: 3, name: 'Carol' }],
  photos: [[0, [1, 2]], [0, [1, 3]], [1, [2, 3]], [2, [1, 2, 3]], [-1, [1, 2]]],
};
const index = A.buildIndex(data);
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-10, `${actual} != ${expected}`);

test('one appearance per person per photo; undated photo retained', () => {
  assert.deepEqual(index.byPerson.get(1), [0, 1, 3, 4]);
  assert.deepEqual(index.taggedByMonth, [2, 1, 1, 0]);
  assert.deepEqual(index.tagsByMonth, [4, 2, 3, 0]);
  assert.equal(A.summarize(data, index, [1]).count, 4);
  assert.equal(A.summarize(data, index, [1]).undated, 1);
});

test('group is an intersection, not a union', () => {
  assert.deepEqual(A.selectionPhotos(data, index, [1, 2]), [0, 3, 4]);
  assert.deepEqual(A.selectionPhotos(data, index, [1, 2, 3]), [3]);
  assert.deepEqual(A.selectionPhotos(data, index, [2, 1, 1]), [0, 3, 4]);
  assert.equal(A.summarize(data, index, [1, 2]).count, 3);
});

test('monthly photo shares include zero-activity months and undefined denominators', () => {
  const rows = A.timeline(data, index, [1, 2]);
  assert.deepEqual(rows.map(row => row.count), [1, 0, 1, 0]);
  assert.deepEqual(rows.map(row => row.denominator), [2, 1, 1, 0]);
  assert.deepEqual(rows.map(row => row.share), [0.5, 0, 1, null]);
});

test('person-tag share uses occurrences, with group intersection numerator', () => {
  const rows = A.timeline(data, index, [1, 2], { shareMode: 'tags' });
  assert.deepEqual(rows.map(row => row.numerator), [2, 0, 2, 0]);
  assert.deepEqual(rows.map(row => row.denominator), [4, 2, 3, 0]);
  close(rows[2].share, 2 / 3);
});

test('year aggregation and year filtering', () => {
  const rows = A.timeline(data, index, [1, 2], { grain: 'year' });
  assert.equal(rows.length, 2);
  assert.equal(rows[0].count, 2);
  close(rows[0].share, 2 / 4);
  assert.equal(rows[1].share, null);
  const filtered = A.timeline(data, index, [1], { year: '2021' });
  assert.deepEqual(filtered.map(row => row.key), ['2021-01']);
  assert.equal(filtered[0].count, 0);
});

test('companions exclude every selected person and rank by shared photos', () => {
  const single = A.companions(data, index, [1]);
  assert.deepEqual(single.map(item => [item.person.id, item.count]), [[2, 3], [3, 2]]);
  close(single[0].share, 3 / 4);
  const group = A.companions(data, index, [1, 2]);
  assert.deepEqual(group.map(item => [item.person.id, item.count]), [[3, 1]]);
  close(group[0].share, 1 / 3);
  assert.deepEqual(A.companions(data, index, [1, 2, 3]), []);
  assert.equal(A.companions(data, index, [1], 1).length, 1);
});

test('overview counts all tagged photos, not tag occurrences', () => {
  assert.deepEqual(A.overviewTimeline(data, index).map(row => [row.key, row.count]), [['2020', 4], ['2021', 0]]);
  assert.equal(A.selectionPhotos(data, index, []).length, 5);
});

test('sorted intersection handles empty and disjoint inputs', () => {
  assert.deepEqual(A.intersectSorted([], [1, 2]), []);
  assert.deepEqual(A.intersectSorted([1, 3, 5], [2, 3, 4, 5]), [3, 5]);
});

test('empty exports are valid', () => {
  const empty = { version: 1, months: [], people: [], photos: [] };
  const emptyIndex = A.buildIndex(empty);
  assert.deepEqual(A.timeline(empty, emptyIndex, []), []);
  assert.deepEqual(A.companions(empty, emptyIndex, []), []);
  assert.equal(A.summarize(empty, emptyIndex, []).share, 0);
});

test('invalid data fails clearly', () => {
  assert.throws(() => A.buildIndex({ version: 99 }), /Unsupported/);
  assert.throws(() => A.buildIndex({ version: 1, months: [], people: [], photos: [[0, [1]]] }), /Unknown person/);
});
