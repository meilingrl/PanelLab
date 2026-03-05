# GIT_SEQUENCE_EDITOR: change "pick" to "reword" for the three commits we want to fix.
$todoPath = $args[0]
$content = Get-Content $todoPath -Raw
$content = $content -replace '^pick 34b7386 ', 'reword 34b7386 '
$content = $content -replace '^pick 7593572 ', 'reword 7593572 '
$content = $content -replace '^pick eaaee8a ', 'reword eaaee8a '
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($todoPath, $content, $utf8NoBom)
exit 0
