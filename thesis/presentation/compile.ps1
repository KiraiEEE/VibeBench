# Build the defence presentation. Run from thesis/presentation/.
#
# XeLaTeX is required, not pdflatex: the deck uses fontspec to pick up Segoe UI
# with a fallback cascade. There is no bibliography, so no bibtex pass.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

Write-Output "=== latexmk -xelatex ==="
latexmk -xelatex -interaction=nonstopmode main.tex

if (Test-Path main.pdf) {
  Write-Output "=== built ==="
  Get-Item main.pdf | Select-Object Name, Length, LastWriteTime
  $pages = (Select-String -Path main.log -Pattern '\((\d+) pages' | Select-Object -Last 1).Matches.Groups[1].Value
  Write-Output "pages: $pages"
} else {
  Write-Output "main.pdf was NOT produced. First errors from main.log:"
  Select-String -Path main.log -Pattern '^!' | Select-Object -First 10
}
