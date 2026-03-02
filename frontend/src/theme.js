const STORAGE_KEY = 'panel_theme'

export function getTheme() {
  return localStorage.getItem(STORAGE_KEY) || 'light'
}

export function setTheme(theme) {
  if (theme !== 'light' && theme !== 'dark') return
  localStorage.setItem(STORAGE_KEY, theme)
  document.documentElement.setAttribute('data-theme', theme)
}

export function initTheme() {
  const theme = getTheme()
  document.documentElement.setAttribute('data-theme', theme)
}

export function toggleTheme() {
  const next = getTheme() === 'light' ? 'dark' : 'light'
  setTheme(next)
  return next
}
