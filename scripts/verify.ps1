[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$OriginalPythonPath = $env:PYTHONPATH

function Assert-CommandSucceeded {
    param([string]$Step)

    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

Push-Location $RepoRoot
try {
    $env:PYTHONPATH = if ($OriginalPythonPath) {
        "$(Join-Path $RepoRoot 'src');$OriginalPythonPath"
    }
    else {
        Join-Path $RepoRoot "src"
    }

    python -m compileall -q src tests scripts
    Assert-CommandSucceeded "Python compilation"

    if (Get-Command ruff -ErrorAction SilentlyContinue) {
        ruff check src tests scripts
        Assert-CommandSucceeded "Ruff"
    }

    if (Get-Command pytest -ErrorAction SilentlyContinue) {
        python -m pytest -q
        Assert-CommandSucceeded "Pytest"
    }

    if (Get-Command node -ErrorAction SilentlyContinue) {
        node web/tests/core.test.mjs
        Assert-CommandSucceeded "Web reducer"
    }

    $TrackedSecretFiles = git ls-files | Where-Object {
        (($_ -match '(^|/)\.env($|\.)') -and ($_ -notmatch '(^|/)\.env\.example$')) -or
        ($_ -match '\.pem$|\.key$')
    }
    if ($TrackedSecretFiles) {
        throw "Potential secret file is tracked: $TrackedSecretFiles"
    }

    Write-Host "Verification complete." -ForegroundColor Green
}
finally {
    $env:PYTHONPATH = $OriginalPythonPath
    Pop-Location
}
