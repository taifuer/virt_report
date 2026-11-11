/* Launch only a local static preview; no database, source fetch or AI credentials. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const http = require('node:http');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const root = path.resolve(__dirname, '..');
const artifacts = path.resolve(process.env.BROWSER_ARTIFACT_DIR || '/tmp/virt-report-browser');
const suites = ['conference_papers', 'review_improvements', 'linux_foundations', 'mechanism_directory', 'search', 'report_sources', 'inline_disclosures', 'versions', 'virtqueue', 'walkthrough_interaction', 'execution_demos'];
if (process.argv.includes('--full')) suites.push('walkthrough_global');
fs.mkdirSync(artifacts, {recursive: true});
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const env = {...process.env,
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
function healthy(url) {
  return new Promise(resolve => {
    const request = http.get(url, response => { response.resume(); resolve(response.statusCode === 200); });
    request.on('error', () => resolve(false));
    request.setTimeout(1000, () => request.destroy());
  });
}
(async () => {
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
      console.log(`Browser suite: ${suite}`);
      const output = fs.createWriteStream(path.join(artifacts, `${suite}.log`));
      const url = suite === 'versions' ? `${base}/versions.html` : base;
      child = spawn(process.execPath, [`scripts/check_${suite}_browser.cjs`, url],
        {cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'], timeout: 600000});
      child.stdout.pipe(output, {end: false}); child.stderr.pipe(output, {end: false});
      child.stdout.pipe(process.stdout); child.stderr.pipe(process.stderr);
      const [code, signal] = await once(child, 'close'); output.end();
      if (code !== 0) throw Error(`${suite} failed (${signal || code}); see ${artifacts}`);
    }
    console.log(`PASS browser checks; artifacts: ${artifacts}`);
  } finally {
    stop();
    await serverClosed;
    log.end();
    process.removeListener('SIGINT', stop); process.removeListener('SIGTERM', stop);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
