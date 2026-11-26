$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
flutter build windows --debug --dart-define=BN_V3_WRITE_STATUS=true
$runner = (Resolve-Path 'build/windows/x64/runner/Debug').Path
$result = Join-Path $runner 'runtime-result.txt'
$exe = Join-Path $runner 'platform_proof.exe'

foreach ($expected in @(
    'PASS: durable sale saved; relaunch to verify persistence',
    'PASS: persisted sale verified after relaunch'
)) {
    if (Test-Path $result) { Remove-Item $result }
    $process = Start-Process -FilePath $exe -WorkingDirectory $runner -PassThru
    try {
        for ($index = 0; $index -lt 60; $index++) {
            if (Test-Path $result) {
                $actual = (Get-Content $result -Raw)
                if ($actual) { break }
            }
            if ($process.HasExited) { throw "Flutter exited before emitting proof" }
            Start-Sleep -Seconds 1
        }
        if (!(Test-Path $result)) { throw "Flutter runtime result timed out" }
        $actual = Get-Content $result -Raw
        Write-Output $actual
        if ($actual -ne $expected) { throw "Expected '$expected', got '$actual'" }
    } finally {
        if (!$process.HasExited) { Stop-Process -Id $process.Id }
        $process.WaitForExit()
        $process.Dispose()
    }
}
