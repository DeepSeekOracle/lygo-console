# LYGO-THIRD-PARTY-LICENCE: this script fetches llama.cpp, licensed MIT (ggml-org/llama.cpp).
# Verified 2026-10-12 against ggml-org/llama.cpp's own licence API. The payload is a separate work:
# its licence governs it, and its own licence file is kept beside the payload where the
# release ships one. Nothing copyleft is bundled by this kit.
<#
.SYNOPSIS
  Install the llama.cpp engine, or a GPU backend for it, from the offline-first kit's
  pinned upstream tag.

.DESCRIPTION
  The kit boots on CPU with no download at all: this script is optional and never called
  by a normal boot.

  -Backend cpu     the shipped engine itself, extracted into engine/ (as before).
  -Backend vulkan  ggml-vulkan.dll only, into engine\backends\vulkan\  (one file, ~43 MB,
                   covers NVIDIA/AMD/Intel). Applied onto engine/ by the kit *only* after
                   it loads a real model on this host.
  -Backend cuda    a complete CUDA engine build into engine\backends\cuda\ (~600 MB,
                   NVIDIA only). Used as the engine dir directly, same rule: self-test
                   first, remembered per host.

  Every install writes engine\backends\<name>\backend.json with the tag, file list, sizes
  and sha256, so a re-fetch is visible as a new backend and earns a fresh self-test.

.PARAMETER Backend
  cpu (default), vulkan or cuda.

.PARAMETER Tag
  llama.cpp release tag. Defaults to $env:LYGO_LLAMA_TAG or the kit's pin.

.PARAMETER List
  Show what is installed now (engine + backends) and exit. Installs nothing.

.EXAMPLE
  scripts\fetch_engine.ps1 -List
  scripts\fetch_engine.ps1 -Backend cuda
#>
param(
  [ValidateSet("cpu", "vulkan", "cuda")][string]$Backend = "cpu",
  [string]$Tag = $(if ($env:LYGO_LLAMA_TAG) { $env:LYGO_LLAMA_TAG } else { "b11457" }),
  [switch]$List
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # no progress bar: it throttles large downloads
$Root   = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Engine = Join-Path $Root "engine"
$Store  = Join-Path $Engine "backends"
$Headers = @{ "User-Agent" = "LYGO-LLM-CONSOLE" }
$Receipt = @()

function Write-Step($msg) { Write-Host "  $msg" }

function Get-BackendKind([string]$dir) {
  if (-not (Test-Path $dir)) { return "" }
  $names = @(Get-ChildItem -File -Path $dir | Select-Object -ExpandProperty Name)
  if (($names -contains "llama-server.exe") -and ($names -contains "llama.dll")) { return "engine" }
  if ($names | Where-Object { $_ -like "ggml-*.dll" }) { return "overlay" }
  return ""
}

function Show-Installed {
  Write-Host "LYGO engine store"
  $exe = Join-Path $Engine "llama-server.exe"
  if (Test-Path $exe) {
    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 2)
    Write-Step "engine\llama-server.exe  $mb MB (base build)"
  } else {
    Write-Step "engine\llama-server.exe  MISSING - run: scripts\fetch_engine.ps1 -Backend cpu"
  }
  $applied = @(Get-ChildItem -File -Path $Engine -Filter "ggml-*-*" -ErrorAction SilentlyContinue |
               Where-Object { $_.Name -notlike "ggml-cpu-*" -and $_.Name -ne "ggml-base.dll" } |
               Select-Object -ExpandProperty Name)
  if ($applied.Count -eq 0) {
    Write-Step "applied to engine\: none (CPU)"
  } else {
    Write-Step ("applied to engine\: " + ($applied -join ", "))
  }
  if (-not (Test-Path $Store)) { Write-Step "engine\backends\: nothing installed"; return }
  foreach ($d in (Get-ChildItem -Directory -Path $Store)) {
    $kind = Get-BackendKind $d.FullName
    $bytes = (Get-ChildItem -File -Recurse -Path $d.FullName | Measure-Object -Property Length -Sum).Sum
    $man = Join-Path $d.FullName "backend.json"
    $tag = if (Test-Path $man) { (Get-Content -Raw $man | ConvertFrom-Json).tag } else { "no manifest" }
    $mb = [math]::Round($bytes / 1MB, 2)
    Write-Step ("engine\backends\{0}\  {1,-7} {2,8} MB  tag={3}" -f $d.Name, $kind, $mb, $tag)
  }
}

if ($List) {
  Show-Installed
  exit 0
}

Write-Host "LYGO engine fetch"
Write-Step "tag     : $Tag"
Write-Step "backend : $Backend"

try {
  $rel = Invoke-RestMethod -Headers $Headers -Uri "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/$Tag"
} catch {
  Write-Error "cannot read release $Tag from ggml-org/llama.cpp: $($_.Exception.Message)"
  exit 2
}
if ($Tag -eq "PIN_AFTER_FETCH") {
  Write-Error "set LYGO_LLAMA_TAG (or -Tag) to a ggml-org/llama.cpp release tag, then re-run."
  exit 2
}

function Get-Asset([string]$pattern) {
  $hit = $rel.assets | Where-Object { $_.name -match $pattern } | Select-Object -First 1
  if (-not $hit) { return $null }
  return $hit
}

function Save-Asset($asset, [string]$destDir) {
  New-Item -ItemType Directory -Force -Path $destDir | Out-Null
  $zip = Join-Path $destDir $asset.name
  Write-Step "downloading $($asset.name) ($([math]::Round($asset.size / 1MB, 1)) MB)"
  Invoke-WebRequest -Headers $Headers -Uri $asset.browser_download_url -OutFile $zip
  return $zip
}

