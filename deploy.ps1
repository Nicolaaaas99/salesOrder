<#
.SYNOPSIS
  Deploy SalesOrder on the server. The ONLY supported way to deploy.

.DESCRIPTION
  Run on the server from an elevated PowerShell:

      powershell -ExecutionPolicy Bypass -File C:\inetpub\salesOrder\deploy.ps1

  Unlike typing the commands by hand, this stops at the FIRST failure and
  says why. The old way kept going after a failed `git pull`, rebuilt the
  OLD code and brought the site back as if nothing was wrong.

  What it does, in order:
    1. Fetches the latest commit from GitHub and checks nobody has edited
       tracked files on the server. Nothing is stopped yet, so a problem
       here costs no downtime.
    2. Stops the IIS site + app pool.
    3. Moves the code to exactly origin/<branch> and verifies it.
    4. Runs `npm ci` only when package-lock.json changed (or node_modules
       is missing), then `npm run build`.
    5. Checks the build output (index.html, web.config, version.json) and
       that version.json carries the commit just pulled.
    6. Starts the app pool + site and prints the commit that is now live.

  If anything in 3-5 fails, it puts the previous code and build back and
  restarts the site, so a failed deploy leaves the old version running
  instead of taking the site down.

  backend\.env, backend\start_backend.bat and backend\logs are not in git,
  so they are never touched.
#>
[CmdletBinding()]
param(
    [string]$RepoPath = 'C:\inetpub\salesOrder',
    [string]$Site     = 'SalesOrder',
    [string]$AppPool  = 'SalesOrderPool',
    [string]$Branch   = 'main'
)

$ErrorActionPreference = 'Stop'
Import-Module WebAdministration

function Step([string]$text) { Write-Host "`n==> $text" -ForegroundColor Cyan }

# Native commands (git, npm) don't throw on failure in PowerShell; check the
# exit code ourselves so a failure can never be silently skipped again.
function Invoke-Native([string]$what, [scriptblock]$cmd) {
    $global:LASTEXITCODE = 0
    $out = & $cmd
    if ($LASTEXITCODE -ne 0) { throw "FAILED: $what (exit code $LASTEXITCODE)" }
    return $out
}

function Stop-App {
    if ((Get-WebsiteState -Name $Site).Value -ne 'Stopped') { Stop-WebSite -Name $Site }
    if ((Get-WebAppPoolState -Name $AppPool).Value -ne 'Stopped') { Stop-WebAppPool -Name $AppPool }
    # Wait for the pool to really stop so files aren't locked mid-build.
    for ($i = 0; $i -lt 30 -and (Get-WebAppPoolState -Name $AppPool).Value -ne 'Stopped'; $i++) {
        Start-Sleep -Seconds 1
    }
}

function Start-App {
    if ((Get-WebAppPoolState -Name $AppPool).Value -ne 'Started') { Start-WebAppPool -Name $AppPool }
    if ((Get-WebsiteState -Name $Site).Value -ne 'Started') { Start-WebSite -Name $Site }
}

Set-Location $RepoPath
$frontend = Join-Path $RepoPath 'frontend'
$dist     = Join-Path $frontend 'dist'
$distPrev = Join-Path $frontend 'dist.previous'

# ---------------------------------------------------------------------------
# 1. Pre-flight (site still running)
# ---------------------------------------------------------------------------
Step "Fetching origin/$Branch"
Invoke-Native 'git fetch' { git fetch origin $Branch } | Out-Null

# Older commits tracked frontend/tsconfig.tsbuildinfo, which every build
# rewrites. That local change is what made `git pull` refuse in the past.
# It's a build cache: throw the server's copy away.
# (No stderr redirection here on purpose: in Windows PowerShell 5.1,
# redirecting a native command's stderr under ErrorActionPreference=Stop
# turns any warning into a terminating error.)
$tracked = Invoke-Native 'git ls-files' { git ls-files frontend/tsconfig.tsbuildinfo }
if ($tracked) {
    Invoke-Native 'discard build cache' { git checkout -- frontend/tsconfig.tsbuildinfo } | Out-Null
}

