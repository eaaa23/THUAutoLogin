(() => {
  const POLL_MS = 300;
  const RETRY_AFTER_MS = 3000;
  const MAX_CLICKS = 3;

  const click = (el) => el.click();

  const askPageToLogin = () => chrome.runtime.sendMessage({ type: 'callDoLogin' });

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
      match: (p) => p.startsWith('/do/off/ui/auth/login/form'),
      act: askPageToLogin,
    },
  ];

  let clicks = 0;
  let lastClickAt = 0;
  let lastPath = null;

  const currentPath = () => location.pathname.replace(/\/+$/, '') || '/';

  function tick() {
    const path = currentPath();
    if (path !== lastPath) {
      lastPath = path;
      clicks = 0;
    }

    const rule = RULES.find((r) => r.match(path));
    if (!rule) return;

    const el = rule.find ? rule.find() : null;
    if (rule.find && (!el || el.disabled)) return;

    const now = Date.now();
    if (clicks > 0 && now - lastClickAt < RETRY_AFTER_MS) return;
    if (clicks >= MAX_CLICKS) return;

    clicks += 1;
    lastClickAt = now;
    (rule.act || click)(el);
  }

  setInterval(tick, POLL_MS);
  tick();
})();
