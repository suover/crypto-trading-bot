$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$LogDir = Join-Path $ProjectRoot "logs"

New-Item -ItemType Directory -Force $LogDir | Out-Null

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $LogDir "ai_trade_analysis_$Timestamp.log"

Set-Location $ProjectRoot

# Windows PowerShell과 Python의 입출력을 UTF-8로 통일
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"

chcp 65001 | Out-Null


function Write-Log {
    param(
        [AllowEmptyString()]
        [string]$Message
    )

    Write-Host $Message
    Add-Content -Path $LogFile -Value $Message -Encoding UTF8
}


try {
    Write-Log "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] START AI trade analysis"

    $PreviousErrorActionPreference = $ErrorActionPreference

    try {
        $ErrorActionPreference = "Continue"

        & uv run python -X utf8 -u -m scripts.run_ai_trade_analysis 2>&1 |
            ForEach-Object {
                Write-Log ([string]$_)
            }

        $ExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }

    Write-Log "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] EXIT_CODE=$ExitCode"

    if ($ExitCode -ne 0) {
        throw "AI trade analysis failed. exitCode=$ExitCode"
    }

    Write-Log "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] SUCCESS AI trade analysis"

    exit 0
}
catch {
    Write-Log "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] FAILED AI trade analysis"
    Write-Log $_.Exception.Message

    exit 1
}