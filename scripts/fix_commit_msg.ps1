# Used as GIT_EDITOR during rebase to fix garbled commit messages (UTF-8).
# Usage: GIT_EDITOR="powershell -ExecutionPolicy Bypass -File scripts/fix_commit_msg.ps1" git rebase -i ...
$msgPath = $args[0]
$hash = (git rev-parse HEAD).Trim()

$messages = @{
  "34b7386b107f04254c51e21e6044fbe4f3a62e59" = "chore: 初始化 PanelLab 项目结构"
  "75935727d0f3f5057fd241f6312b52aeb71c6ba7" = "docs: 添加 GitHub 仓库关联说明"
  "eaaee8a570e9ee0d12edb2050d7c40c9d0f5306f" = "chore: 从 Git 中移除 backend 虚拟环境并更新 .gitignore"
}

if ($messages.ContainsKey($hash)) {
  $utf8NoBom = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($msgPath, $messages[$hash], $utf8NoBom)
}
# else leave file unchanged (script could copy existing content; for reword, git already put old message in file)
exit 0