$dirty = Invoke-Native 'git status' { git status --porcelain --untracked-files=no }
if ($dirty) {
    Write-Host ($dirty -join "`n") -ForegroundColor Yellow
    throw ("These tracked files were changed ON THE SERVER, so the deploy stopped " +
           "before touching anything (the site is still running). Make the change in " +
           "the repo instead, then discard it here with: git checkout -- <file>")
}

$prev   = (Invoke-Native 'git rev-parse HEAD' { git rev-parse HEAD }).Trim()
$target = (Invoke-Native "git rev-parse origin/$Branch" { git rev-parse "origin/$Branch" }).Trim()
$targetShort = $target.Substring(0, 7)
if ($prev -eq $target) {
    Write-Host "Server is already on $targetShort - rebuilding anyway." -ForegroundColor Yellow
} else {
    Write-Host "Deploying $($prev.Substring(0,7)) -> $targetShort"
}

# ---------------------------------------------------------------------------
# 2-5. Stop, update, build — with rollback on any failure
# ---------------------------------------------------------------------------
Step 'Stopping site and app pool'
Stop-App

try {
    Step "Updating code to origin/$Branch"
    Invoke-Native 'git reset' { git reset --hard $target } | Out-Null
    $now = (Invoke-Native 'git rev-parse HEAD' { git rev-parse HEAD }).Trim()
    if ($now -ne $target) { throw "Code is on $now, expected $target." }

    Push-Location $frontend
    try {
        $lockChanged = $false
        if ($prev -ne $target) {
            $changed = Invoke-Native 'git diff' { git diff --name-only $prev $target -- frontend/package-lock.json frontend/package.json }
            $lockChanged = [bool]$changed
        }
        if ($lockChanged -or -not (Test-Path 'node_modules')) {
            Step 'Installing frontend packages (npm ci)'
            Invoke-Native 'npm ci' { npm ci } | Out-Host
        }

        Step 'Building frontend'
        if (Test-Path $distPrev) { Remove-Item $distPrev -Recurse -Force }
        if (Test-Path $dist)     { Rename-Item $dist 'dist.previous' }
        Remove-Item 'tsconfig.tsbuildinfo' -Force -ErrorAction SilentlyContinue
        Invoke-Native 'npm run build' { npm run build } | Out-Host
    } finally {
        Pop-Location
    }

    Step 'Checking build output'
    foreach ($f in 'index.html', 'web.config', 'version.json') {
        if (-not (Test-Path (Join-Path $dist $f))) { throw "Build output is missing dist\$f." }
    }
    $version = Get-Content (Join-Path $dist 'version.json') -Raw | ConvertFrom-Json
    if ($version.commit -ne $targetShort) {
        throw "dist\version.json says commit '$($version.commit)', expected '$targetShort'."
    }

    if ($prev -ne $target) {
        $req = Invoke-Native 'git diff' { git diff --name-only $prev $target -- backend/requirements.txt }
        if ($req) {
            Write-Host ("NOTE: backend\requirements.txt changed in this deploy. Check whether the " +
                        "server's Python environment needs a pip install (test-only packages don't).") -ForegroundColor Yellow
        }
    }

    if (Test-Path $distPrev) { Remove-Item $distPrev -Recurse -Force }
}
catch {
    Write-Host "`nDEPLOY FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Rolling back to the previous version...' -ForegroundColor Yellow
    & git reset --hard $prev | Out-Null
    if (Test-Path $distPrev) {
        if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
        Rename-Item $distPrev 'dist'
    }
    Start-App
    Write-Host "Rolled back to $($prev.Substring(0,7)); the site is running the OLD version." -ForegroundColor Yellow
    exit 1
}

# ---------------------------------------------------------------------------
# 6. Start and report
# ---------------------------------------------------------------------------
Step 'Starting app pool and site'
Start-App

$subject = (Invoke-Native 'git log' { git log -1 --format=%s }).Trim()
Write-Host "`nDEPLOYED $targetShort - $subject" -ForegroundColor Green
Write-Host "Built at $($version.builtAt). Check it in a browser: https://<server>/version.json shows commit $targetShort."
Write-Host 'Remember any SQL steps for this release in DEPLOYMENT.md (Pending deploy).'
