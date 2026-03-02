# 静态资源说明

- **panellab-logo.svg**：登录页默认使用本目录下的矢量 Logo。若改用莫比乌斯环 PNG，请将 `assets/panellab-logo-mobius.png` 复制到本目录并重命名为 `panellab-logo.png`，同时把 `Login.vue` 中的 `src="/panellab-logo.svg"` 改为 `src="/panellab-logo.png"`。图片加载失败时登录页会显示「P」占位。
