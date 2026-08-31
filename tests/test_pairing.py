from __future__ import annotations

import fcntl
import os
from pathlib import Path
import tempfile
import unittest

from omarchy_cast.pairing import (
    PairingError,
    read_pairing_credential,
    read_pairing_pin_stdin,
    sealed_pairing_credential,
    validate_pairing_pin,
    validate_sealed_credential_path,
)


class PairingTest(unittest.TestCase):
    def test_pin_requires_exact_digits_and_wps_checksum(self) -> None:
        self.assertEqual(validate_pairing_pin("12345670"), b"12345670")
        for value in ("1234567", "123456700", "12345671", "1234abcd", True):
            with self.subTest(value=value), self.assertRaises(PairingError):
                validate_pairing_pin(value)  # type: ignore[arg-type]

    def test_stdin_is_newline_terminated_and_bounded(self) -> None:
        import io

        self.assertEqual(read_pairing_pin_stdin(io.BytesIO(b"12345670\n")), b"12345670")
        for value in (b"12345670", b"12345670x\n", b"12345671\n"):
            with self.subTest(value=value), self.assertRaises(PairingError):
                read_pairing_pin_stdin(io.BytesIO(value))

    def test_memfd_is_sealed_and_disappears_after_use(self) -> None:
        with sealed_pairing_credential(b"12345670") as path:
            descriptor = int(path.rsplit("/", 1)[1])
            self.assertEqual(Path(path).read_bytes(), b"12345670")
            seals = fcntl.fcntl(descriptor, fcntl.F_GET_SEALS)
            self.assertEqual(
                seals & (fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE),
                fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE,
            )
        self.assertFalse(Path(path).exists())

    def test_service_credential_path_requires_the_current_sealed_memfd(self) -> None:
        with sealed_pairing_credential(b"12345670") as path:
            self.assertEqual(validate_sealed_credential_path(path), path)
        with tempfile.TemporaryFile() as ordinary:
            ordinary.write(b"12345670")
            ordinary.flush()
            with self.assertRaises(PairingError):
                validate_sealed_credential_path(f"/proc/{os.getpid()}/fd/{ordinary.fileno()}")
        for path in ("/tmp/file", f"/proc/{os.getpid()}/fd/0", "/proc/1/fd/7"):
            with self.subTest(path=path), self.assertRaises(PairingError):
                validate_sealed_credential_path(path)

    def test_systemd_credential_is_read_through_validated_descriptors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            runtime.chmod(0o700)
            credential_directory = runtime / "systemd" / "unit"
            credential_directory.mkdir(parents=True, mode=0o700)
            credential_directory.chmod(0o700)
            credential = credential_directory / "omacast-pairing-pin"
            credential.write_bytes(b"12345670")
            credential.chmod(0o600)
            self.assertEqual(
                read_pairing_credential(
                    {"CREDENTIALS_DIRECTORY": str(credential_directory)},
                    runtime_root=runtime,
                ),
                b"12345670",
            )

    def test_systemd_credential_rejects_links_special_files_and_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            runtime.chmod(0o700)
            safe = runtime / "safe"
            safe.mkdir(mode=0o700)
            target = safe / "target"
            target.write_bytes(b"12345670")
            target.chmod(0o600)
            credential = safe / "omacast-pairing-pin"
            credential.symlink_to(target)
            with self.assertRaises(PairingError):
                read_pairing_credential({"CREDENTIALS_DIRECTORY": str(safe)}, runtime_root=runtime)
            credential.unlink()
            os.mkfifo(credential, mode=0o600)
            with self.assertRaises(PairingError):
                read_pairing_credential({"CREDENTIALS_DIRECTORY": str(safe)}, runtime_root=runtime)
            with self.assertRaises(PairingError):
                read_pairing_credential({"CREDENTIALS_DIRECTORY": "/tmp"}, runtime_root=runtime)


if __name__ == "__main__":
    unittest.main()
