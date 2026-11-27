// Runs before first paint (loaded synchronously from <head>) so there is no light/dark flash.
// Same-origin file rather than an inline script: the CSP is `default-src 'self'`.
// Keep in sync with src/lib/theme.ts (storage key, values, resolution).
;(function () {
  var pref = 'system'
  try {
    var stored = localStorage.getItem('nexus-theme')
    if (stored === 'light' || stored === 'dark' || stored === 'system') pref = stored
  } catch (e) {}
  var dark =
    pref === 'dark' || (pref === 'system' && matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
})()
