param([Parameter(Mandatory)][ValidateRange(1,[long]::MaxValue)][long]$TradingUserId)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $javaExecutable = if ($env:JAVA_HOME) { Join-Path $env:JAVA_HOME 'bin/java.exe' } else { (Get-Command java).Source }
    $classpath = & .\gradlew.bat --no-daemon --console=plain -q dashboardAccountClasspath
    if ($LASTEXITCODE -ne 0 -or @($classpath).Count -ne 1) { throw 'Gradle classpath resolution failed.' }
    & $javaExecutable --class-path $classpath 'tools/CreateDashboardAccount.java' $TradingUserId
    if ($LASTEXITCODE -ne 0) { throw 'Dashboard account was not created.' }
} finally {
    Pop-Location
}
