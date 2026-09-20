(() => {
  const POLL_MS = 300;
  const RETRY_AFTER_MS = 3000;
  const MAX_ATTEMPTS = 3;

  const click = (el) => el.click();

  // Guards against overlapping native round-trips for the same page.
  let busy = false;

  const isFocused = () =>
    document.hasFocus() && document.visibilityState === 'visible';

  // getClientRects() is empty for display:none and for zero-size elements, and
  // unlike offsetParent it still works for position:fixed nodes.
  const isVisible = (el) => el.getClientRects().length > 0;

  const askBackgroundToLogin = () => {
    if (busy) return Promise.resolve({ ok: false, reason: 'busy' });
    busy = true;
    return chrome.runtime
      .sendMessage({ type: 'loginForm', url: location.href })
      .catch((e) => ({ ok: false, reason: String((e && e.message) || e) }))
      .finally(() => {
        busy = false;
      });
  };

  const RULES = [
    {
      match: (p) => p === '/f/login',
      find: () => document.getElementById('loginButtonId'),
    },
    {
      match: (p) => p === '/f/wlxt/index/course/student',
      find: () => document.querySelector('.chongxin'),
    },
    {
      // Needs the native key agent: Chrome hides autofilled values from JS
      // until a real (OS-level) key event reaches the page.
      match: (p) => p.startsWith('/do/off/ui/auth/login/form'),
      act: askBackgroundToLogin,
      requireFocus: true,
    },
    {
      // Information portal. When the user is not logged in the header renders
      // the 登录 control as <span class="dehmil">. Its mere presence means
      // "logged out", so no separate login-state probe is needed. There are two
      // such spans (desktop + mobile layouts); click whichever is visible.
      match: (p, host) => host === 'info.tsinghua.edu.cn',
      find: () => {
        const candidates = Array.from(document.querySelectorAll('.dehmil'));
        return candidates.find(isVisible) || null;
      },
    },
  ];

  let attempts = 0;
  let lastAttemptAt = 0;
  let lastPageKey = null;
  let done = false;

  const currentPath = () => location.pathname.replace(/\/+$/, '') || '/';

  function tick() {
    const host = location.hostname;
    const path = currentPath();
    const pageKey = `${host}${path}`;
    if (pageKey !== lastPageKey) {
      lastPageKey = pageKey;
      attempts = 0;
      lastAttemptAt = 0;
      done = false;
    }
    if (done) return;

    const rule = RULES.find((r) => r.match(path, host));
    if (!rule) return;

    // Only drive the native key agent while this document really has the
    // user's focus, otherwise synthetic keystrokes would land elsewhere.
    if (rule.requireFocus && !isFocused()) return;

    const el = rule.find ? rule.find() : null;
    if (rule.find && (!el || el.disabled)) return;

    // Throttle independently of the attempt counter, so regaining focus cannot
    // be used to hammer the native host.
    const now = Date.now();
    if (lastAttemptAt && now - lastAttemptAt < RETRY_AFTER_MS) return;
    if (attempts >= MAX_ATTEMPTS) return;

    attempts += 1;
    lastAttemptAt = now;

    const result = (rule.act || click)(el);
    if (result && typeof result.then === 'function') {
      result.then((r) => {
        if (r && r.ok) done = true;
      });
    }
  }

  // Coming back to the tab grants a fresh set of attempts: attempts burned
  // while the window was losing focus must not permanently disable the rule.
  const onFocusRegained = () => {
    attempts = 0;
    tick();
  };
  window.addEventListener('focus', onFocusRegained);
  document.addEventListener('visibilitychange', () => {
    if (isFocused()) onFocusRegained();
  });

  setInterval(tick, POLL_MS);
  tick();
})();
