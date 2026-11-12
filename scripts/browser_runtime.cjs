'use strict';

function browserOptions(options = {}, env = process.env) {
  return env.PLAYWRIGHT_CHANNEL ? {...options, channel: env.PLAYWRIGHT_CHANNEL} : {...options};
}

function launchBrowser(options = {}) {
  // Lazy loading keeps the model/runner unit tests independent of Playwright.
  const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
  return chromium.launch(browserOptions(options));
}

module.exports = {browserOptions, launchBrowser};
