[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ArtifactDirectory = Join-Path $RepoRoot "artifacts"
$BrowserSummary = Join-Path $ArtifactDirectory "browser-smoke-summary.json"
$OriginalPythonPath = $env:PYTHONPATH
$CompletedGates = [System.Collections.Generic.List[string]]::new()

function Write-BrowserFailure {
    param(
        [Parameter(Mandatory = $true)][string]$CaseId,
        [Parameter(Mandatory = $true)][string]$Code
    )

    New-Item -ItemType Directory -Force -Path $ArtifactDirectory | Out-Null
    $Summary = [ordered]@{
        schema = "agent24.browser-smoke.v1"
        browser = "chromium"
        passed = $false
        cases = @()
        failure = [ordered]@{
            case_id = $CaseId
            code = $Code
        }
    }
    $Summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $BrowserSummary -Encoding utf8
}

function Assert-LastExitCode {
    param(
        [Parameter(Mandatory = $true)][string]$Gate,
        [Parameter(Mandatory = $true)][string]$Recovery
    )

    if ($LASTEXITCODE -ne 0) {
        throw "VERIFY $Gate FAIL: command exited with code $LASTEXITCODE. Recover: $Recovery"
    }
}

function Assert-CommandAvailable {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Recovery
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "VERIFY prerequisites FAIL: required command '$Name' is missing. Recover: $Recovery"
    }
}

function Invoke-Gate {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )

    Write-Host "VERIFY $Name START"
    & $Action
    $CompletedGates.Add($Name)
    Write-Host "VERIFY $Name PASS"
}

