$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$progrezPython = if (Test-Path -LiteralPath '.venv\Scripts\python.exe') { Join-Path $PSScriptRoot '.venv\Scripts\python.exe' } else { 'python' }
& $progrezPython manage.py migrate
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
& $progrezPython manage.py seed_demo
if ($LASTEXITCODE -ne 0) { throw 'Seeding failed.' }
& $progrezPython manage.py backfill_circle
if ($LASTEXITCODE -ne 0) { throw 'Circle preparation failed.' }
Write-Host 'Progrez is starting at http://127.0.0.1:8000/'
& $progrezPython manage.py runserver 127.0.0.1:8000
