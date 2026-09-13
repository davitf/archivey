<#
.SYNOPSIS
    Install the official x64 RARLAB UnRAR binary on Windows.

.DESCRIPTION
    Downloads and unpacks RARLAB's ``unrarw64.exe`` self-extractor, leaving
    ``<Dest>\UnRAR.exe`` behind. No-ops when that path already exists, so the
    caller can restore <Dest> from a cache and skip the network entirely — the
    reason this is a script and not an inline CI step, and the same
    already-present contract as scripts/install-rarlab-unrar.sh on macOS.

    Chocolatey's ``unrar`` package is not a substitute: it is the x86 build
    behind a ShimGen shim, and the shim has broken solid ``unrar p`` pipes on
    the CI matrix. ``unar`` / ``7z`` are not substitutes either — archivey's
    finder requires the RARLAB banner.

    rarlab.com refuses connections often enough to redden a run on its own
    (see the download-failure loop in CI), so the download retries with
    exponential backoff. Caching <Dest> is what removes the request in the
    common case; the retry only covers a cache miss.

.PARAMETER Dest
    Directory to install into. Created if missing. Must not contain spaces —
    see the check below.

.PARAMETER Retries
    Download attempts before giving up. Backoff doubles from 2s.

.EXAMPLE
    scripts/install-rarlab-unrar.ps1 -Dest "$env:RUNNER_TEMP/rarlab-unrar"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Dest,

    [int] $Retries = 4
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# PowerShell 7.4+ turns a nonzero native exit code into a terminating error under
# the Stop preference. UnRAR's exit codes are data here, not failures, so the
# banner check below reads them itself.
$PSNativeCommandUseErrorActionPreference = $false

# Unversioned by design: rarlab publishes only "current" at this path, so a
# cache entry pins whichever build was current when it was filled. CI's key
# therefore carries a rotating window as well as this file's hash, bounding how
# stale the tested binary can get; see the "Compute unrar cache window" step in
# ci.yml. Editing anything here also refills it. There is no content pin here, unlike the macOS installer's git
# commit; rarlab offers no per-version URL to pin against.
#
# Do not "fix" that by building from source the way the macOS installer does.
# CI exists to test archivey against the binary Windows users actually run,
# which is rarlab's own build — a self-compiled one diverges in toolchain (this
# library parses unrar's output and pipes `unrar p`) and in version. Maintainer
# decision on #320; review/backlog.md ("#320 F2") has the reasoning and the
# integrity options that remain open.
$Url = 'https://www.rarlab.com/rar/unrarw64.exe'

