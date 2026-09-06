"""Send a file to the PicoCalc prompt using 128-byte XMODEM."""
import argparse
import math
import os
import sys
import time

import serial
from xmodem import XMODEM


def positive_seconds(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("timeout must be finite and positive")
    return value


def remote_path(value):
    if not value or any(ord(c) < 32 or ord(c) > 126 or c == '"' for c in value):
        raise argparse.ArgumentTypeError("remote path must be printable ASCII without quotes")
    return value


def wait_handshake(port, timeout):
    """Accept NAK immediately, or a CRC 'C' followed by an idle gap.

    Reading byte-by-byte handles optional echo. The idle gap avoids mistaking
    the C in RECEIVE (or a banner) for a CRC request. Keep diagnostics bounded.
    """
    deadline = time.monotonic() + timeout
    recent = bytearray()
    candidate_crc = False
    cancelled = False
    while time.monotonic() < deadline:
        port.timeout = min(0.2, max(0.001, deadline - time.monotonic()))
        char = port.read(1)
        if not char:
            if candidate_crc:
                return b"C"
            continue
        recent.extend(char)
        del recent[:-256]
        if char == b"\x15":
            return char
        if char == b"\x18" and cancelled:
            raise RuntimeError("receiver cancelled the transfer")
        cancelled = char == b"\x18"
        candidate_crc = char == b"C"
    raise TimeoutError(f"no XMODEM handshake within {timeout:g}s; received {bytes(recent)!r}")


class Progress:
    def __init__(self, size):
        self.size = size
        self.started = time.monotonic()
        self.last_report = self.started
        self.previous_packet = None
        self.previous_errors = 0
        self.previous_successful = 0
        self.retries = 0

    def __call__(self, packets, successful, errors):
        # xmodem resets errors per packet and repeats the count on success.
        if packets != self.previous_packet:
            self.previous_errors = 0
        self.retries += max(0, errors - self.previous_errors)
        self.previous_errors = errors
        if successful > self.previous_successful:
            # EOT retries reuse the last packet number but start at zero.
            self.previous_errors = 0
        self.previous_successful = successful
        self.previous_packet = packets
        now = time.monotonic()
        if now - self.last_report < 2:
            return
        self.last_report = now
        sent = min(successful * 128, self.size)
        elapsed = now - self.started
        rate = sent / max(elapsed, 0.001)
        eta = f"{(self.size - sent) / rate:.0f}s" if rate else "unknown"
        print(f"Progress: {sent}/{self.size} bytes ({sent / self.size:.1%}), "
              f"retries={self.retries}, elapsed={elapsed:.0f}s, ETA={eta}", flush=True)


def transfer(args):
    # Validate/open before changing the device into receive mode.
    with open(args.local_file, "rb") as source:
        size = os.fstat(source.fileno()).st_size
        if size == 0:
            raise ValueError("input file is empty")
        with serial.Serial(args.port, 115200, timeout=1, write_timeout=15) as port:
            time.sleep(0.3)
            port.reset_input_buffer()
            port.write(b"\r\n")
            port.flush()
            time.sleep(0.3)
            port.reset_input_buffer()
            print(f"Sending {size} bytes via {args.port} to {args.remote_path}", flush=True)
            # Written in chunks a receiver's FIFO can hold. The RP2040's is 32
            # bytes and this command is longer than that, so a receiver busy
            # with a slow frame loses the tail of it and never answers. Pacing
            # the write gives it a chance to drain between chunks.
            command = f'XMODEM RECEIVE "{args.remote_path}"\r\n'.encode("ascii")
            for i in range(0, len(command), 16):
                port.write(command[i:i + 16])
                port.flush()
                time.sleep(0.02)
            try:
                primed = bytearray(wait_handshake(port, args.handshake_timeout))
                # A receiver repeats its handshake NAK until data arrives, so
                # any byte buffered now is a surplus one from that wait. Left
                # in place it is read as the reply to block 1, and the sender
                # resends against a receiver that has already moved on until
                # one side gives up. Nothing else can be in flight here: the
                # receiver sends nothing but NAKs until we transmit a block.
                port.reset_input_buffer()
                print(f"Handshake: {bytes(primed)!r}; transfer started", flush=True)

                def getc(size, timeout=10):
                    if primed:
                        data = bytes(primed[:size])
                        del primed[:size]
                        return data
                    port.timeout = timeout
                    return port.read(size) or None

                def putc(data, timeout=10):
                    port.write_timeout = timeout
                    written = port.write(data)
                    if written != len(data):
                        raise serial.SerialTimeoutException("incomplete serial write")
                    return written

                progress = Progress(size)
                ok = XMODEM(getc, putc).send(source, retry=20, timeout=15, callback=progress)
                if not ok:
                    raise RuntimeError("XMODEM failed; remote file may be incomplete")
            except (Exception, KeyboardInterrupt):
                # Best effort release of the receiver; retain the original error.
                try:
                    port.write(b"\x18\x18")
                    port.flush()
                except serial.SerialException:
                    pass
                raise
            elapsed = time.monotonic() - progress.started
            print(f"SUCCESS: {size} bytes sent, retries={progress.retries}, "
                  f"elapsed={elapsed:.1f}s; receiver acknowledged end of transfer", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("local_file")
    parser.add_argument("remote_path", type=remote_path)
    parser.add_argument("--handshake-timeout", type=positive_seconds, default=30)
    parser.add_argument("--port", default="COM4" if os.name == "nt" else "/dev/ttyUSB0")
    args = parser.parse_args(argv)
    try:
        transfer(args)
    except KeyboardInterrupt:
        print("CANCELLED: transfer interrupted", file=sys.stderr)
        return 130
    except (OSError, serial.SerialException, RuntimeError, ValueError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
