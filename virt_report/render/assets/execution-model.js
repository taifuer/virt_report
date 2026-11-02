/* Pure, cumulative snapshots. No virtualization, networking or timing is simulated. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.ExecutionDemoModel = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  function orderedSteps(scene, firstBranch) {
    if (!scene.parallel) return scene.steps;
    const {branches, after} = scene.parallel;
    const first = branches.find(branch => branch.id === firstBranch) || branches[0];
    const ordered = [first, ...branches.filter(branch => branch !== first)];
    const byId = new Map(scene.steps.map(step => [step.id, step]));
    const fork = scene.steps.findIndex(step => step.id === after);
    return [...scene.steps.slice(0, fork + 1), ...ordered.flatMap(branch => branch.steps.map(id => byId.get(id)))];
  }
  function buildTrace(scene, firstBranch) {
    let values = Object.fromEntries(scene.structures.flatMap(structure =>
      structure.fields.map(field => [field.key, field.initial])));
    let nodes = Object.fromEntries(scene.nodes.map(node => [node.id, node.initial || '准备就绪']));
    // Queue data is authored as snapshots in the reference order. Replay its
    // per-event changes so an independent branch cannot import future reclaim.
    let previous = {}, queueState = {};
    const queuePatches = new Map(scene.steps.filter(step => step.queueState).map(step => {
      const patch = Object.fromEntries(Object.entries(step.queueState).filter(([key, value]) =>
        key !== 'changed' && JSON.stringify(previous[key]) !== JSON.stringify(value)));
      previous = step.queueState;
      return [step.id, patch];
    }));
    return orderedSteps(scene, firstBranch).map((step, index) => {
      values = {...values, ...step.changes};
      nodes = {...nodes, ...step.nodeStates};
      if (step.queueState) queueState = {...queueState, ...queuePatches.get(step.id), changed: step.queueState.changed};
      return {...step, index, values: {...values}, nodes: {...nodes},
        ...(step.queueState ? {queueState: JSON.parse(JSON.stringify(queueState))} : {}),
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
  function phases(scene, trace) {
    // Navigation groups reviewed operations; playback retains every causal step.
    const entries = [...(scene.phases || trace.map(frame => ({id: frame.id, title: frame.title, start: frame.id})))];
    entries.sort((a, b) => trace.findIndex(frame => frame.id === a.start) - trace.findIndex(frame => frame.id === b.start));
    return entries.map((phase, ordinal) => {
      const index = trace.findIndex(frame => frame.id === phase.start);
      const next = entries[ordinal + 1];
      const branch = scene.parallel?.branches.find(branch => branch.steps.includes(phase.start));
      return {...phase, index, end: next ? trace.findIndex(frame => frame.id === next.start) - 1 : trace.length - 1,
        ...(branch ? {branch: branch.id} : {})};
    });
  }
  function navigation(scene, trace, view) {
    return view === 'structure'
      ? stops(trace, view).map(index => ({id: trace[index].id, title: trace[index].title, index, end: index}))
      : phases(scene, trace);
  }
  return {buildTrace, stops, project, next, phases, navigation, orderedSteps};
});
