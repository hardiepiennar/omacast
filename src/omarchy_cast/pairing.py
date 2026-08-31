"""Bounded, non-argv handling for receiver-displayed WPS PINs."""

from __future__ import annotations

from contextlib import contextmanager
import errno
import fcntl
import os
from pathlib import Path
import stat
from typing import BinaryIO, Iterator, Mapping


PAIRING_CREDENTIAL_NAME = "omacast-pairing-pin"
PAIRING_PIN_LENGTH = 8


class PairingError(ValueError):
    pass


def _checksum(pin: bytes) -> int:
    accumulator = 0
    value = int(pin[:7].decode("ascii"))
    while value:
        accumulator += 3 * (value % 10)
        value //= 10
        accumulator += value % 10
        value //= 10
    return (10 - accumulator % 10) % 10


def validate_pairing_pin(value: bytes | bytearray | memoryview | str) -> bytes:
    try:
        encoded = value.encode("ascii") if isinstance(value, str) else bytes(value)
    except (UnicodeEncodeError, TypeError, ValueError) as exc:
        raise PairingError("The pairing PIN must contain exactly eight digits.") from exc
    if len(encoded) != PAIRING_PIN_LENGTH or not encoded.isdigit():
        raise PairingError("The pairing PIN must contain exactly eight digits.")
    if _checksum(encoded) != encoded[7] - ord("0"):
        raise PairingError("The pairing PIN checksum is invalid. Check the digits shown by the display.")
    return encoded


def read_pairing_pin_stdin(stream: BinaryIO) -> bytes:
    raw = stream.readline(PAIRING_PIN_LENGTH + 2)
    if len(raw) > PAIRING_PIN_LENGTH + 1 or not raw.endswith(b"\n"):
        raise PairingError("The pairing PIN input was incomplete or oversized.")
    return validate_pairing_pin(raw[:-1])


@contextmanager
def sealed_pairing_credential(pin: bytes) -> Iterator[str]:
    encoded = validate_pairing_pin(pin)
    flags = getattr(os, "MFD_CLOEXEC", 0) | getattr(os, "MFD_ALLOW_SEALING", 0)
    try:
        descriptor = os.memfd_create(PAIRING_CREDENTIAL_NAME, flags)
    except (AttributeError, OSError) as exc:
        raise PairingError("This system cannot create a protected pairing credential.") from exc
    try:
        written = os.write(descriptor, encoded)
        if written != len(encoded):
            raise PairingError("The protected pairing credential could not be prepared.")
        os.lseek(descriptor, 0, os.SEEK_SET)
        if flags & getattr(os, "MFD_ALLOW_SEALING", 0):
            seals = fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE
            fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, seals)
        yield f"/proc/{os.getpid()}/fd/{descriptor}"
    finally:
        os.close(descriptor)


def validate_sealed_credential_path(path: str) -> str:
    expected_prefix = f"/proc/{os.getpid()}/fd/"
    suffix = path.removeprefix(expected_prefix)
    if (
        path != expected_prefix + suffix
        or not suffix.isdigit()
        or len(suffix) > 7
        or not 3 <= int(suffix) <= 1_048_576
    ):
        raise PairingError("The pairing credential descriptor is unsafe.")
    descriptor = int(suffix)
    try:
        metadata = os.fstat(descriptor)
        seals = fcntl.fcntl(descriptor, fcntl.F_GET_SEALS)
    except OSError as exc:
        raise PairingError("The pairing credential descriptor is unavailable.") from exc
    required = fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_size != PAIRING_PIN_LENGTH
        or seals & required != required
    ):
        raise PairingError("The pairing credential descriptor is unsafe.")
    return path


def _open_runtime_child(parent: int, name: str, *, directory: bool) -> int:
    if not name or name in {".", ".."} or "/" in name:
        raise PairingError("The pairing credential path is unsafe.")
    flags = os.O_CLOEXEC | os.O_NOFOLLOW | (os.O_PATH if directory else os.O_RDONLY | os.O_NONBLOCK)
    if directory:
        flags |= os.O_DIRECTORY
    try:
        return os.open(name, flags, dir_fd=parent)
    except OSError as exc:
        raise PairingError("The pairing credential is unavailable or unsafe.") from exc


def read_pairing_credential(
    environ: Mapping[str, str], *, runtime_root: Path | None = None,
) -> bytes:
    uid = os.getuid()
    trusted_root = Path(f"/run/user/{uid}") if runtime_root is None else runtime_root
    directory_value = environ.get("CREDENTIALS_DIRECTORY", "")
    directory = Path(directory_value)
    try:
        relative = directory.relative_to(trusted_root)
    except (TypeError, ValueError) as exc:
        raise PairingError("The pairing credential directory is outside the user runtime.") from exc
    if not relative.parts:
        raise PairingError("The pairing credential directory is invalid.")
    root_flags = os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY
    descriptors: list[int] = []
    try:
        root_descriptor = os.open(trusted_root, root_flags)
        descriptors.append(root_descriptor)
        root_metadata = os.fstat(root_descriptor)
        if not stat.S_ISDIR(root_metadata.st_mode) or root_metadata.st_uid != uid or stat.S_IMODE(root_metadata.st_mode) & 0o077:
            raise PairingError("The user runtime directory is unsafe.")
        current = root_descriptor
        for part in relative.parts:
            current = _open_runtime_child(current, part, directory=True)
            descriptors.append(current)
            metadata = os.fstat(current)
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != uid or stat.S_IMODE(metadata.st_mode) & 0o022:
                raise PairingError("The pairing credential directory is unsafe.")
        credential = _open_runtime_child(current, PAIRING_CREDENTIAL_NAME, directory=False)
        descriptors.append(credential)
        metadata = os.fstat(credential)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != uid
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_nlink != 1
            or metadata.st_size != PAIRING_PIN_LENGTH
        ):
            raise PairingError("The pairing credential file is unsafe.")
        encoded = os.read(credential, PAIRING_PIN_LENGTH + 1)
        if len(encoded) != PAIRING_PIN_LENGTH:
            raise PairingError("The pairing credential has an invalid size.")
        return validate_pairing_pin(encoded)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise PairingError("The pairing credential path contains a symbolic link.") from exc
        raise PairingError("The pairing credential could not be read safely.") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
