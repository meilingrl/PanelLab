# One-time script: remove backend venv paths from git index (paths with arrow or backtick)
$root = (Get-Item $PSScriptRoot).Parent.FullName
Set-Location $root
$lines = git ls-files | Where-Object { $_ -match "342|\.venv|venv" -and $_ -match "backend" -and $_ -notmatch "requirements\.txt" }
foreach ($line in $lines) {
    $path = $line.Trim('"')
    & git rm --cached $path 2>&1 | Out-Null
}
Write-Host "Done. Removed $($lines.Count) paths."
