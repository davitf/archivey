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
    Directory to install into. Created if missing.

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

# Unversioned by design: rarlab publishes only "current" at this path. A cache
# entry therefore pins whichever build was current when it was filled, until
# the key changes — the CI key hashes this file, so editing anything here
# refills it.
$Url = 'https://www.rarlab.com/rar/unrarw64.exe'

function Find-UnRARExe {
    param([string] $Root)
    Get-ChildItem -Path $Root -Recurse -Filter 'UnRAR.exe' -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

New-Item -ItemType Directory -Force -Path $Dest | Out-Null
$Dest = (Resolve-Path -LiteralPath $Dest).Path

$installed = Join-Path $Dest 'UnRAR.exe'
if (Test-Path -LiteralPath $installed) {
    Write-Host "install-rarlab-unrar: already present at $installed"
    exit 0
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

# The banner is the finder's actual requirement, so check it here rather than
# discovering a wrong binary two steps later. Delete a binary that fails it: the
# caller caches <Dest>, and the already-present check above would otherwise
# accept the bad file on every later run.
$banner = (& $installed 2>&1 | Select-Object -First 2) -join ' '
if ($banner -notmatch 'UNRAR') {
    Remove-Item -LiteralPath $installed -Force -ErrorAction SilentlyContinue
    throw "install-rarlab-unrar: unexpected banner from ${installed}: $banner"
}
Write-Host "install-rarlab-unrar: installed $installed"
Write-Host "install-rarlab-unrar: $banner"
