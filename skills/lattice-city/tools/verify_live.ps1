# verify_live.ps1 — prove the LIVE Lattice City page, from the network.
# usage: pwsh -File verify_live.ps1 [-Url https://excavationpro.ca/games/lattice-city/]
param(
  [string]$Url = 'https://excavationpro.ca/games/lattice-city/',
  [string]$ExpectBuster = ''
)
$ErrorActionPreference = 'Stop'
$fail = 0

function Say($ok, $msg) {
  if ($ok) { Write-Host "PASS  $msg" } else { Write-Host "FAIL  $msg"; $script:fail++ }
}

# 1. page loads
$r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 30
Say ($r.StatusCode -eq 200) "page HTTP $($r.StatusCode), $($r.RawContentLength) bytes"

# 2. which engine — the bundle-vs-city.js trap
$city = [regex]::Match($r.Content, 'src="\./city\.js\?v=([^"]+)"')
$bundle = [regex]::Match($r.Content, 'assets/index-[A-Za-z0-9]+\.js')
if ($city.Success) {
  Say $true "engine is city.js (v=$($city.Groups[1].Value))"
} elseif ($bundle.Success) {
  Say $false "engine is the Vite bundle ($($bundle.Value)) — this is the OLD build; edits to city.js cannot appear"
} else {
  Say $false "no known engine script tag found"
}

# 3. cache-buster matches what was committed
if ($ExpectBuster) {
  Say ($city.Groups[1].Value -eq $ExpectBuster) "cache-buster is '$($city.Groups[1].Value)' (expected '$ExpectBuster')"
} else {
  Write-Host "INFO  cache-buster: '$(if ($city.Success) { $city.Groups[1].Value } else { 'n/a' })'"
}

# 4. rail sprites must be 128x128 on the live CDN (parse the PNG IHDR, no image libs)
$base = ($Url.TrimEnd('/')) + '/assets/tiles/'
foreach ($name in 'rail_straight_ew', 'rail_straight_ns', 'rail_cross') {
  try {
    $bytes = (Invoke-WebRequest -Uri ($base + $name + '.png') -UseBasicParsing -TimeoutSec 30).Content
    if ($bytes -is [string]) { $bytes = [Text.Encoding]::Latin1.GetBytes($bytes) }
    $w = ([int]$bytes[16] -shl 24) -bor ([int]$bytes[17] -shl 16) -bor ([int]$bytes[18] -shl 8) -bor [int]$bytes[19]
    $h = ([int]$bytes[20] -shl 24) -bor ([int]$bytes[21] -shl 16) -bor ([int]$bytes[22] -shl 8) -bor [int]$bytes[23]
    Say (($w -eq 128) -and ($h -eq 128)) "$name is ${w}x${h} (needs 128x128)"
  } catch {
    Say $false "$name could not be fetched: $($_.Exception.Message)"
  }
}

Write-Host ''
if ($fail -eq 0) { Write-Host 'RESULT: live page verified' } else { Write-Host "RESULT: $fail check(s) failed"; exit 1 }
