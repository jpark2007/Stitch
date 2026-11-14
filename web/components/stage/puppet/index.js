// Test entry so `node --test components/stage/puppet/` (from web/) runs this
// folder's unit tests: Node resolves a directory argument to its index.js.
// Not imported by the app; import the modules you need directly.
// (Dynamic import keeps this file CommonJS-compatible, so Node does not warn
// about web/package.json having no "type".)
for (const f of ["ik", "kinematics", "poses", "items"]) import(`./${f}.test.mjs`);
