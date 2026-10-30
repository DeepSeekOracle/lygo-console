# LYGO-THIRD-PARTY-LICENCE: this script fetches Colibri, licensed Apache-2.0 (JustVugg/colibri).
# Verified 2026-10-12 against JustVugg/colibri's own licence API. The payload is a separate work:
# its licence governs it, and its own licence file is kept beside the payload where the
# release ships one. Nothing copyleft is bundled by this kit.
# Optional. Engine only - never downloads 100GB+ weights.
$ErrorActionPreference = "Stop"
$Dest = Join-Path $PSScriptRoot "..\engine\colibri"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
$headers = @{ "User-Agent" = "LYGO-LLM-CONSOLE" }
$rel = Invoke-RestMethod -Headers $headers -Uri "https://api.github.com/repos/JustVugg/colibri/releases/latest"
$asset = $rel.assets | Where-Object { $_.name -match '(?i)win.*\.zip$|windows.*\.zip$' } | Select-Object -First 1
if (-not $asset) {
  $asset = $rel.assets | Where-Object { $_.name -match '(?i)\.zip$' -and $_.name -notmatch 'src|source' } | Select-Object -First 1
}
if (-not $asset) { throw "no zip on $($rel.tag_name) - unpack a Windows release from github.com/JustVugg/colibri/releases into engine\colibri" }
$zip = Join-Path $env:TEMP $asset.name
Invoke-WebRequest -Headers $headers -Uri $asset.browser_download_url -OutFile $zip
Expand-Archive -Path $zip -DestinationPath $Dest -Force
Write-Host "colibri extracted to $Dest  tag=$($rel.tag_name)"
Write-Host "Weights are separate. Point Scan at a HF/Colibri model dir (config.json + safetensors)."
