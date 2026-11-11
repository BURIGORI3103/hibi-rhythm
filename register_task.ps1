$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskName = 'HibiRhythm-Daily'
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    throw 'Task already exists. Inspect the existing task before registering.'
}
$scriptPath = Join-Path $PSScriptRoot 'run_daily.ps1'
$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $scriptPath + '"'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $PSScriptRoot
# 13:30 JST: after the 13:00 sleep import into Notion has finished.
$trigger = New-ScheduledTaskTrigger -Daily -At '13:30'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10) -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'HibiRhythm: rebuild the encrypted site data from Notion sleep records and ActivityWatch, then publish to gh-pages.' | Select-Object TaskName,State
