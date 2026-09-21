param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^\d{2}:\d{2}$")]
    [string]$DailyAt,

    [string]$TaskName = "Crypto Trading Bot Daily Analysis"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$RunnerPath = Join-Path $ScriptDir "run_ai_trade_analysis.ps1"
$PowerShellExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

if (-not (Test-Path $RunnerPath)) {
    throw "Analysis runner was not found. path=$RunnerPath"
}

$RunTime = [datetime]::ParseExact(
    $DailyAt,
    "HH:mm",
    [System.Globalization.CultureInfo]::InvariantCulture
)

$ActionArguments = @(
    "-NoProfile"
    "-NonInteractive"
    "-ExecutionPolicy Bypass"
    "-File `"$RunnerPath`""
) -join " "

$Action = New-ScheduledTaskAction `
    -Execute $PowerShellExe `
    -Argument $ActionArguments `
    -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At $RunTime

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$Principal = New-ScheduledTaskPrincipal `
    -UserId $CurrentUser `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Description "Collect market/account data, generate AI trade recommendations, and send Telegram notifications." `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Force | Out-Null

Write-Host "Windows scheduled task registered successfully."
Write-Host "Task name: $TaskName"
Write-Host "Daily time: $DailyAt"
Write-Host "Runner: $RunnerPath"
Write-Host "User: $CurrentUser"