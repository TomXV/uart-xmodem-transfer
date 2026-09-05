# uart-xmodem-transfer

Sends a file to a PicoMite (PicoCalc) prompt over UART using 128-byte XMODEM.
Written for flashing RP2040 firmware onto a PicoCalc's SD card, but it will
send anything to any prompt that understands `XMODEM RECEIVE "<path>"`.

## Use

```powershell
powershell -ExecutionPolicy Bypass -File .\transfer_com4.ps1 `
    -Firmware .\picocalc_tracker.uf2 `
    -Destination "B:/pico1-apps/picocalc_tracker.uf2" `
    -Port COM5
```

The helper installs `pyserial` and `xmodem` when they are missing, then hands
off to the Python sender. Override `-Firmware`, `-Destination`, `-Port`,
`-Python` and `-HandshakeTimeout` as needed. The device must already be sitting
at a prompt that accepts `XMODEM RECEIVE`; the script issues that command
itself.

Python directly, if you would rather skip PowerShell:

```sh
python uart_xmodem_send.py firmware.uf2 "B:/pico1-apps/firmware.uf2" --port COM5
```

## What it reports

Progress every two seconds — bytes, retries, elapsed time, estimated time
remaining. A 1.5 MB image over 115200 baud takes about 140 seconds.

Exit status is meaningful: `0` only after the receiver acknowledges the end of
transfer, `1` on failure, `130` on Ctrl+C (which also tries to cancel the
receiver). An incomplete destination file may remain after a failure.

## Two things this cannot tell you

**Where the file needs to land.** A transfer to the wrong directory succeeds
and stores the file, and nothing anywhere reports a problem — the device simply
keeps booting whatever it had. On the reference PicoCalc the SD launcher scans
only `B:/pico1-apps` and lists the `.uf2` files there. Some cards instead carry
the multi-booter from `PicoCalc/Code/pico_multi_booter`, which reads `.bin`
files from `/firmware` and requires them linked at a 200 KiB flash offset. The
two layouts ignore each other's images in silence.

**Whether the firmware runs.** Success here means bytes reached storage. Verify
the running image separately — over UART, a version-identifying command and the
response latency distribution both distinguish builds without reflashing.

## Tests

Nine Python cases and three PowerShell cases drive the whole exchange against a
simulated receiver: echo and no echo, CRC and checksum, retransmission,
failure, and cancellation. No serial port is opened.

```sh
python -m unittest discover -p test_uart_transfer.py -v
```

## Files

| File | Purpose |
| --- | --- |
| `uart_xmodem_send.py` | The sender. Handshake, progress, exit status. |
| `transfer_com4.ps1` | PowerShell wrapper: dependency install, argument defaults. |
| `test_uart_transfer.py` | Offline tests for both. |
| `requirements-uart.txt` | `pyserial`, `xmodem`. |