if (Test-Path -LiteralPath $BrowserSummary) {
    Remove-Item -LiteralPath $BrowserSummary -Force
}
Push-Location $RepoRoot
try {
    Invoke-Gate "prerequisites" {
        if ($PSVersionTable.PSVersion.Major -lt 7) {
            throw "VERIFY prerequisites FAIL: PowerShell 7 or newer is required. Recover: install PowerShell 7 and run pwsh -File ./scripts/verify.ps1."
        }
        Assert-CommandAvailable "git" "install Git and rerun from the repository root."
        Assert-CommandAvailable "python" "install Python 3.11+ and run uv sync --extra dev --frozen."
        Assert-CommandAvailable "uv" "install uv and run uv sync --extra dev --frozen."
        Assert-CommandAvailable "node" "install Node.js 22.13.0+ and run npm --prefix site ci --engine-strict."
        Assert-CommandAvailable "npm" "install npm with Node.js 22.13.0+ and run npm --prefix site ci --engine-strict."

        git rev-parse --show-toplevel | Out-Null
        Assert-LastExitCode "prerequisites" "run the command from a Git checkout."
        python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
        Assert-LastExitCode "prerequisites" "install Python 3.11 or newer."
        node -e "const [a,b,c]=process.versions.node.split('.').map(Number); process.exit(a>22 || (a===22 && (b>13 || (b===13 && c>=0))) ? 0 : 1)"
        Assert-LastExitCode "prerequisites" "install Node.js 22.13.0 or newer."
    }

    Invoke-Gate "python" {
        uv sync --extra dev --frozen
        Assert-LastExitCode "python" "run uv sync --extra dev --frozen and resolve the locked dependency error."
        $env:PYTHONPATH = Join-Path $RepoRoot "src"
        uv run --frozen python -m compileall -q src tests scripts
        Assert-LastExitCode "python" "fix the reported Python compilation error."
        uv run --frozen ruff check src tests scripts
        Assert-LastExitCode "python" "fix Ruff findings after uv sync --extra dev --frozen."
        uv run --frozen pytest -q
        Assert-LastExitCode "python" "fix the failing pytest case after uv sync --extra dev --frozen."
    }

    Invoke-Gate "canonical_web" {
        node web/tests/core.test.mjs
        Assert-LastExitCode "canonical_web" "fix the canonical reducer contract reported by Node."
        node --test scripts/tests/verification.test.mjs
        Assert-LastExitCode "canonical_web" "fix the verification contract test reported by Node."
    }

    Invoke-Gate "eval_registry" {
        # Validation and target-existence are unconditional: a duplicate id,
        # an unknown field, or a pytest node that no longer collects is drift.
        uv run --frozen python -m agent24.evals --validate-only
        Assert-LastExitCode "eval_registry" "fix the eval registry drift reported by agent24.evals."
    }

    Invoke-Gate "hosted_site" {
        Push-Location (Join-Path $RepoRoot "site")
        try {
            npm ci --engine-strict
            Assert-LastExitCode "hosted_site" "run npm --prefix site ci --engine-strict and resolve the lockfile or engine error."
            npm audit --audit-level=high
            Assert-LastExitCode "hosted_site" "resolve high-or-greater npm audit findings without weakening the threshold."
            npm run typecheck
            Assert-LastExitCode "hosted_site" "fix the hosted TypeScript errors."
            npm run lint
            Assert-LastExitCode "hosted_site" "fix the hosted ESLint errors."
            npm run build
            Assert-LastExitCode "hosted_site" "fix the hosted production build."
            node --test tests/rendered-html.test.mjs
            Assert-LastExitCode "hosted_site" "fix the hosted route contract tests."
        }
        finally {
            Pop-Location
    }
    }

    Invoke-Gate "drift" {
        node scripts/verify-web-site-drift.mjs
        Assert-LastExitCode "drift" "synchronize the shared web/site contract or document a narrowly allowed adapter."
    }

    Invoke-Gate "browser" {
        # This fallback exists only while browser dependencies or server startup are unresolved.
        # Once Playwright starts, canonical-flow.spec.mjs replaces it with observed case evidence.
        Write-BrowserFailure "prerequisites" "dependency_install_failed"
        & (Join-Path $RepoRoot "scripts/install-browser.ps1")
        Assert-LastExitCode "browser" "run ./scripts/install-browser.ps1 after npm --prefix site ci --engine-strict."
        Push-Location (Join-Path $RepoRoot "site")
        try {
            npm run test:browser
            Assert-LastExitCode "browser" "fix the normal, unsupported, or explicit fallback browser contract."
        }
        finally {
            Pop-Location
        }
        node scripts/validate-browser-summary.mjs
        if ($LASTEXITCODE -ne 0) {
            Write-BrowserFailure "artifact_export" "artifact_policy_failed"
            throw "VERIFY browser FAIL: browser summary violates the closed schema. Recover: regenerate the sanitized browser summary with the repository test runner."
        }
    }

    Invoke-Gate "hygiene" {
        $TrackedSecretFiles = @(git ls-files | Where-Object {
            (($_ -match '(^|/)\.env($|\.)') -and ($_ -notmatch '(^|/)\.env\.example$')) -or
            ($_ -match '\.pem$|\.key$|\.log$') -or
            ($_ -match '(^|/)artifacts/.+\.(json|jsonl|har|zip|png|jpe?g|webp|mp4|webm)$')
        })
        Assert-LastExitCode "hygiene" "restore Git metadata and rerun from the repository root."
        if ($TrackedSecretFiles.Count -gt 0) {
            throw "VERIFY hygiene FAIL: a forbidden secret, log, or generated artifact is tracked. Recover: remove it from Git and keep only the documented example or ignore file."
        }
        node scripts/validate-browser-summary.mjs
        if ($LASTEXITCODE -ne 0) {
            Write-BrowserFailure "artifact_export" "artifact_policy_failed"
            throw "VERIFY hygiene FAIL: browser artifact policy failed. Recover: remove unapproved browser artifact fields or files and rerun the browser gate."
        }
    }

    $GateList = [string]::Join(",", $CompletedGates)
    Write-Host "VERIFY COMPLETE PASS: gates=$GateList; skipped=0"
}
catch {
    $Message = $_.Exception.Message
    if ($Message -notmatch '^VERIFY [a-z_]+ FAIL:') {
        $ActiveGate = if ($CompletedGates.Count -eq 0) { "prerequisites" } else { "pipeline" }
        $Message = "VERIFY $ActiveGate FAIL: an unexpected verification error occurred. Recover: inspect the immediately preceding fixed-command output and rerun ./scripts/verify.ps1."
    }
    Write-Error $Message
    exit 1
}
finally {
    $env:PYTHONPATH = $OriginalPythonPath
    Pop-Location
}
