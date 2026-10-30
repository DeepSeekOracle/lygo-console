# LYGO-THIRD-PARTY-LICENCE: this script fetches FilmCraft, licensed MIT OR Apache-2.0 (the FilmCraft release).
# Verified 2026-10-12 against the FilmCraft release's own licence API. The payload is a separate work:
# its licence governs it, and its own licence file is kept beside the payload where the
# release ships one. Nothing copyleft is bundled by this kit.
<#
.SYNOPSIS
  Install FilmCraft (the console's open-source video editor) from the pinned release in
  utilities\filmcraft\PIN.json.

.DESCRIPTION
  The console boots without this: the filmcards then say plainly that FilmCraft is not installed
  and name this script. Nothing is fetched by a normal boot.

  Everything is verified against the release's own SHA256SUMS.txt BEFORE anything is unpacked. A
  payload whose hash does not match is refused by name and nothing already installed is replaced.

  Two payloads are known:
    win-x64   filmcraft.exe (the editor) + filmcraft-cli.exe (headless engine, control-channel
              client and MCP server) + the licences.             ~142 MB unpacked
    web       the same engine and UI compiled to WebAssembly, a static site the console serves
              locally so the editor opens in a browser with no install.  ~43 MB unpacked

  -Payload both (default) fetches what this host can use; win-x64 on Windows, and web everywhere.

.PARAMETER Version
  The FilmCraft release to install. Defaults to the pin in PIN.json, never to `latest`.

.PARAMETER Payload
  both (default), native, or web.

.PARAMETER List
  Show what is installed here now (paths, sizes, hashes) and exit. Installs nothing.

.PARAMETER Force
  Re-download and re-verify even when the payload is already installed.

.EXAMPLE
  scripts\fetch_filmcraft.ps1 -List
  scripts\fetch_filmcraft.ps1
  scripts\fetch_filmcraft.ps1 -Payload web -Force
#>
param(
    [string]$Version = "",
    [ValidateSet("both", "native", "web")][string]$Payload = "both",
    [switch]$List,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$KitRoot = Split-Path -Parent $PSScriptRoot
$Home_ = Join-Path $KitRoot "utilities\filmcraft"
$PinPath = Join-Path $Home_ "PIN.json"
if (-not (Test-Path $PinPath)) { Write-Host "PIN.json is missing at $PinPath - this script fetches what that file names, nothing else."; exit 2 }
$Pin = Get-Content $PinPath -Raw | ConvertFrom-Json
if (-not $Version) { $Version = [string]$Pin.release.version }
$Base = [string]$Pin.release.base_url

function Show-Installed {
    Write-Host "FilmCraft in $Home_"
    Write-Host ("  pinned release : {0}" -f [string]$Pin.release.version)
    foreach ($key in @("win-x64", "web")) {
        $dir = Join-Path $Home_ $key
        if (-not (Test-Path $dir)) { Write-Host ("  {0,-7}        : not installed" -f $key); continue }
        $files = Get-ChildItem -Path $dir -Recurse -File | Sort-Object Name
        $mb = [math]::Round((($files | Measure-Object Length -Sum).Sum / 1MB), 1)
        Write-Host ("  {0,-7}        : {1} files, {2} MB" -f $key, $files.Count, $mb)
        foreach ($f in $files) {
            if ($f.Name -match "\.(exe|wasm)$") {
                $hash = (Get-FileHash -Path $f.FullName -Algorithm SHA256).Hash.ToLower()
                $mbF = [math]::Round($f.Length / 1MB, 1)
                Write-Host ("      {0,-26} {1,7} MB  sha256 {2}" -f $f.Name, $mbF, $hash)
            }
        }
    }
}

if ($List) {
    Show-Installed
    Write-Host ""
    Write-Host "Run without -List to install, with -Force to re-download and re-verify."
    exit 0
}

$Native = ""
if ($IsWindows -or $env:OS -eq "Windows_NT") { $Native = "win-x64" }
elseif ($IsMacOS) { $Native = "macos-universal" }
else { $Native = "linux-x86_64" }

$want = @()
if ($Payload -eq "web" -or $Payload -eq "both") { $want += "web" }
if ($Native -eq "win-x64" -and ($Payload -eq "native" -or $Payload -eq "both")) { $want += "win-x64" }
if ($want.Count -eq 0) {
    Write-Host ("No payload this script can install for this host ({0})." -f $Native)
    Write-Host "PIN.json names the platforms and their assets; fetch the right one by hand and tell the steward."
    exit 2
}

$dl = Join-Path $Home_ "_dl"
New-Item -ItemType Directory -Force -Path $dl | Out-Null

function Get-Pinned ($url, $path) {
    if ((Test-Path $path) -and -not $Force) { return }
    Write-Host ("downloading {0}" -f (Split-Path $url -Leaf))
    curl.exe -sSL -o $path $url
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $path)) { throw ("download failed: {0}" -f $url) }
}

