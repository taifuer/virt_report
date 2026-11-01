/* Pure orthogonal routing around diagram cards; no DOM or layout assumptions. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.WalkthroughRouting = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const GAP = 4, TURN_COST = 12, EPS = 1e-7;
  const validRect = rect => rect && ['left', 'right', 'top', 'bottom'].every(key => Number.isFinite(rect[key]))
    && rect.right > rect.left && rect.bottom > rect.top;
  const sameRect = (a, b) => ['left', 'right', 'top', 'bottom'].every(key => Math.abs(a[key] - b[key]) < EPS);
  const inside = (point, rect) => point[0] > rect.left + EPS && point[0] < rect.right - EPS
    && point[1] > rect.top + EPS && point[1] < rect.bottom - EPS;

  function crosses(a, b, rect) {
    if (Math.abs(a[0] - b[0]) < EPS) {
      return a[0] > rect.left + EPS && a[0] < rect.right - EPS
        && Math.max(a[1], b[1]) > rect.top + EPS && Math.min(a[1], b[1]) < rect.bottom - EPS;
    }
    if (Math.abs(a[1] - b[1]) < EPS) {
      return a[1] > rect.top + EPS && a[1] < rect.bottom - EPS
        && Math.max(a[0], b[0]) > rect.left + EPS && Math.min(a[0], b[0]) < rect.right - EPS;
    }
    return true;
  }

  function clear(a, b, obstacles) {
    return obstacles.every(rect => !crosses(a, b, rect));
  }

  function ports(rect) {
    const x = (rect.left + rect.right) / 2, y = (rect.top + rect.bottom) / 2;
    return [
      {anchor: [rect.right, y], point: [rect.right + GAP, y], direction: 0},
      {anchor: [rect.left, y], point: [rect.left - GAP, y], direction: 0},
      {anchor: [x, rect.bottom], point: [x, rect.bottom + GAP], direction: 1},
      {anchor: [x, rect.top], point: [x, rect.top - GAP], direction: 1},
    ];
  }

  // Binary min-heap keeps route calculation small even with several card rows.
  function push(heap, entry) {
    heap.push(entry);
    let index = heap.length - 1;
    while (index > 0) {
      const parent = Math.floor((index - 1) / 2);
      if (heap[parent].cost <= entry.cost) break;
      heap[index] = heap[parent]; index = parent;
    }
    heap[index] = entry;
  }

  function pop(heap) {
    const first = heap[0], last = heap.pop();
    if (!heap.length) return first;
    let index = 0;
    while (2 * index + 1 < heap.length) {
      let child = 2 * index + 1;
      if (child + 1 < heap.length && heap[child + 1].cost < heap[child].cost) child++;
      if (heap[child].cost >= last.cost) break;
      heap[index] = heap[child]; index = child;
    }
    heap[index] = last;
    return first;
  }

  function simplify(points) {
    const result = [];
    for (const point of points) {
      const last = result.at(-1);
      if (last && Math.abs(last[0] - point[0]) < EPS && Math.abs(last[1] - point[1]) < EPS) continue;
      if (result.length >= 2) {
        const before = result.at(-2);
        const vertical = Math.abs(before[0] - last[0]) < EPS && Math.abs(last[0] - point[0]) < EPS;
        const horizontal = Math.abs(before[1] - last[1]) < EPS && Math.abs(last[1] - point[1]) < EPS;
        if (vertical || horizontal) result.pop();
      }
      result.push(point);
    }
    return result;
  }

  function route(sourceRect, targetRect, obstacles, bounds) {
    if (!validRect(sourceRect) || !validRect(targetRect) || sameRect(sourceRect, targetRect)
      || !Array.isArray(obstacles) || !obstacles.every(validRect)
      || !bounds || !Number.isFinite(bounds.width) || !Number.isFinite(bounds.height)
      || bounds.width <= GAP * 2 || bounds.height <= GAP * 2) return null;
    const originals = [...obstacles];
    for (const rect of [sourceRect, targetRect]) {
      if (!originals.some(item => sameRect(item, rect))) originals.push(rect);
    }
    const blocked = originals.map(rect => ({left: rect.left - GAP, right: rect.right + GAP,
      top: rect.top - GAP, bottom: rect.bottom + GAP}));
    const inBounds = point => point[0] >= GAP && point[0] <= bounds.width - GAP
      && point[1] >= GAP && point[1] <= bounds.height - GAP;
    function availablePorts(rect) {
      return ports(rect).filter(port => inBounds(port.anchor) && inBounds(port.point)
        && !blocked.some(box => inside(port.point, box))
        && clear(port.anchor, port.point, originals.filter(box => !sameRect(box, rect))));
    }
    const starts = availablePorts(sourceRect), ends = availablePorts(targetRect);
    if (!starts.length || !ends.length) return null;
    // Obstacle edges form a small visibility grid, including the outer gutter.
    const coordinates = (axis, size, low, high) => [...new Set([GAP, size - GAP,
      ...blocked.flatMap(rect => [rect[low], rect[high]]),
      ...starts.map(port => port.point[axis]), ...ends.map(port => port.point[axis]),
    ].filter(value => value >= GAP && value <= size - GAP))].sort((a, b) => a - b);
    const xs = coordinates(0, bounds.width, 'left', 'right');
    const ys = coordinates(1, bounds.height, 'top', 'bottom');
    const width = xs.length, points = [], adjacency = [];
    for (const y of ys) for (const x of xs) {
      const point = [x, y];
      points.push(blocked.some(rect => inside(point, rect)) ? null : point);
      adjacency.push([]);
    }
    for (let node = 0; node < points.length; node++) {
      if (!points[node]) continue;
      const candidates = [];
      if (node % width < width - 1) candidates.push([node + 1, 0]);
      if (node + width < points.length) candidates.push([node + width, 1]);
      for (const [next, direction] of candidates) {
        if (!points[next] || !clear(points[node], points[next], blocked)) continue;
        const distance = Math.abs(points[node][0] - points[next][0]) + Math.abs(points[node][1] - points[next][1]);
        adjacency[node].push({next, direction, distance});
        adjacency[next].push({next: node, direction, distance});
      }
    }
    const nodeAt = point => ys.indexOf(point[1]) * width + xs.indexOf(point[0]);
    const distances = new Float64Array(points.length * 2).fill(Infinity);
    const parents = new Int32Array(points.length * 2).fill(-1);
    const origins = new Map(), targets = new Map(), heap = [];
    for (const port of starts) {
      const state = nodeAt(port.point) * 2 + port.direction;
      distances[state] = GAP; origins.set(state, port);
      push(heap, {state, cost: GAP});
    }
    for (const port of ends) {
      const node = nodeAt(port.point);
      if (!targets.has(node)) targets.set(node, []);
      targets.get(node).push(port);
    }
    let best = null, bestCost = Infinity;
    while (heap.length) {
      const current = pop(heap);
      if (current.cost !== distances[current.state]) continue;
      if (current.cost >= bestCost) break;
      const node = Math.floor(current.state / 2), direction = current.state % 2;
      for (const port of targets.get(node) || []) {
        const cost = current.cost + GAP + (direction === port.direction ? 0 : TURN_COST);
        if (cost < bestCost) { bestCost = cost; best = {state: current.state, port}; }
      }
      for (const edge of adjacency[node]) {
        const state = edge.next * 2 + edge.direction;
        const cost = current.cost + edge.distance + (direction === edge.direction ? 0 : TURN_COST);
        if (cost >= distances[state]) continue;
        distances[state] = cost; parents[state] = current.state;
        push(heap, {state, cost});
      }
    }
    if (!best) return null;
    const path = [best.port.anchor];
    let state = best.state;
    while (state >= 0) {
      path.push(points[Math.floor(state / 2)]);
      if (parents[state] < 0) {
        path.push(origins.get(state).anchor);
        break;
      }
      state = parents[state];
    }
    const result = simplify(path.reverse());
    // Fail closed if a layout is overlapping or cannot supply a clear route.
    if (result.length < 2 || result.some((point, index) => index > 0
      && !clear(result[index - 1], point, originals))) return null;
    return result;
  }

  return {route};
});
