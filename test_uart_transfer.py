"""Offline protocol tests: run with the UART virtual environment's Python."""
import argparse
import contextlib
import io
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

import uart_xmodem_send as sender


class Receiver:
    def __init__(self, handshake=b"\x15", echo=True, reject=False, retry_first=False, cancel=False):
        self.handshake, self.echo = handshake, echo
        self.reject, self.retry_first, self.cancel = reject, retry_first, cancel
        self.pending = bytearray()
        self.writes = []
        self.closed = False
        self.packet_attempts = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def reset_input_buffer(self):
        self.pending.clear()

    def flush(self):
        pass

    def read(self, size):
        value = bytes(self.pending[:size])
        del self.pending[:size]
        return value

    def write(self, data):
        self.writes.append(bytes(data))
        if data.startswith(b"XMODEM"):
            self.pending.extend((data if self.echo else b"") + self.handshake)
        elif data[0] == 1:
            if self.cancel:
                raise KeyboardInterrupt
            self.packet_attempts += 1
            self.pending.extend(b"\x15" if self.reject or
                                (self.retry_first and self.packet_attempts == 1) else b"\x06")
        elif data == b"\x04":
            self.pending.extend(b"\x15" if self.reject else b"\x06")
        return len(data)


class TransferTests(unittest.TestCase):
    def run_transfer(self, receiver, content=b"sample" * 50):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.uf2"
            path.write_bytes(content)
            output = io.StringIO()
            with patch.object(sender.serial, "Serial", return_value=receiver) as serial_open, \
                    patch.object(sender.time, "sleep"), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                result = sender.main([str(path), "B:/test.uf2", "--port", "COM5"])
            return result, output.getvalue(), serial_open

    def test_echo_and_no_echo_checksum_and_crc(self):
        for echo in (True, False):
            for handshake in (b"\x15", b"C"):
                with self.subTest(echo=echo, handshake=handshake):
                    receiver = Receiver(handshake, echo)
                    result, output, _ = self.run_transfer(receiver)
                    self.assertEqual(result, 0)
                    self.assertIn("SUCCESS", output)
                    packets = [x for x in receiver.writes if x[0] == 1]
                    self.assertEqual(len(packets), 3)
                    self.assertEqual(len(packets[0]), 133 if handshake == b"C" else 132)
                    self.assertEqual(b"".join(x[3:131] for x in packets)[:300], b"sample" * 50)
                    self.assertTrue(receiver.closed)

    def test_retry_then_success(self):
        result, output, _ = self.run_transfer(Receiver(retry_first=True))
        self.assertEqual(result, 0)
        self.assertIn("retries=1", output)

    def test_failed_send_returns_failure_and_closes(self):
        receiver = Receiver(reject=True)
        result, output, _ = self.run_transfer(receiver)
        self.assertEqual(result, 1)
        self.assertNotIn("SUCCESS", output)
        self.assertTrue(receiver.closed)
        self.assertIn(b"\x18\x18", receiver.writes)

    def test_cancel_returns_130_and_closes(self):
        receiver = Receiver(cancel=True)
        result, _, _ = self.run_transfer(receiver)
        self.assertEqual(result, 130)
        self.assertTrue(receiver.closed)
        self.assertIn(b"\x18\x18", receiver.writes)

    def test_empty_file_never_opens_port(self):
        result, _, serial_open = self.run_transfer(Receiver(), b"")
        self.assertEqual(result, 1)
        serial_open.assert_not_called()

    def test_receiver_cancel(self):
        result, output, _ = self.run_transfer(Receiver(b"\x18\x18"))
        self.assertEqual(result, 1)
        self.assertIn("receiver cancelled", output)

    def test_timeout_and_echo_c_is_not_crc(self):
        receiver = Receiver()
        receiver.pending.extend(b'XMODEM RECEIVE "B:/test.uf2"\r\n')
        with patch.object(sender.time, "monotonic", side_effect=[n / 10 for n in range(100)]):
            with self.assertRaises(TimeoutError):
                sender.wait_handshake(receiver, 8)

    def test_invalid_arguments(self):
        for value in ("0", "-1", "nan", "inf"):
            with self.assertRaises(argparse.ArgumentTypeError):
                sender.positive_seconds(value)
        for value in ('B:/bad"name', "B:/test\r\nRUN", ""):
            with self.assertRaises(argparse.ArgumentTypeError):
                sender.remote_path(value)

    def test_progress_counts_errors_once(self):
        with patch.object(sender.time, "monotonic", return_value=0):
            progress = sender.Progress(256)
        with patch.object(sender.time, "monotonic", return_value=3), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            progress(1, 0, 1)
            progress(1, 0, 2)
            progress(1, 1, 2)
            progress(2, 1, 1)
            progress(2, 2, 1)
            progress(2, 2, 1)  # EOT retry, same packet number; new error count
        self.assertEqual(progress.retries, 4)
        self.assertIn("ETA=", output.getvalue())


@unittest.skipUnless(os.name == "nt", "Windows PowerShell wrapper")
class WrapperTests(unittest.TestCase):
    def run_wrapper(self, pip_exit, transfer_exit):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python = root / "fake-python.cmd"
            python.write_text(
                '@echo off\n'
                'if "%~1"=="-c" exit /b 1\n'
                'if "%~1"=="-m" (\n'
                ' echo MOCK_INSTALL\n'
                f' exit /b {pip_exit}\n)\n'
                'echo MOCK_TRANSFER\n'
                f'exit /b {transfer_exit}\n', encoding="ascii")
            firmware = root / "test.uf2"
            firmware.write_bytes(b"test")
            return subprocess.run([
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(Path(__file__).with_name("transfer_com4.ps1")),
                "-Python", str(python), "-Firmware", str(firmware), "-Port", "COM5",
            ], capture_output=True, text=True)

    def test_install_then_transfer(self):
        result = self.run_wrapper(0, 0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MOCK_INSTALL", result.stdout)
        self.assertIn("MOCK_TRANSFER", result.stdout)

    def test_install_failure_stops_transfer(self):
        result = self.run_wrapper(7, 0)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("MOCK_TRANSFER", result.stdout)

    def test_transfer_failure_propagates(self):
        self.assertNotEqual(self.run_wrapper(0, 1).returncode, 0)


if __name__ == "__main__":
    unittest.main()
