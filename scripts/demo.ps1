[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [switch]$OpenBrowser,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$DemoUrl = "http://${HostAddress}:$Port"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv가 필요합니다. https://docs.astral.sh/uv/ 에서 설치한 뒤 다시 실행하세요."
}

Push-Location $RepoRoot
try {
    $Mode = if ($env:OPENAI_API_KEY -or (Test-Path (Join-Path $RepoRoot ".env"))) {
        "live 설정 확인 필요"
    }
    else {
        "offline_demo"
    }

    Write-Host "NIGHTMARE LAB 데모를 시작합니다." -ForegroundColor Magenta
    Write-Host "URL  : $DemoUrl"
    Write-Host "MODE : $Mode"
    Write-Host "AGENT: ExampleCakeAgent (reviewed local bundle)"
    Write-Host "종료하려면 Ctrl+C를 누르세요."

    if ($OpenBrowser) {
        $null = Start-Job -ScriptBlock {
            param($Url)
            Start-Sleep -Milliseconds 900
            Start-Process $Url
        } -ArgumentList $DemoUrl
    }

    if ($Reload) {
        Write-Warning "ExampleCakeAgent 고정 bundle launcher에서는 -Reload를 사용하지 않습니다."
    }

    $Arguments = @(
        "run",
        "python",
        "scripts/demo-local.py",
        "--host",
        $HostAddress,
        "--port",
        "$Port"
    )

    & uv @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "데모 서버가 exit code $LASTEXITCODE 로 종료되었습니다."
    }
}
finally {
    Pop-Location
}
