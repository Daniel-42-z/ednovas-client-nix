// Loaded before the upstream bytenode entrypoint captures Electron/Node APIs.
// nixTunSupport is a build-time constant, never inferred from user environment.
const nixFs = require('node:fs');
const nixPath = require('node:path');
const nixChildProcess = require('node:child_process');
const { ipcMain: nixIpcMain } = require('electron');
const nixDebug = typeof process !== 'undefined' && process.env.EDNOVAS_DEBUG === '1';
const nixPromisifyCustom = Symbol.for('nodejs.util.promisify.custom');

function Trace(message) {
  if (nixDebug) process.stderr.write(`[EdNovas Nix] ${message}\n`);
}

const nixCoreWrappers = {
  'EdNovas-Core': '/run/wrappers/bin/ednovas-core',
  'EdNovas-Core-compatible': '/run/wrappers/bin/ednovas-core-compatible',
};

function TunAvailable() {
  if (!nixTunSupport) return false;
  try {
    for (const wrapperPath of Object.values(nixCoreWrappers)) {
      nixFs.accessSync(wrapperPath, nixFs.constants.X_OK);
    }
    nixFs.accessSync('/dev/net/tun', nixFs.constants.R_OK | nixFs.constants.W_OK);
    return true;
  } catch {
    return false;
  }
}

function TunReason() {
  return nixTunSupport
    ? 'Enable programs.ednovas-cloud.tun.enable, join its configured group, and log in again.'
    : 'This package supports proxy mode. Enable the NixOS module TUN option for TUN mode.';
}

function ResolveCore(command) {
  // Upstream's process scan uses an absolute FHS path, absent on NixOS.
  if (command === '/bin/ps' || command === '/usr/bin/ps') return 'ps';
  const wrapperPath = typeof command === 'string'
    ? nixCoreWrappers[nixPath.basename(command)] : undefined;
  return wrapperPath && TunAvailable() ? wrapperPath : command;
}

for (const methodName of ['spawn', 'spawnSync', 'execFile', 'execFileSync', 'exec', 'execSync']) {
  const originalMethod = nixChildProcess[methodName];
  if (typeof originalMethod !== 'function') continue;
  const wrappedMethod = function LaunchCore(command, ...args) {
    const resolvedCommand = ['exec', 'execSync'].includes(methodName) ? command : ResolveCore(command);
    const commandName = typeof resolvedCommand === 'string'
      ? nixPath.basename(resolvedCommand.split(/\s/, 1)[0]) : '(non-string)';
    Trace(`${methodName} begin executable=${commandName} (arguments omitted)`);
    try {
      const result = originalMethod.call(this, resolvedCommand, ...args);
      Trace(`${methodName} returned executable=${commandName}`);
      if (nixDebug && typeof result?.once === 'function') {
        result.once('exit', (code, signal) => Trace(`child exit executable=${commandName} code=${code} signal=${signal}`));
        // Do not install an 'error' listener: that would change upstream's error handling.
      }
      return result;
    } catch (error) {
      Trace(`${methodName} failed executable=${commandName} code=${error.code || error.name}`);
      throw error;
    }
  };
  // execFile/exec have a custom Promise API returning { stdout, stderr }.
  // Generic promisify would return only stdout, breaking upstream's process scan.
  // Delegate through our wrapper so path redirection and diagnostics still apply.
  if (typeof originalMethod[nixPromisifyCustom] === 'function') {
    Object.defineProperty(wrappedMethod, nixPromisifyCustom, {
      value: function LaunchCoreAsync(...args) {
        let child;
        const promise = new Promise((resolve, reject) => {
          child = wrappedMethod.call(this, ...args, (error, stdout, stderr) => {
            if (error) {
              error.stdout = stdout;
              error.stderr = stderr;
              reject(error);
            } else {
              resolve({ stdout, stderr });
            }
          });
        });
        promise.child = child;
        return promise;
      },
    });
  }
  nixChildProcess[methodName] = wrappedMethod;
}

const nixOriginalHandle = nixIpcMain.handle.bind(nixIpcMain);
nixIpcMain.handle = function RegisterHandler(channel, handler) {
  if (channel === 'check-is-admin') {
    return nixOriginalHandle(channel, () => TunAvailable());
  }
  if (channel === 'check-tun-service-available') {
    return nixOriginalHandle(channel, () => ({
      available: TunAvailable(),
      needsAuthorization: false,
      staleRootCore: false,
      reason: TunAvailable() ? '' : TunReason(),
    }));
  }
  if (channel === 'relaunch-as-admin' || channel === 'authorize-tun-core') {
    return nixOriginalHandle(channel, () => ({
      success: TunAvailable(), error: TunAvailable() ? undefined : TunReason(),
    }));
  }
  if (channel === 'start-clash-service-secure') {
    return nixOriginalHandle(channel, (event, options, ...args) => {
      Trace(`start-clash-service-secure entered tunRequested=${options?.tunMode} tunAvailable=${TunAvailable()}`);
      if (options?.tunMode && !TunAvailable()) {
        return { success: false, msg: TunReason(), error: TunReason() };
      }
      try {
        const result = handler(event, options, ...args);
        if (nixDebug && typeof result?.then === 'function') {
          return result.then(value => {
            Trace('start-clash-service-secure resolved');
            return value;
          }, error => {
            Trace(`start-clash-service-secure rejected code=${error.code || error.name}`);
            throw error;
          });
        }
        Trace('start-clash-service-secure returned');
        return result;
      } catch (error) {
        Trace(`start-clash-service-secure threw code=${error.code || error.name}`);
        throw error;
      }
    });
  }
  return nixOriginalHandle(channel, handler);
};

if (nixDebug) {
  Trace(`shim loaded uid=${process.getuid?.()} tunAvailable=${TunAvailable()}`);
  setInterval(() => Trace('main event loop responsive'), 10000).unref();
}
