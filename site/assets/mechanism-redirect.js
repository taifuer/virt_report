"use strict";

// Static hosting has no redirect rules; retain bookmarks and section fragments.
(() => {
  const script = document.querySelector("script[data-target]");
  const target = script && script.dataset.target;
  if (target && /^[a-z0-9-]+\.html$/.test(target)) {
    window.location.replace(target + window.location.search + window.location.hash);
  }
})();
