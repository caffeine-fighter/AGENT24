[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$SiteRoot = Join-Path $RepoRoot "site"

if (-not (Test-Path -LiteralPath (Join-Path $SiteRoot "node_modules/@playwright/test"))) {
    Write-Error "VERIFY browser FAIL: Playwright is not installed. Recover: run npm --prefix site ci --engine-strict."
    exit 1
}

Push-Location $SiteRoot
try {
    $Arguments = @("exec", "--", "playwright", "install", "chromium")
    if ($IsLinux -and $env:CI) {
        $Arguments = @("exec", "--", "playwright", "install", "--with-deps", "chromium")
    }
    & npm @Arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Error "VERIFY browser FAIL: Chromium installation failed. Recover: rerun ./scripts/install-browser.ps1 after npm ci."
        exit 1
    }
}
finally {
    Pop-Location
}
