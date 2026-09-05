param(
    [string]$Firmware = ".\picocalc_tracker.uf2",
    # B:/pico1-apps is where the SD launcher looks. A file written to B:/ is
    # transferred and stored successfully but never appears in the menu, so the
    # device silently keeps booting whatever was installed before.
    [string]$Destination = "B:/pico1-apps/picocalc_tracker.uf2",
    [string]$Port = "COM4",
    [string]$Python = "python",
    [ValidateRange(1, 3600)]
    [int]$HandshakeTimeout = 30
)

$ErrorActionPreference = "Stop"
$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Test-Path -LiteralPath $Firmware -PathType Leaf)) {
    throw "Firmware not found: $Firmware"
}

# Avoid a traceback on stderr: Windows PowerShell with Stop can terminate
# before reaching the install branch. Probe without importing missing modules.
& $Python -c "import importlib.util, sys; sys.exit(0 if all(importlib.util.find_spec(m) for m in ('serial', 'xmodem')) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing UART transfer dependencies..."
    & $Python -m pip install -r (Join-Path $scriptDirectory "requirements-uart.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "UART dependency installation failed with exit code $LASTEXITCODE"
    }
}

& $Python (Join-Path $scriptDirectory "uart_xmodem_send.py") `
    $Firmware `
    $Destination `
    --port $Port `
    --handshake-timeout $HandshakeTimeout

if ($LASTEXITCODE -ne 0) {
    throw "UART transfer failed with exit code $LASTEXITCODE"
}
