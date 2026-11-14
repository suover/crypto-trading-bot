param([switch]$SelfTest)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $javaExecutable = if ($env:JAVA_HOME) { Join-Path $env:JAVA_HOME 'bin/java.exe' } else { (Get-Command java -ErrorAction Stop).Source }
    $cryptoClasspath = & .\gradlew.bat --no-daemon --console=plain -q adminHashClasspath
    if ($LASTEXITCODE -ne 0) { throw 'Gradle classpath resolution failed.' }
    $cryptoJar = @($cryptoClasspath | Where-Object { $_ -match 'spring-security-crypto-.*\.jar$' })
    if ($cryptoJar.Count -ne 1 -or -not (Test-Path -LiteralPath $cryptoJar[0])) { throw 'BCrypt library could not be located.' }
    $toolArguments = @('--class-path', $cryptoJar[0], 'tools/CreateAdminHash.java')
    if ($SelfTest) { $toolArguments += '--self-test' }
    & $javaExecutable @toolArguments
    if ($LASTEXITCODE -ne 0) { throw 'Password hash generation failed.' }
} finally {
    Pop-Location
}
