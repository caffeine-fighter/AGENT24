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

    # Eval registry gate (issue #107). Validation and target-existence are
    # unconditional: a duplicate id, an unknown field, or a pytest node that no
    # longer collects is drift and must fail here rather than stay green.
    #
    # --validate-only, not a full execution: every pytest target the registry
    # names was already run by the `pytest -q` step above, so executing them a
    # second time would double the gate's runtime to prove the same thing. Run
    # `python -m agent24.evals` directly to execute the cases themselves.
    python -m agent24.evals --validate-only
    Assert-CommandSucceeded "Eval registry"

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
