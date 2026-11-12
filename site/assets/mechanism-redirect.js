"use strict";

// Static hosting has no redirect rules; retain bookmarks and section fragments.
(() => {
  const script = document.querySelector("script[data-target]");
  const target = script && script.dataset.target;
  if (target && /^[a-z0-9-]+\.html$/.test(target)) {
    let hash = window.location.hash;
    try {
      const anchors = JSON.parse(script.dataset.anchors || '{}');
      const mapped = anchors[decodeURIComponent(hash.slice(1))];
      if (typeof mapped === 'string' && /^[a-z][a-z0-9-]*$/.test(mapped)) hash = '#' + mapped;
    } catch (_) { /* Keep malformed bookmarks unchanged. */ }
    window.location.replace(target + window.location.search + hash);
  }
})();