function Write-Manifest([string]$dir, [string]$name, [string]$kind, [string]$tag, [string[]]$only) {
  $files = @{}
  $total = 0
  foreach ($f in (Get-ChildItem -File -Path $dir)) {
    if ($f.Name -eq "backend.json") { continue }
    if ($f.Extension -eq ".zip") { continue }
    if ($only.Count -gt 0 -and ($only -notcontains $f.Name)) { continue }
    $files[$f.Name] = (Get-FileHash -Algorithm SHA256 -Path $f.FullName).Hash.ToLower()
    $total += $f.Length
  }
  $man = [ordered]@{
    name     = $name
    kind     = $kind
    tag      = $tag
    files    = $files
    bytes    = $total
    created  = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    source   = "ggml-org/llama.cpp $tag"
    signature = [char]0x0394 + "9" + [char]0x03A6 + "963-LYGO-BACKEND-v1"
  }
  $json = $man | ConvertTo-Json -Depth 6
  # No BOM: the kit reads this with Python, and a BOM makes json.loads reject it.
  [System.IO.File]::WriteAllText((Join-Path $dir "backend.json"), $json, (New-Object System.Text.UTF8Encoding($false)))
  return $man
}

$stamp = Join-Path $env:TEMP ("lygo_engine_fetch_" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $stamp | Out-Null

switch ($Backend) {
  "cpu" {
    $asset = Get-Asset '^llama-.*-bin-win-cpu-x64\.zip$'
    if (-not $asset) { Write-Error "no CPU zip on $Tag"; exit 3 }
    $zip = Save-Asset $asset $stamp
    New-Item -ItemType Directory -Force -Path $Engine | Out-Null
    Expand-Archive -Path $zip -DestinationPath $Engine -Force
    Write-Step "engine extracted to engine\"
    $Receipt += "cpu:${Tag}:$((Get-Item (Join-Path $Engine 'llama-server.exe')).Length)"
  }
  "vulkan" {
    $asset = Get-Asset '^llama-.*-bin-win-vulkan-x64\.zip$'
    if (-not $asset) { Write-Error "no Vulkan zip on $Tag (assets: $($rel.assets.name -join ', '))"; exit 3 }
    $zip = Save-Asset $asset $stamp
    $tmp = Join-Path $stamp "vulkan"
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $dll = Join-Path $tmp "ggml-vulkan.dll"
    if (-not (Test-Path $dll)) { Write-Error "ggml-vulkan.dll not in $($asset.name)"; exit 4 }
    $dest = Join-Path $Store "vulkan"
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Copy-Item -Force $dll (Join-Path $dest "ggml-vulkan.dll")
    # Only the one file is kept: the rest of that zip is byte-identical to the shipped engine.
    $man = Write-Manifest $dest "vulkan" "overlay" $Tag @("ggml-vulkan.dll")
    Write-Step "installed engine\backends\vulkan\ggml-vulkan.dll ($([math]::Round($man.bytes / 1MB, 1)) MB)"
    $Receipt += "vulkan:${Tag}:$($man.sha256)"
  }
  "cuda" {
    $engineAsset = $rel.assets | Where-Object { $_.name -match '^llama-.*-bin-win-cuda-(\d+\.\d+)-x64\.zip$' } |
                   Sort-Object { [version]($_.name -replace '^llama-.*-bin-win-cuda-(\d+\.\d+)-x64\.zip$', '$1') } -Descending |
                   Select-Object -First 1
    if (-not $engineAsset) { Write-Error "no CUDA x64 zip on $Tag"; exit 3 }
    $cudaVer = $engineAsset.name -replace '^llama-.*-bin-win-cuda-(\d+\.\d+)-x64\.zip$', '$1'
    $rtAsset = Get-Asset ("^cudart-llama-bin-win-cuda-" + [regex]::Escape($cudaVer) + "-x64\.zip$")
    if (-not $rtAsset) { $rtAsset = Get-Asset '^cudart-llama-bin-win-cuda-[\d.]+-x64\.zip$' }
    $dest = Join-Path $Store "cuda"
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    $z1 = Save-Asset $engineAsset $stamp
    Expand-Archive -Path $z1 -DestinationPath $dest -Force
    $note = ""
    if ($rtAsset) {
      $z2 = Save-Asset $rtAsset $stamp
      Expand-Archive -Path $z2 -DestinationPath $dest -Force
    } else {
      $note = " (no cudart zip on this tag - the CUDA runtime must come from the driver)"
    }
    if (-not (Test-Path (Join-Path $dest "ggml-cuda.dll"))) { Write-Error "ggml-cuda.dll missing after extract"; exit 4 }
    if (-not (Test-Path (Join-Path $dest "llama-server.exe"))) { Write-Error "llama-server.exe missing after extract"; exit 4 }
    $man = Write-Manifest $dest "cuda" "engine" $Tag @()
    Write-Step "installed engine\backends\cuda\ (CUDA $cudaVer, $([math]::Round($man.bytes / 1MB, 1)) MB)$note"
    $Receipt += "cuda:${Tag}:$cudaVer"
  }
}

Remove-Item -Recurse -Force $stamp -ErrorAction SilentlyContinue
Write-Host ""
Write-Step "next boot on this PC will self-test the backend with a real model load;"
Write-Step "check http://127.0.0.1:PORT/api/health -> perf.backend_layer"
Write-Host "RECEIPT $($Receipt -join ' | ')"
Show-Installed
exit 0
