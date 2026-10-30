# LYGO-THIRD-PARTY-LICENCE: this script fetches Plexo, licensed MIT (anmolkapil/plexo).
# Verified 2026-10-12 against anmolkapil/plexo's own licence API. The payload is a separate work:
# its licence governs it, and its own licence file is kept beside the payload where the
# release ships one. Nothing copyleft is bundled by this kit.
# fetch_plexo.ps1 - fetch, verify and place the PINNED Plexo release for this kit.
#
# Plexo (https://github.com/anmolkapil/plexo, MIT, (c) Anmol Kapil) is an EXTERNAL application that adds
# something this kit cannot do at all: several network adapters on one download, and BitTorrent. LYGO's
# own download engine (src/plexo_tools.py) needs none of this and works without it - so this script is
# optional by design, and NOTHING in the console ever runs it by itself.
#
# Rules this script keeps, the same ones scripts/fetch_filmcraft.ps1 keeps:
#   * the version is PINNED in utilities/plexo/PIN.json - never `latest`, because a silent upstream
#     upgrade would make this kit's own claims wrong;
#   * every artifact is sha256-checked BEFORE it is placed. A mismatch REFUSES BY NAME and replaces
#     nothing - never unpack-then-check;
#   * the payload folder (utilities/plexo/<platform>/) is gitignored: the tree carries the pin, this
#     fetcher, the licence and the manifests; the installer or stick carries the payload;
#   * an installer is never RUN here. This script verifies and places; installing is your step;
#   * a normal console boot never calls this. The card says what is missing and names this file.
#
#   Usage:
#     powershell -ExecutionPolicy Bypass -File scripts/fetch_plexo.ps1 -List
#     powershell -ExecutionPolicy Bypass -File scripts/fetch_plexo.ps1
#     powershell -ExecutionPolicy Bypass -File scripts/fetch_plexo.ps1 -Force
#
#   Exit codes: 0 ok (or -List), 2 no pinned asset for this host, 3 sha256 mismatch,
#               4 download failed, 5 pin unreadable.

