$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $PSScriptRoot
$stateDir = Join-Path $PSScriptRoot '.state'
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
# Build first, publish only when the build succeeds (see pipeline/daily.py).
$process = Start-Process -FilePath $pythonPath -ArgumentList '-X utf8 -m pipeline.daily' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $stateDir 'daily.log') -RedirectStandardError (Join-Path $stateDir 'daily-error.log')
$line = '{0}  exit={1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $process.ExitCode
Add-Content -LiteralPath (Join-Path $stateDir 'history.log') -Value $line -Encoding ascii
exit $process.ExitCode
