// Checks the crop/box geometry used by the suspect-tags report.
// The functions live in suspects/report_template.html between <geometry> markers.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "..", "suspects", "report_template.html"), "utf8");
const block = html.match(/\/\/ <geometry>([\s\S]*?)\/\/ <\/geometry>/);
assert.ok(block, "geometry block not found in the template");
const { cropGeometry, boxPercent, aspectMismatch } = new Function(
  `${block[1]}; return { cropGeometry, boxPercent, aspectMismatch };`,
)();

const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`);

test("the crop is centred on the face and scaled so the padded box fills the square", () => {
  // 200x200 face at (100,100) in a 1000x800 photo, padded 1.7x, shown in a 170px square.
  const g = cropGeometry([100, 100, 200, 200], [1000, 800], 170, 1.7);
  const scale = 170 / 340;
  close(g.width, 1000 * scale);
  // The face centre (200,200) must land in the middle of the square (85,85).
  close(g.left + 200 * scale, 85);
  close(g.top + 200 * scale, 85);
});

test("a wide box uses its longer side", () => {
  const g = cropGeometry([0, 0, 400, 100], [1000, 1000], 100, 1);
  close(g.width, 1000 * (100 / 400));
});

test("box percentages are relative to the displayed photo", () => {
  const p = boxPercent([250, 100, 500, 200], [1000, 800]);
  assert.deepEqual(p, { left: 25, top: 12.5, width: 50, height: 25 });
});

test("a photo whose shape differs from the database record is flagged", () => {
  assert.equal(aspectMismatch([4000, 3000], 4000, 3000), false);
  assert.equal(aspectMismatch([4000, 3000], 2000, 1500), false);   // same shape, smaller file
  assert.equal(aspectMismatch([4000, 3000], 3000, 4000), true);    // rotated
});
