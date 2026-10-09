const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

function LoadShim(tunSupport, accessAllowed = true) {
  const handlers = new Map();
  const launches = [];
  const childProcess = Object.fromEntries(
    ['spawn', 'spawnSync', 'execFile', 'execFileSync'].map(methodName => [
      methodName, function Launch(command, ...args) {
        const result = { methodName, command, args };
        launches.push(result);
        return result;
      },
    ]),
  );
  const ipcMain = { handle(channel, handler) { handlers.set(channel, handler); } };
  const fakeFs = {
    constants: fs.constants,
    accessSync() { if (!accessAllowed) throw new Error('Permission denied'); },
  };
  const modules = {
    'node:fs': fakeFs, 'node:path': path,
    'node:child_process': childProcess, electron: { ipcMain },
  };
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, '../scripts/runtime-shim.js'), 'utf8'),
    { nixTunSupport: tunSupport, require: moduleName => modules[moduleName] },
  );
  for (const channel of ['check-is-admin', 'check-tun-service-available', 'relaunch-as-admin']) {
    ipcMain.handle(channel, () => { throw new Error('Upstream root relaunch must not run'); });
  }
  let starts = 0;
  ipcMain.handle('start-clash-service-secure', () => { starts += 1; return 'started'; });
  return { handlers, childProcess, launches, GetStarts: () => starts };
}

test('proxy-only package cannot acquire TUN through wrappers already on the host', () => {
  const shim = LoadShim(false);
  assert.equal(shim.handlers.get('check-is-admin')(), false);
  assert.equal(shim.handlers.get('check-tun-service-available')().available, false);
  assert.equal(shim.handlers.get('relaunch-as-admin')().success, false);
  assert.equal(shim.handlers.get('start-clash-service-secure')({}, { tunMode: true }).success, false);
  assert.equal(shim.GetStarts(), 0);
  assert.equal(shim.handlers.get('start-clash-service-secure')({}, { tunMode: false }), 'started');
  const command = '/nix/store/example/resources/bin/EdNovas-Core';
  assert.equal(shim.childProcess.spawn(command, []).command, command);
});

test('TUN package maps both cores and preserves spawn options and arguments', () => {
  const shim = LoadShim(true);
  assert.equal(shim.handlers.get('check-is-admin')(), true);
  assert.equal(shim.handlers.get('check-tun-service-available')().available, true);
  assert.equal(shim.handlers.get('start-clash-service-secure')({}, { tunMode: true }), 'started');
  for (const methodName of ['spawn', 'spawnSync', 'execFile', 'execFileSync']) {
    for (const [coreName, wrapperName] of [
      ['EdNovas-Core', 'ednovas-core'], ['EdNovas-Core-compatible', 'ednovas-core-compatible'],
    ]) {
      const args = ['-d', '/home/user/config', '-f', 'config.yaml'];
      const options = { stdio: 'pipe', detached: true };
      const launch = shim.childProcess[methodName](`/package/bin/${coreName}`, args, options);
      assert.equal(launch.command, `/run/wrappers/bin/${wrapperName}`);
      assert.equal(launch.args[0], args);
      assert.equal(launch.args[1], options);
    }
  }
  assert.equal(shim.childProcess.spawn('/bin/other', []).command, '/bin/other');
});

test('missing wrapper access denies TUN but preserves ordinary proxy startup', () => {
  const shim = LoadShim(true, false);
  assert.equal(shim.handlers.get('check-is-admin')(), false);
  assert.equal(shim.handlers.get('check-tun-service-available')().available, false);
  assert.equal(shim.handlers.get('start-clash-service-secure')({}, { tunMode: true }).success, false);
  assert.equal(shim.handlers.get('start-clash-service-secure')({}, { tunMode: false }), 'started');
});
