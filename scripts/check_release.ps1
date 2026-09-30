# Release check: latest.yml sha512 vs actual installer hash
param([string]$Dir = "")
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $Dir) { $Dir = Join-Path $root 'release' }
if (-not (Test-Path $Dir)) { throw "dir missing: $Dir" }
$ly = Get-ChildItem $Dir -Filter 'latest.yml' | Select-Object -First 1
if (-not $ly) { throw 'latest.yml not found' }
$y = Get-Content $ly.FullName -Raw
$m = [regex]::Match($y, 'sha512: ([A-Za-z0-9+/=]+)')
if (-not $m.Success) { throw 'latest.yml has no sha512' }
$expected = $m.Groups[1].Value
$exe = Get-ChildItem $Dir -Filter '*.exe' | Select-Object -First 1
if (-not $exe) { throw 'no installer in release dir' }
$hash = Get-FileHash $exe.FullName -Algorithm SHA512
$actual = [Convert]::ToBase64String([byte[]]$hash.Hash)
$ok = $actual -eq $expected
Write-Host ("installer: " + $exe.Name + " (" + [math]::Round($exe.Length/1MB, 1) + " MB)")
Write-Host "expected: $expected"
Write-Host "actual  : $actual"
Write-Host ("sha512 match: " + $ok)
exit $(if ($ok) { 0 } else { 1 })
