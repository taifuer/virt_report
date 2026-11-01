/* Pure, cumulative snapshots. No virtualization, networking or timing is simulated. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.ExecutionDemoModel = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  function buildTrace(scene) {
    let values = Object.fromEntries(scene.structures.flatMap(structure =>
      structure.fields.map(field => [field.key, field.initial])));
    let nodes = Object.fromEntries(scene.nodes.map(node => [node.id, node.initial || '准备就绪']));
    return scene.steps.map((step, index) => {
      values = {...values, ...step.changes};
      nodes = {...nodes, ...step.nodeStates};
      return {...step, index, values: {...values}, nodes: {...nodes},
        changed: Object.keys(step.changes).filter(key => index > 0)};
    });
  }
  function stops(trace, view) {
    return trace.filter(frame => view !== 'structure' || frame.focus).map(frame => frame.index);
  }
  function project(trace, view, index) {
    return stops(trace, view).filter(value => value <= index).at(-1) ?? 0;
  }
  function next(trace, view, index, direction) {
    const points = stops(trace, view);
    return direction > 0 ? (points.find(value => value > index) ?? points.at(-1))
      : ([...points].reverse().find(value => value < index) ?? 0);
  }
  return {buildTrace, stops, project, next};
});