try {
    $sumsPath = Join-Path $dl "SHA256SUMS.txt"
    Get-Pinned "$Base/SHA256SUMS.txt" $sumsPath
    $sums = @{}
    foreach ($line in (Get-Content $sumsPath)) {
        if ($line -match "^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$") { $sums[$Matches[2]] = $Matches[1].ToLower() }
    }

    foreach ($key in $want) {
        $entry = $Pin.payloads.$key
        if (-not $entry) { throw ("PIN.json carries no payload named '{0}'" -f $key) }
        $asset = [string]$entry.asset
        $zip = Join-Path $dl $asset
        $into = Join-Path $Home_ $key
        if ((Test-Path $into) -and -not $Force) {
            Write-Host ("{0}: already installed at {1} (use -Force to replace)" -f $key, $into)
            continue
        }
        Get-Pinned "$Base/$asset" $zip
        $want_hash = ([string]$entry.sha256).ToLower()
        $got_hash = (Get-FileHash -Path $zip -Algorithm SHA256).Hash.ToLower()
        if ($sums.ContainsKey($asset)) {
            if ($sums[$asset] -ne $got_hash) { throw ("{0}: the release's own SHA256SUMS.txt disagrees - refusing to install" -f $asset) }
        }
        if ($want_hash -and $want_hash -ne $got_hash) {
            throw ("{0}: sha256 mismatch - pinned {1}, downloaded {2} - refusing to install" -f $asset, $want_hash, $got_hash)
        }
        Write-Host ("{0}: verified {1}" -f $key, $got_hash)
        if (Test-Path $into) { Remove-Item -Recurse -Force $into }
        New-Item -ItemType Directory -Force -Path $into | Out-Null
        $stage = Join-Path $dl ("unpack-" + $key)
        if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
        Expand-Archive -Path $zip -DestinationPath $stage -Force
        $inner = Get-ChildItem -Path $stage -Directory | Select-Object -First 1
        if ($inner -and (Get-ChildItem -Path $stage -File).Count -eq 0) {
            Get-ChildItem -Path $inner.FullName -Force | Move-Item -Destination $into
        } else {
            Get-ChildItem -Path $stage -Force | Move-Item -Destination $into
        }
        Remove-Item -Recurse -Force $stage
        Write-Host ("{0}: installed into {1}" -f $key, $into)
    }
    Remove-Item -Recurse -Force $dl -ErrorAction SilentlyContinue
    Write-Host ""
    Show-Installed
    Write-Host ""
    Write-Host "Boot it from the console: the FilmCraft card's buttons, or the film limb:"
    Write-Host "  filmcraft-cli.exe --demo inspect sequence"
    exit 0
}
catch {
    Write-Host ("filmcraft fetch refused: {0}" -f $_.Exception.Message)
    Write-Host "Nothing was replaced. The downloads stay in $dl if you want to look at them."
    exit 1
}
