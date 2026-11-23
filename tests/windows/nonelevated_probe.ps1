# Proves the per-user (HKCU) native menu works for a NON-elevated process, like the Explorer
# a normal user runs. GitHub's runner is elevated, and the shell skips per-user shell
# extensions in elevated processes, so we drop to a "basic user" token with runas /trustlevel.
# With -Click it also clicks "Merge selected PDFs" and waits for the merged file.
param([string]$Python, [string]$Probe, [switch]$Click)
$ErrorActionPreference = "Stop"
$dir = Join-Path $env:TEMP ("fw-nonelev-" + [guid]::NewGuid().ToString("N").Substring(0, 6))
New-Item -ItemType Directory -Force $dir | Out-Null
$pdf1 = Join-Path $dir "a.pdf"; $pdf2 = Join-Path $dir "b.pdf"
& $Python -c "import pymupdf,sys`nfor p in sys.argv[1:]:`n d=pymupdf.open(); d.new_page().insert_text((72,72),p); d.save(p)" $pdf1 $pdf2

function Invoke-Basic([string]$probeArgs, [string]$out) {
    Remove-Item $out -ErrorAction SilentlyContinue
    $cmd = "cmd /c `"`"$Python`" `"$Probe`" $probeArgs > `"$out`" 2>&1`""
    Write-Host "runas /trustlevel:0x20000 $cmd"
    runas /trustlevel:0x20000 $cmd
    for ($i = 0; $i -lt 60 -and -not (Test-Path $out); $i++) { Start-Sleep 1 }
    Start-Sleep 3
}

$out = Join-Path $dir "dump.txt"
Invoke-Basic "dump `"$pdf1`" `"$pdf2`"" $out
if (-not (Test-Path $out)) { throw "non-elevated probe produced no output" }
$text = Get-Content $out -Raw
Write-Host $text
if ($text -notmatch "Merge selected PDFs") { throw "native menu not visible to a non-elevated process" }
Write-Host "OK: native menu visible to a non-elevated process"

if ($Click) {
    $out2 = Join-Path $dir "click.txt"
    Invoke-Basic "click `"Merge selected PDFs`" `"$pdf1`" `"$pdf2`"" $out2
    for ($i = 0; $i -lt 90 -and -not (Get-ChildItem $dir -Filter "*merged*.pdf"); $i++) { Start-Sleep 1 }
    $merged = Get-ChildItem $dir -Filter "*merged*.pdf"
    if (-not $merged) {
        if (Test-Path $out2) { Get-Content $out2 }
        $log = "$env:LOCALAPPDATA\FileWizard\filewizard.log"
        if (Test-Path $log) { Get-Content $log -Tail 30 }
        throw "clicking Merge did not produce a merged PDF"
    }
    Write-Host "OK: clicked Merge as a normal user -> $($merged.Name)"
}
