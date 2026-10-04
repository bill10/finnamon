// Runs before the stylesheet paints: the saved theme, else the system's. In its own file because the page's CSP allows no inline script.
try { document.documentElement.setAttribute('data-theme', localStorage.getItem('theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')); } catch (e) {}
