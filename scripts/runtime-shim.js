// Loaded before the upstream bytenode entrypoint captures Electron/Node APIs.
// nixTunSupport is a build-time constant, never inferred from user environment.
const nixFs = require('node:fs');
const nixPath = require('node:path');
const nixChildProcess = require('node:child_process');
const { ipcMain: nixIpcMain } = require('electron');

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
  const wrapperPath = typeof command === 'string'
    ? nixCoreWrappers[nixPath.basename(command)] : undefined;
  return wrapperPath && TunAvailable() ? wrapperPath : command;
}

for (const methodName of ['spawn', 'spawnSync', 'execFile', 'execFileSync']) {
  const originalMethod = nixChildProcess[methodName];
  nixChildProcess[methodName] = function LaunchCore(command, ...args) {
    return originalMethod.call(this, ResolveCore(command), ...args);
  };
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
      if (options?.tunMode && !TunAvailable()) {
        return { success: false, msg: TunReason(), error: TunReason() };
      }
      return handler(event, options, ...args);
    });
  }
  return nixOriginalHandle(channel, handler);
};
