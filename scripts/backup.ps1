param([string]$Compose = "docker")
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectPath
$backupPath = Join-Path $projectPath 'backups'
New-Item -ItemType Directory -Path $backupPath -Force | Out-Null
$backupName = 'arcana-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.dump'
& $Compose compose exec -T db pg_dump -U arcana -d arcana -Fc -f "/backups/$backupName"
if ($LASTEXITCODE -ne 0) { throw 'Backup failed' }
# Keep daily snapshots for seven days. This deletes only named backup files in this folder.
$cutoff = (Get-Date).AddDays(-7)
Get-ChildItem -LiteralPath $backupPath -File -Filter 'arcana-*.dump' | Where-Object LastWriteTime -lt $cutoff | ForEach-Object {
    if (-not $_.FullName.StartsWith($backupPath + '\')) { throw 'Unsafe backup path' }
    Remove-Item -LiteralPath $_.FullName
}
Write-Output "Backup saved: backups/$backupName. Keep runtime/deletions.log independently of snapshots."
