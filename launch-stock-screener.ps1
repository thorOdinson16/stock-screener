$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

try {
    docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        $desktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
        if (-not (Test-Path -LiteralPath $desktop)) { throw 'Docker Desktop was not found.' }
        Start-Process -FilePath $desktop -WindowStyle Hidden
        $deadline = (Get-Date).AddMinutes(5)
        do {
            Start-Sleep -Seconds 5
            docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
        } while ($LASTEXITCODE -ne 0 -and (Get-Date) -lt $deadline)
        if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop did not become ready within five minutes.' }
    }

    docker compose up -d
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose could not start the stack.' }
    Start-Process 'http://localhost:5173/'
} catch {
    Write-Host "Unable to start Stock Screener: $_" -ForegroundColor Red
    Read-Host 'Press Enter to close'
}
