/* Launch only a local static preview; no database, source fetch or AI credentials. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const http = require('node:http');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const root = path.resolve(__dirname, '..');
const reviewSuites = ['conference_papers', 'review_improvements', 'linux_foundations', 'mechanism_directory', 'search', 'report_sources', 'inline_disclosures', 'versions', 'virtqueue', 'walkthrough_interaction', 'execution_demos'];
function getPlan(args = []) {
  if (args.length > 1 || args.some(arg => !['--smoke', '--review', '--full'].includes(arg))) {
    throw Error('Usage: node scripts/run_browser_checks.cjs [--smoke | --review | --full]');
  }
  const mode = (args[0] || '--smoke').slice(2);
  return {
    mode, suites: mode === 'smoke' ? ['smoke'] :
      [...reviewSuites, ...(mode === 'full' ? ['walkthrough_global'] : [])],
    timeoutMs: mode === 'smoke' ? 180000 : 600000,
  };
}
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
function suiteEnv(artifacts) {
  return {
    ...process.env,
    BROWSER_ARTIFACT_DIR: artifacts,
    PAPER_SCREENSHOT_DIR: path.join(artifacts, 'papers'),
    REVIEW_SCREENSHOT_DIR: path.join(artifacts, 'review'),
    LINUX_SCREENSHOT_DIR: path.join(artifacts, 'linux'),
    MECHANISM_DIRECTORY_SCREENSHOT_DIR: path.join(artifacts, 'directory'),
    SEARCH_SCREENSHOT_DIR: path.join(artifacts, 'search'),
    REPORT_SOURCES_SCREENSHOT_DIR: path.join(artifacts, 'report-sources'),
    INLINE_DISCLOSURES_SCREENSHOT_DIR: path.join(artifacts, 'disclosures'),
    VERSIONS_SCREENSHOT_DIR: path.join(artifacts, 'versions'),
    VIRTQUEUE_SCREENSHOT_DIR: path.join(artifacts, 'virtqueue'),
    WALKTHROUGH_INTERACTION_SCREENSHOT_DIR: path.join(artifacts, 'interaction'),
    EXECUTION_SCREENSHOT_DIR: path.join(artifacts, 'execution'),
    WALKTHROUGH_GLOBAL_SCREENSHOT_DIR: path.join(artifacts, 'global'),
  };
}
function healthy(url) {
  return new Promise(resolve => {
    const request = http.get(url, response => { response.resume(); resolve(response.statusCode === 200); });
    request.on('error', () => resolve(false));
    request.setTimeout(1000, () => request.destroy());
  });
}
async function run(args = process.argv.slice(2)) {
  const {mode, suites, timeoutMs} = getPlan(args);
  const artifacts = path.join(path.resolve(process.env.BROWSER_ARTIFACT_DIR || '/tmp/virt-report-browser'), mode);
  fs.mkdirSync(artifacts, {recursive: true});
  const env = suiteEnv(artifacts), timings = [], started = performance.now();
  const listener = net.createServer();
  listener.listen(0, '127.0.0.1'); await once(listener, 'listening');
  const port = listener.address().port;
  await new Promise(resolve => listener.close(resolve));
  const base = `http://127.0.0.1:${port}`;
  const log = fs.createWriteStream(path.join(artifacts, 'preview.log'));
  const server = spawn(process.env.VIRT_REPORT_PYTHON || path.join(root, '.venv/bin/python'),
    ['scripts/preview_site.py', '--port', String(port)], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']});
  const serverClosed = new Promise(resolve => server.once('close', resolve));
  let startError;
  server.on('error', error => { startError = error; });
  server.stdout.pipe(log, {end: false}); server.stderr.pipe(log, {end: false});
  let child;
  const stop = () => { child?.kill('SIGTERM'); server.kill('SIGTERM'); };
  process.once('SIGINT', stop); process.once('SIGTERM', stop);
  try {
    const deadline = Date.now() + 10000;
    while (!(await healthy(`${base}/search.html`))) {
      if (startError) throw startError;
      if (server.exitCode !== null || Date.now() > deadline) throw Error('Local preview did not start; see preview.log');
      await delay(100);
    }
    if (server.exitCode !== null) throw Error('Local preview exited during startup');
    for (const suite of suites) {
      console.log(`Browser suite: ${suite} (${mode})`);
      const suiteStarted = performance.now();
      const output = fs.createWriteStream(path.join(artifacts, `${suite}.log`));
      const url = suite === 'versions' ? `${base}/versions.html` : base;
      child = spawn(process.execPath, [`scripts/check_${suite}_browser.cjs`, url],
        {cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'], timeout: timeoutMs});
      child.stdout.pipe(output, {end: false}); child.stderr.pipe(output, {end: false});
      child.stdout.pipe(process.stdout); child.stderr.pipe(process.stderr);
      const [code, signal] = await once(child, 'close'); output.end();
      const seconds = Math.round((performance.now() - suiteStarted) / 100) / 10;
      timings.push({suite, seconds, code, signal});
      console.log(`${suite}: ${seconds}s`);
      if (code !== 0) throw Error(`${suite} failed (${signal || code}); see ${artifacts}`);
    }
    console.log(`PASS ${mode} browser checks in ${((performance.now() - started) / 1000).toFixed(1)}s; artifacts: ${artifacts}`);
  } finally {
    stop();
    await serverClosed;
    log.end();
    fs.writeFileSync(path.join(artifacts, 'timings.json'), JSON.stringify({mode, timings}, null, 2) + '\n');
    process.removeListener('SIGINT', stop); process.removeListener('SIGTERM', stop);
  }
}
if (require.main === module) run().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = {getPlan};
