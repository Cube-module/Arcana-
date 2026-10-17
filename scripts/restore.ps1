param([Parameter(Mandatory=$true)][string]$BackupFile)
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectPath
if ($BackupFile -notmatch '^arcana-[0-9]{8}-[0-9]{6}\.dump$') { throw 'Pass a backup filename from backups, without a path' }
if (-not (Test-Path -LiteralPath (Join-Path $projectPath 'runtime/deletions.log'))) { throw 'Current deletion registry is required' }
docker compose stop app
docker compose exec -T db pg_restore -U arcana -d arcana --clean --if-exists --single-transaction "/backups/$BackupFile"
if ($LASTEXITCODE -ne 0) { throw 'Restore failed; keep app stopped' }
docker compose run --rm app python -m app.cli apply-deletions
if ($LASTEXITCODE -ne 0) { throw 'Deletion replay failed; keep app stopped' }
docker compose run --rm app python -m app.cli check
if ($LASTEXITCODE -ne 0) { throw 'Startup check failed; keep app stopped' }
Write-Output 'Restore checked. Start app with: docker compose up -d app'