function Find-UnRARExe {
    param([string] $Root)
    Get-ChildItem -Path $Root -Recurse -Filter 'UnRAR.exe' -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

# The RARLAB banner is what archivey's finder actually requires, so it is the
# test for "is this binary the right one" on every path through this script —
# including a <Dest> restored from a cache, which no step here produced.
function Get-UnRARBanner {
    param([string] $Exe)
    # A truncated or non-PE file does not just print the wrong thing — launching
    # it throws, which under the Stop preference would take the script down
    # instead of letting the caller reinstall over it. That failure is an
    # answer ("not a usable unrar"), so catch it and say so.
    try {
        $banner = (& $Exe 2>&1 | Select-Object -First 2) -join ' '
    }
    catch {
        return $null
    }
    if ($banner -match 'UNRAR') { return $banner }
    return $null
}

New-Item -ItemType Directory -Force -Path $Dest | Out-Null
$Dest = (Resolve-Path -LiteralPath $Dest).Path

# The SFX takes its destination as /d<path> — one token, with no documented
# quoting for a path containing spaces. CI's RUNNER_TEMP (D:\a\_temp) has none,
# so rather than guess at the escaping and have extraction fail somewhere
# unhelpful, refuse the path up front and say why.
if ($Dest -match '\s') {
    throw "install-rarlab-unrar: -Dest must not contain whitespace (the RARLAB SFX /d switch takes an unquoted path): $Dest"
}

$installed = Join-Path $Dest 'UnRAR.exe'
if (Test-Path -LiteralPath $installed) {
    # Re-check rather than trusting the file: the caller caches <Dest> with
    # `save-always`, which saves even when a step failed, so a miss that failed
    # to clean up after itself can leave a bad binary under the key. Reinstall
    # over it instead of exiting non-zero — one poisoned entry should not hold
    # the matrix red until the key changes. Eviction would not rescue it either:
    # GHA drops caches *not accessed* for 7 days, and an entry every run
    # restores is never idle.
    $cached = Get-UnRARBanner -Exe $installed
    if ($cached) {
        Write-Host "install-rarlab-unrar: already present at $installed"
        Write-Host "install-rarlab-unrar: $cached"
        exit 0
    }
    Write-Host "install-rarlab-unrar: $installed does not report the UNRAR banner; reinstalling"
    Remove-Item -LiteralPath $installed -Force
}

$sfx = Join-Path $Dest 'unrarw64.exe'
$attempt = 0
$delay = 2
while ($true) {
    $attempt++
    try {
        Write-Host "install-rarlab-unrar: downloading $Url (attempt $attempt/$Retries)"
        Invoke-WebRequest -Uri $Url -OutFile $sfx -TimeoutSec 60
        break
    }
    catch {
        if ($attempt -ge $Retries) {
            throw "install-rarlab-unrar: download failed after $attempt attempts: $($_.Exception.Message)"
        }
        Write-Host "install-rarlab-unrar: attempt $attempt failed ($($_.Exception.Message)); retrying in ${delay}s"
        Start-Sleep -Seconds $delay
        $delay = $delay * 2
    }
}

# A captive portal or an error page saved as .exe would otherwise fail later,
# inside the SFX, with nothing pointing at the download.
$header = New-Object byte[] 2
$stream = [System.IO.File]::OpenRead($sfx)
try { $read = $stream.Read($header, 0, 2) } finally { $stream.Dispose() }
if ($read -lt 2 -or $header[0] -ne 0x4D -or $header[1] -ne 0x5A) {
    $size = (Get-Item -LiteralPath $sfx).Length
    throw "install-rarlab-unrar: $Url did not return a PE executable ($size bytes)"
}

# RARLAB SFX: /s silent, /d<path> extract destination.
Start-Process -FilePath $sfx -ArgumentList '/s', "/d$Dest" -Wait -NoNewWindow

$real = Find-UnRARExe -Root $Dest
if (-not $real) {
    Get-ChildItem -Path $Dest -Recurse | Format-Table FullName
    throw 'install-rarlab-unrar: UnRAR.exe not found after extracting unrarw64.exe'
}
# The SFX drops UnRAR.exe straight into $Dest; the copy is for the day it does
# not. Guard it — Windows is case-insensitive, so copying onto itself would
# truncate the file.
if ($real.DirectoryName -ne $Dest) {
    Copy-Item -LiteralPath $real.FullName -Destination $installed -Force
}

# Keep the self-extractor out of the cached tree (and off PATH, since callers
# add <Dest> to it).
Remove-Item -LiteralPath $sfx -Force -ErrorAction SilentlyContinue

# Check the banner here rather than discovering a wrong binary two steps later.
# Delete one that fails, so a cache save cannot carry it forward; the
# already-present branch re-checks anyway, so a delete that does not take is a
# warning rather than the failure itself.
$banner = Get-UnRARBanner -Exe $installed
if (-not $banner) {
    Remove-Item -LiteralPath $installed -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $installed) {
        Write-Host "install-rarlab-unrar: WARNING could not delete $installed"
    }
    throw "install-rarlab-unrar: $installed does not report the UNRAR banner"
}
Write-Host "install-rarlab-unrar: installed $installed"
Write-Host "install-rarlab-unrar: $banner"
