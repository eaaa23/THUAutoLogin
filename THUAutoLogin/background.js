const POLL_TIMEOUT_MS = 300000;
const DEBUGGER_VERSION = '1.3';

const runInPage = (tabId, frameId, func, args) =>
  chrome.scripting
    .executeScript({ target: { tabId, frameIds: [frameId] }, world: 'MAIN', func, args })
    .then(([result]) => (result ? result.result : undefined));

const areFieldsFilled = (tabId, frameId) =>
  runInPage(tabId, frameId, () => {
    const user = document.getElementById('i_user');
    const pass = document.getElementById('i_pass');
    return Boolean(user && user.value && pass && pass.value);
  });

// Chrome hides autofilled values from JavaScript until a trusted input event arrives,
// so we send a real (CDP-injected) Enter to unlock them and to trigger the page's keyLogin().
const injectTrustedEnter = async (tabId, frameId) => {
  const target = { tabId };
  let attached = false;
  try {
    await chrome.debugger.attach(target, DEBUGGER_VERSION);
    attached = true;
    await runInPage(tabId, frameId, () => {
      const pass = document.getElementById('i_pass');
      if (pass) pass.focus();
    });
    const key = { key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 };
    await chrome.debugger.sendCommand(target, 'Input.dispatchKeyEvent', { type: 'keyDown', ...key });
    await chrome.debugger.sendCommand(target, 'Input.dispatchKeyEvent', { type: 'keyUp', ...key });
  } catch (e) {
    // e.g. DevTools already attached, or the tab navigated away: fall back to waiting
  } finally {
    if (attached) await chrome.debugger.detach(target).catch(() => {});
  }
};

const waitForCredentialsThenLogin = (tabId, frameId) =>
  runInPage(
    tabId,
    frameId,
    (timeoutMs) => {
      const FLAG = '__thuAutoLoginPoller';
      if (window[FLAG]) return;
      window[FLAG] = true;

      const startedAt = Date.now();
      const timer = setInterval(() => {
        const user = document.getElementById('i_user');
        const pass = document.getElementById('i_pass');
        const ready = typeof doLogin === 'function' && user && user.value && pass && pass.value;
        if (!ready) {
          if (Date.now() - startedAt > timeoutMs) {
            clearInterval(timer);
            window[FLAG] = false;
          }
          return;
        }
        clearInterval(timer);
        doLogin();
      }, 300);
    },
    [POLL_TIMEOUT_MS]
  );

const isAlreadyHandling = (tabId, frameId) =>
  runInPage(tabId, frameId, () => Boolean(window.__thuAutoLoginPoller));

const handleLoginPage = async (tabId, frameId) => {
  if (await isAlreadyHandling(tabId, frameId)) return;
  if (!(await areFieldsFilled(tabId, frameId))) {
    await injectTrustedEnter(tabId, frameId);
  }
  await waitForCredentialsThenLogin(tabId, frameId);
};

chrome.runtime.onMessage.addListener((msg, sender) => {
  if (!msg || msg.type !== 'callDoLogin' || !sender.tab) return;
  handleLoginPage(sender.tab.id, sender.frameId ?? 0);
});
