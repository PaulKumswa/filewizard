# Proves the per-user (HKCU) native registration works for a NON-elevated process, like the
# Explorer a normal user runs. GitHub's runner is elevated, and elevated processes ignore
# per-user COM registrations, so we drop to a "basic user" token with runas /trustlevel.
param([string]$Python, [string]$Probe)
$ErrorActionPreference = "Stop"
$dir = Join-Path $env:TEMP "fw-nonelev"
New-Item -ItemType Directory -Force $dir | Out-Null
$pdf1 = Join-Path $dir "a.pdf"; $pdf2 = Join-Path $dir "b.pdf"
& $Python -c "import pymupdf,sys; [ (lambda d: (d.new_page(), d.save(p)))(pymupdf.open()) for p in sys.argv[1:] ]" $pdf1 $pdf2
$out = Join-Path $dir "out.txt"
Remove-Item $out -ErrorAction SilentlyContinue
$cmd = "cmd /c `"`"$Python`" `"$Probe`" dump `"$pdf1`" `"$pdf2`" > `"$out`" 2>&1`""
Write-Host "runas /trustlevel:0x20000 $cmd"
runas /trustlevel:0x20000 $cmd
for ($i = 0; $i -lt 60 -and -not ((Test-Path $out) -and (Get-Item $out).Length -gt 0); $i++) { Start-Sleep 1 }
Start-Sleep 2
if (-not (Test-Path $out)) { throw "non-elevated probe produced no output" }
$text = Get-Content $out -Raw
Write-Host $text
if ($text -notmatch "Merge selected PDFs") { throw "native menu not visible to a non-elevated process" }
Write-Host "OK: per-user native menu works for non-elevated processes"
