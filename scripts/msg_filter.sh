#!/bin/sh
# Rewrite garbled commit messages to UTF-8. Used by git filter-branch --msg-filter.
case "$GIT_COMMIT" in
34b7386b107f04254c51e21e6044fbe4f3a62e59) printf '%s\n' "chore: 初始化 PanelLab 项目结构" ;;
75935727d0f3f5057fd241f6312b52aeb71c6ba7) printf '%s\n' "docs: 添加 GitHub 仓库关联说明" ;;
eaaee8a570e9ee0d12edb2050d7c40c9d0f5306f) printf '%s\n' "chore: 从 Git 中移除 backend 虚拟环境并更新 .gitignore" ;;
*) cat ;;
esac
