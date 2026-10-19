param(
    [ValidateSet('cpu', 'directml', 'cuda')]
    [string]$Device = 'directml',
    [string]$Python = '.venv\Scripts\python.exe'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not [IO.Path]::IsPathRooted($Python)) { $Python = Join-Path $repoRoot $Python }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Create .venv first: py -3.13 -m venv .venv' }
& $Python -c 'import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Use a virtual environment; system Python will not be modified.' }

& $Python -m pip install -r (Join-Path $repoRoot 'requirements-rag.txt')
if ($LASTEXITCODE -ne 0) { throw 'RAG dependency installation failed.' }

# These packages install the same onnxruntime module. Never leave two variants
# installed together: uninstalling either one later can break the other one.
& $Python -m pip uninstall -y onnxruntime onnxruntime-directml onnxruntime-gpu
if ($LASTEXITCODE -ne 0) { throw 'Could not replace the ONNX runtime.' }
$runtime = @{ cpu = 'onnxruntime==1.27.0'; directml = 'onnxruntime-directml==1.24.4'; cuda = 'onnxruntime-gpu==1.27.0' }[$Device]
& $Python -m pip install $runtime
if ($LASTEXITCODE -ne 0) { throw 'ONNX runtime installation failed; rerun with -Device cpu to restore CPU support.' }
& $Python -c 'import onnxruntime; print("Available providers:", onnxruntime.get_available_providers())'
if ($LASTEXITCODE -ne 0) { throw 'ONNX runtime import failed.' }
Write-Output 'Choose Auto or the matching device in RAG settings, save, and restart the application.'