[CmdletBinding()]
param(
    [switch]$List,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$KitRoot  = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PinPath  = Join-Path $KitRoot "utilities/plexo/PIN.json"
$Payload  = Join-Path $KitRoot "utilities/plexo"
$StageDir = Join-Path $Payload "_dl"
$Licence  = Join-Path $Payload "LICENSE-plexo.txt"

function Host-Platform {
    if ($IsWindows -or $env:OS -eq "Windows_NT") { return "win-x64" }
    if ($IsMacOS) { return "macos-universal" }
    return "linux-x86_64"
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLower()
}

if (-not (Test-Path $PinPath)) {
    Write-Host "PIN MISSING: $PinPath" -ForegroundColor Red
    Write-Host "This kit has no pin for Plexo, so it will not guess a version to download."
    exit 5
}
$Pin = Get-Content -Raw -Path $PinPath | ConvertFrom-Json
$Platform = Host-Platform
$Asset = $Pin.assets.$Platform
$Live = Join-Path $Payload $Platform

Write-Host "Plexo $($Pin.release)  (pinned $($Pin.pinned_on), $($Pin.licence), (c) $($Pin.copyright))"
Write-Host "host platform: $Platform      payload folder: $Live"

if ($List) {
    Write-Host ""
    Write-Host "== what is installed here now =="
    if (Test-Path $Live) {
        $files = Get-ChildItem -Path $Live -File -ErrorAction SilentlyContinue
        if ($files) {
            foreach ($f in $files) {
                $mb = [math]::Round($f.Length / 1MB, 1)
                Write-Host ("  {0,-46} {1,8} MB  {2}" -f $f.Name, $mb, (Get-Sha256 $f.FullName))
            }
        } else {
            Write-Host "  (the folder exists and holds nothing)"
        }
    } else {
        Write-Host "  (no payload folder for this platform)"
    }
    if (Test-Path $StageDir) {
        $staged = Get-ChildItem -Path $StageDir -File -ErrorAction SilentlyContinue
        if ($staged) { Write-Host "  staged in _dl: $($staged.Count) file(s) - a run that did not finish" }
    }
    Write-Host ""
    Write-Host "== the pin =="
    if ($Asset) {
        Write-Host ("  {0}  {1} bytes" -f $Asset.file, $Asset.bytes)
        Write-Host ("  sha256 {0}" -f $Asset.sha256)
        if ($Asset.kind -eq "installer") { Write-Host "  note: $($Asset.install_note)" }
    } else {
        Write-Host "  no pinned asset names this platform" -ForegroundColor Yellow
    }
    if (Test-Path $Licence) { Write-Host "  licence text: $Licence" }
    Write-Host ""
    Write-Host "== LYGO's own engine, which needs none of the above =="
    Write-Host "  src/plexo_tools.py - parallel range blocks, resume, standard library only."
    exit 0
}

if (-not $Asset) {
    Write-Host "NO PAYLOAD FOR THIS HOST: the pin names no asset for '$Platform'." -ForegroundColor Red
    Write-Host "Pick your platform's file from $($Pin.downloads_page) or the release page, or set"
    Write-Host "config plexo_app to an install you already have. Nothing was downloaded."
    exit 2
}

if ((Test-Path $Live) -and -not $Force) {
    $already = Get-ChildItem -Path $Live -File -ErrorAction SilentlyContinue
    if ($already) {
        Write-Host ""
        Write-Host "Already installed: $(Join-Path $Live $Asset.file)" -ForegroundColor Yellow
        Write-Host "Run with -Force to fetch and replace it."
        exit 0
    }
}

New-Item -ItemType Directory -Force -Path $StageDir | Out-Null
$Url = "$($Pin.base_url)$($Asset.file)"
$Staged = Join-Path $StageDir $Asset.file

Write-Host ""
Write-Host "== fetch =="
Write-Host "  $Url"
$sw = [System.Diagnostics.Stopwatch]::StartNew()
try {
    $ProgressPreference = "Continue"
    Invoke-WebRequest -Uri $Url -OutFile $Staged -UseBasicParsing
} catch {
    Write-Host "DOWNLOAD FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Nothing was installed. A large download can also time out: re-run this script and it starts again."
    exit 4
}
$sw.Stop()
$got = Get-Sha256 $Staged
$mb = [math]::Round((Get-Item $Staged).Length / 1MB, 1)
Write-Host ("  {0} MB in {1:n1} s" -f $mb, $sw.Elapsed.TotalSeconds)

Write-Host ""
Write-Host "== verify (before anything is placed) =="
if ($Asset.sha256 -and ($got -ne $Asset.sha256.ToLower())) {
    Write-Host "SHA256 MISMATCH - REFUSING BY NAME" -ForegroundColor Red
    Write-Host "  expected $($Asset.sha256)"
    Write-Host "  got      $got"
    Write-Host "Nothing was installed and nothing was replaced. The staged file is left at:"
    Write-Host "  $Staged"
    Write-Host "Delete it and fetch again; if it mismatches twice, the pin is stale - report it rather than"
    Write-Host "installing either copy."
    exit 3
}
Write-Host "  sha256 OK: $got"

# The release's own updater manifest, when it is reachable: it lists the artifacts for a platform and
# their hashes, so a hash this kit's PIN.json does not carry can still be checked against upstream.
Write-Host ""
Write-Host "== the release's own manifest =="
$manifestName = if ($Platform -eq "win-x64") { "latest.yml" }
                elseif ($Platform -eq "macos-universal") { "latest-mac.yml" }
                else { "latest-linux.yml" }
try {
    $manifest = Invoke-WebRequest -Uri "$($Pin.base_url)$manifestName" -UseBasicParsing
    $text = $manifest.Content
    if ($text -match [regex]::Escape($Asset.file)) {
        Write-Host "  $manifestName lists $($Asset.file): the release still publishes the pinned file"
    } else {
        Write-Host "  $manifestName does NOT list $($Asset.file)" -ForegroundColor Yellow
        Write-Host "  The pin may be superseded upstream. This run still installs what the PIN names -"
        Write-Host "  and says so, rather than quietly following upstream to something unverified."
    }
} catch {
    Write-Host "  $manifestName not reachable (offline?): the PIN's own sha256 was the check, and it passed."
}

Write-Host ""
Write-Host "== place =="
New-Item -ItemType Directory -Force -Path $Live | Out-Null
$Dest = Join-Path $Live $Asset.file
Move-Item -Force -Path $Staged -Destination $Dest
Write-Host "  $Dest"
if (Test-Path $Licence) {
    Copy-Item -Force -Path $Licence -Destination (Join-Path $Live "LICENSE-plexo.txt")
    Write-Host "  licence text copied beside the payload (MIT, (c) Anmol Kapil)"
}

Write-Host ""
Write-Host "== done =="
Write-Host "  verified: $((Get-Sha256 $Dest))"
if (($Asset.kind -eq "installer") -or ($Asset.kind -eq "dmg")) {
    Write-Host ""
    Write-Host "NEXT STEP IS YOURS: this is an installer, and this kit does not run installers quietly."
    Write-Host "  run:  $Dest"
    Write-Host "  Windows will say 'Windows protected your PC' - click More info, then Run anyway."
    Write-Host "  Or point the console at an install you already have: set config key plexo_app to its exe."
    Write-Host "  Until then the Downloads card reads 'installed, not answering' and the Start button says so."
} else {
    Write-Host "  Portable: set config key plexo_app to this file if it is not on your PATH."
    Write-Host "  Make it executable:  chmod +x `"$Dest`""
}
Write-Host ""
Write-Host "LYGO's own engine needed none of this and was already ready: plexo action=fetch in the console."
exit 0
