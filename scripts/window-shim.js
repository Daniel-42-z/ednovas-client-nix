// Runs only in the privileged AppImage, before the upstream bytecode loader.
const { app, BrowserWindow } = require('electron');
const net = require('node:net');
const fs = require('node:fs');

let quitting = false;
const controlPath = process.env.EDNOVAS_CONTROL_SOCKET;

app.on('before-quit', () => { quitting = true; });
app.on('browser-window-created', (_event, window) => {
  window.on('close', (event) => {
    if (!quitting && !window.isDestroyed()) {
      event.preventDefault();
      window.hide();
    }
  });
});

function RestoreWindow() {
  const windows = BrowserWindow.getAllWindows().filter(window => !window.isDestroyed());
  const window = windows.find(candidate => !candidate.isVisible()) || windows[0];
  if (window) {
    window.show();
    window.focus();
  }
}

if (controlPath) {
  app.whenReady().then(() => {
    // The containing directory is user-owned and mode 0700, created by the
    // authenticated launcher. Never accept an arbitrary socket path from argv.
    if (!/^\/run\/user\/[0-9]+\/ednovas-bus\.[A-Za-z0-9]{8}\/control$/.test(controlPath)) {
      throw new Error('Invalid EdNovas control socket location');
    }
    const server = net.createServer(connection => {
      connection.setTimeout(2000, () => connection.destroy());
      connection.once('data', bytes => {
        const command = bytes.toString('utf8');
        if (command === 'show\n') RestoreWindow();
        else if (command === 'quit\n') {
          quitting = true;
          app.quit();
        }
        connection.end();
      });
    });
    server.on('error', error => console.error('EdNovas control socket:', error));
    server.listen(controlPath, () => fs.chmodSync(controlPath, 0o666));
    app.on('will-quit', () => {
      server.close();
      try { fs.unlinkSync(controlPath); } catch (error) { if (error.code !== 'ENOENT') console.error(error); }
    });
  });
}
