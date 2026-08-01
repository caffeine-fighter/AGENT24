[CmdletBinding()]
param(
    [string]$Message = "chore: scaffold AGENT:24 project"
)

$ErrorActionPreference = "Stop"
$KoreaTimeZone = [TimeZoneInfo]::FindSystemTimeZoneById("Korea Standard Time")
$NowKst = [TimeZoneInfo]::ConvertTime([DateTimeOffset]::UtcNow, $KoreaTimeZone)
$CodingStartKst = [DateTimeOffset]::new(2026, 8, 1, 14, 0, 0, [TimeSpan]::FromHours(9))

if ($NowKst -lt $CodingStartKst) {
    throw "Commit blocked until 2026-08-01 14:00 KST. Current KST: $($NowKst.ToString('yyyy-MM-dd HH:mm:ss zzz'))"
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not (Test-Path (Join-Path $RepoRoot ".git"))) {
    throw "Git repository not found at $RepoRoot"
}

& (Join-Path $PSScriptRoot "verify.ps1")
git -C $RepoRoot add -A
git -C $RepoRoot commit -m $Message

Write-Host "First commit created locally. Add a remote and push only when the team is ready." -ForegroundColor Green

