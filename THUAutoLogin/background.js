const POLL_TIMEOUT_MS = 30000;

chrome.runtime.onMessage.addListener((msg, sender) => {
  if (!msg || msg.type !== 'callDoLogin' || !sender.tab) return;

  chrome.scripting.executeScript({
    target: { tabId: sender.tab.id, frameIds: [sender.frameId ?? 0] },
    world: 'MAIN',
    args: [POLL_TIMEOUT_MS],
    func: (timeoutMs) => {
      const FLAG = '__thuAutoLoginPoller';
      if (window[FLAG]) return;
      window[FLAG] = true;

      const startedAt = Date.now();
      const timer = setInterval(() => {
        if (typeof doLogin !== 'function') {
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
  });
});
