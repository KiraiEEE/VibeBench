# Local compile script for the thesis (XeLaTeX + BibTeX, run from thesis/latex/)
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

Write-Output "=== Pass 1: xelatex ==="
xelatex -interaction=nonstopmode -halt-on-error main.tex
if ($LASTEXITCODE -ne 0) { Write-Output "xelatex pass 1 reported errors (exit $LASTEXITCODE) -- continuing to gather more log context" }

Write-Output "=== bibtex ==="
bibtex main
if ($LASTEXITCODE -ne 0) { Write-Output "bibtex reported errors (exit $LASTEXITCODE)" }

Write-Output "=== Pass 2: xelatex ==="
xelatex -interaction=nonstopmode -halt-on-error main.tex

Write-Output "=== Pass 3: xelatex ==="
xelatex -interaction=nonstopmode -halt-on-error main.tex

Write-Output "=== Done. main.pdf: ==="
if (Test-Path main.pdf) {
  Get-Item main.pdf | Select-Object Name, Length, LastWriteTime
} else {
  Write-Output "main.pdf was NOT produced."
}
