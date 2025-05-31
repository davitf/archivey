import collections
import enum
import functools
import hashlib
import hmac
import io
import logging
import stat
import struct
import subprocess
import threading
import zlib
from typing import IO, Any, Iterable, Iterator, List, Optional, cast

try:
    import rarfile
except ImportError:
    rarfile = None  # type: ignore[assignment]

from archivey.base_reader import ArchiveReader
from archivey.exceptions import (
    ArchiveCorruptedError,
    ArchiveEncryptedError,
    ArchiveError,
    LibraryNotInstalledError,
)
from archivey.formats import ArchiveFormat
from archivey.io_wrappers import ExceptionTranslatingIO
from archivey.types import ArchiveInfo, ArchiveMember, MemberType
from archivey.utils import bytes_to_str, str_to_bytes

logger = logging.getLogger(__name__)


_RAR_COMPRESSION_METHODS = {
    0x30: "store",
    0x31: "fastest",
    0x32: "fast",
    0x33: "normal",
    0x34: "good",
    0x35: "best",
}

RAR_ENCDATA_FLAG_TWEAKED_CHECKSUMS = 0x2
RAR_ENCDATA_FLAG_HAS_PASSWORD_CHECK_DATA = 0x1


RarEncryptionInfo = collections.namedtuple(
    "RarEncryptionInfo", ["algo", "flags", "kdf_count", "salt", "iv", "check_value"]
)


def get_encryption_info(rarinfo: rarfile.RarInfo) -> RarEncryptionInfo | None:
    # The file_encryption attribute is not publicly defined, but it's there.
    if not isinstance(rarinfo, rarfile.Rar5Info):
        return None
    if rarinfo.file_encryption is None:  # type: ignore[attr-defined]
        return None
    return RarEncryptionInfo(*rarinfo.file_encryption)  # type: ignore[attr-defined]


class PasswordCheckResult(enum.Enum):
    CORRECT = 1
    INCORRECT = 2
    UNKNOWN = 3


@functools.lru_cache(maxsize=128)
def _verify_rar5_password_internal(
    password: bytes, salt: bytes, kdf_count: int, check_value: bytes
) -> PasswordCheckResult:
    # Mostly copied from RAR5Parser._check_password
    RAR5_PW_CHECK_SIZE = 8
    RAR5_PW_SUM_SIZE = 4

    if len(check_value) != RAR5_PW_CHECK_SIZE + RAR5_PW_SUM_SIZE:
        return PasswordCheckResult.UNKNOWN  # Unnown algorithm

    hdr_check = check_value[:RAR5_PW_CHECK_SIZE]
    hdr_sum = check_value[RAR5_PW_CHECK_SIZE:]
    sum_hash = hashlib.sha256(hdr_check).digest()
    if sum_hash[:RAR5_PW_SUM_SIZE] != hdr_sum:
        # Unknown algorithm?
        return PasswordCheckResult.UNKNOWN

    iterations = (1 << kdf_count) + 32
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password, salt, iterations)

    pwd_check = bytearray(RAR5_PW_CHECK_SIZE)
    len_mask = RAR5_PW_CHECK_SIZE - 1
    for i, v in enumerate(pwd_hash):
        pwd_check[i & len_mask] ^= v

    if pwd_check != hdr_check:
        return PasswordCheckResult.INCORRECT

    return PasswordCheckResult.CORRECT


def verify_rar5_password(
    password: bytes | None, rar_info: rarfile.RarInfo
) -> PasswordCheckResult:
    """
    Verifies whether the given password matches the check value in RAR5 encryption data.
    Returns True if the password is correct, False if not.
    """
    if not rar_info.needs_password():
        return PasswordCheckResult.CORRECT
    if password is None:
        return PasswordCheckResult.INCORRECT
    encdata = get_encryption_info(rar_info)
    if not encdata or not encdata.flags & RAR_ENCDATA_FLAG_HAS_PASSWORD_CHECK_DATA:
        return PasswordCheckResult.UNKNOWN

    return _verify_rar5_password_internal(
        password, encdata.salt, encdata.kdf_count, encdata.check_value
    )


@functools.lru_cache(maxsize=128)
def _rar_hash_key(password: bytes, salt: bytes, kdf_count: int) -> bytes:
    iterations = 1 << kdf_count
    return hashlib.pbkdf2_hmac("sha256", password, salt, iterations + 16)


def convert_crc_to_encrypted(
    crc: int, password: bytes, salt: bytes, kdf_count: int
) -> int:
    """Convert a CRC32 to the encrypted format used in RAR5 archives.

    This implements the ConvertHashToMAC function from the RAR source code.
    First creates a hash key using PBKDF2 with the password and salt,
    then uses that key for HMAC-SHA256 of the CRC.
    """
    # Convert password to UTF-8 if it isn't already
    if isinstance(password, str):
        password = password.encode("utf-8")

    hash_key = _rar_hash_key(password, salt, kdf_count)

    # Convert CRC to bytes
    raw_crc = crc.to_bytes(4, "little")

    # Compute HMAC-SHA256 of the CRC using the hash key
    digest = hmac.new(hash_key, raw_crc, hashlib.sha256).digest()

    # logger.info(f"Digest: {password=} {salt=} crc={crc:08x} {raw_crc=} {digest.hex()}")

    # XOR the digest bytes into the CRC
    result = 0
    for i in struct.iter_unpack("<I", digest):
        result ^= i[0]

    return result


def check_rarinfo_crc(
    rarinfo: rarfile.RarInfo, password: bytes | None, computed_crc: int
) -> bool:
    encryption_info = get_encryption_info(rarinfo)
    if (
        not encryption_info
        or not encryption_info.flags & RAR_ENCDATA_FLAG_TWEAKED_CHECKSUMS
    ):
        return computed_crc == rarinfo.CRC

    if password is None:
        logger.warning(f"No password specified for checking {rarinfo.filename}")
        return False

    converted = convert_crc_to_encrypted(
        computed_crc, password, encryption_info.salt, encryption_info.kdf_count
    )
    return converted == rarinfo.CRC


class BaseRarReader(ArchiveReader):
    """Base class for RAR archive readers."""

    def __init__(self, archive_path: str, *, pwd: bytes | str | None = None):
        super().__init__(ArchiveFormat.RAR)
        self.archive_path = archive_path
        self._members: Optional[list[ArchiveMember]] = None
        self._format_info: Optional[ArchiveInfo] = None

        if rarfile is None:
            raise LibraryNotInstalledError(
                "rarfile library is not installed. Please install it to work with RAR archives."
            )

        try:
            self._archive = rarfile.RarFile(archive_path, "r")
            if pwd:
                self._archive.setpassword(pwd)
        except rarfile.BadRarFile as e:
            raise ArchiveCorruptedError(f"Invalid RAR archive {archive_path}: {e}")
        except rarfile.NotRarFile as e:
            raise ArchiveCorruptedError(f"Not a RAR archive {archive_path}: {e}")
        except rarfile.NeedFirstVolume as e:
            raise ArchiveError(
                f"Need first volume of multi-volume RAR archive {archive_path}: {e}"
            )
        except rarfile.RarWrongPassword as e:
            raise ArchiveEncryptedError(
                f"Wrong password specified for {archive_path}"
            ) from e

    def close(self):
        if self._archive:
            self._archive.close()
            self._archive = None
            self._members = None

    def _get_link_target(self, info: rarfile.RarInfo) -> Optional[str]:
        if not info.is_symlink():
            return None
        if info.file_redir:
            return info.file_redir[2]
        elif not info.needs_password():
            if self._archive is None:
                raise ArchiveError("Archive is closed")
            return self._archive.read(info.filename).decode("utf-8")

        # If the link target is encrypted, we can't read it.
        return None

    def get_members(self) -> List[ArchiveMember]:
        if self._archive is None:
            raise ArchiveError("Archive is closed")

        # According to https://documentation.help/WinRAR/HELPArcEncryption.htm :
        # If "Encrypt file names" [i.e. header encryption] option is off,
        # file checksums for encrypted RAR 5.0 files are modified using a
        # special password dependent algorithm. [...] So do not expect checksums
        # for encrypted RAR 5.0 files to match actual CRC32 or BLAKE2 values.
        # If "Encrypt file names" option is on, checksums are stored without modification,
        # because they can be accessed only after providing a valid password.

        if self._members is None:
            self._members = []
            rarinfos: list[rarfile.RarInfo] = self._archive.infolist()
            for info in rarinfos:
                compression_method = (
                    _RAR_COMPRESSION_METHODS.get(info.compress_type, "unknown")
                    if info.compress_type is not None
                    else None
                )

                has_encrypted_crc: bool
                encryption_info = get_encryption_info(info)
                if encryption_info:
                    has_encrypted_crc = bool(
                        encryption_info.flags & RAR_ENCDATA_FLAG_TWEAKED_CHECKSUMS
                    )
                else:
                    has_encrypted_crc = False

                member = ArchiveMember(
                    filename=info.filename or "",  # Will never actually be None
                    file_size=info.file_size,
                    compress_size=info.compress_size,
                    mtime=info.mtime.replace(tzinfo=None) if info.mtime else None,
                    type=(
                        MemberType.DIR
                        if info.is_dir()
                        else MemberType.FILE
                        if info.is_file()
                        else MemberType.LINK
                        if info.is_symlink()
                        else MemberType.OTHER
                    ),
                    mode=stat.S_IMODE(info.mode)
                    if hasattr(info, "mode") and isinstance(info.mode, int)
                    else None,
                    crc32=info.CRC if not has_encrypted_crc else None,
                    compression_method=compression_method,
                    comment=info.comment,
                    encrypted=info.needs_password(),
                    raw_info=info,
                    link_target=self._get_link_target(info),
                )
                self._members.append(member)

        return self._members

    def iter_members(self) -> Iterator[ArchiveMember]:
        return iter(self.get_members())

    def get_archive_info(self) -> ArchiveInfo:
        """Get detailed information about the archive's format.

        Returns:
            ArchiveInfo: Detailed format information
        """
        if self._archive is None:
            raise ArchiveError("Archive is closed")

        if self._format_info is None:
            # RAR5 archives have a different magic number and structure
            with open(self.archive_path, "rb") as f:
                magic = f.read(8)
                version = (
                    "5"
                    if magic.startswith(b"\x52\x61\x72\x21\x1a\x07\x01\x00")
                    else "4"
                )

            has_header_encryption = (
                self._archive._file_parser is not None
                and self._archive._file_parser.has_header_encryption()
            )

            self._format_info = ArchiveInfo(
                format=self.get_format(),
                version=version,
                is_solid=getattr(self._archive, "is_solid", lambda: False)(), # rarfile < 4.1 doesn't have is_solid
                comment=self._archive.comment,
                extra={
                    # "is_multivolume": self._archive.is_multivolume(),
                    "needs_password": self._archive.needs_password(),
                    "header_encrypted": has_header_encryption,
                },
            )

        return self._format_info


class RarReader(BaseRarReader):
    """Reader for RAR archives using rarfile."""

    def __init__(self, archive_path: str, *, pwd: bytes | str | None = None):
        super().__init__(archive_path, pwd=pwd)
        self._pwd = pwd

    def _exception_translator(self, e: Exception) -> Optional[Exception]:
        if isinstance(e, rarfile.BadRarFile):
            return ArchiveCorruptedError(f"Error reading member {self.archive_path}")
        return None

    def open(
        self, member: ArchiveMember, *, pwd: Optional[str | bytes] = None
    ) -> IO[bytes]:
        if member.encrypted:
            pwd_check = verify_rar5_password(
                str_to_bytes(pwd or self._pwd), cast(rarfile.RarInfo, member.raw_info)
            )
            if pwd_check == PasswordCheckResult.INCORRECT:
                raise ArchiveEncryptedError(
                    f"Wrong password specified for {member.filename}"
                )

        if self._archive is None:
            raise ValueError("Archive is closed")

        try:
            # Apparently pwd can be either bytes or str
            inner = self._archive.open(member.filename, pwd=bytes_to_str(pwd))
            return ExceptionTranslatingIO(inner, self._exception_translator)  # type: ignore[arg-type]
        except rarfile.BadRarFile as e:
            raise ArchiveCorruptedError(
                f"Error reading member {member.filename}"
            ) from e
        except rarfile.RarWrongPassword as e:
            raise ArchiveEncryptedError(
                f"Wrong password specified for {member.filename}"
            ) from e
        except rarfile.PasswordRequired as e:
            raise ArchiveEncryptedError(
                f"Password required for {member.filename}"
            ) from e
        except rarfile.Error as e:
            raise ArchiveError(
                f"Unknown error reading member {member.filename}: {e}"
            ) from e


class CRCMismatchError(ArchiveCorruptedError):
    def __init__(self, filename: str):
        super().__init__(f"CRC mismatch in {filename}")


class RarStreamMemberFile(io.RawIOBase, IO[bytes]):
    def __init__(
        self,
        member: ArchiveMember,
        shared_stream: IO[bytes],
        lock: threading.Lock,
        *,
        pwd: bytes | None = None,
    ):
        super().__init__()
        self._stream = shared_stream
        assert member.file_size is not None
        self._remaining: int = member.file_size
        self._expected_crc = (
            member.crc32 & 0xFFFFFFFF if member.crc32 is not None else None
        )
        self._expected_encrypted_crc: int | None = (
            member.extra.get("encrypted_crc", None) if member.extra else None
        )
        self._actual_crc = 0
        self._lock = lock
        self._closed = False
        self._filename = member.filename
        self._fully_read = False
        self._member = member
        self._pwd = pwd
        self._crc_checked = False

    def read(self, n: int = -1) -> bytes:
        if self._closed:
            raise ValueError(f"Cannot read from closed/expired file: {self._filename}")

        with self._lock:
            if self._remaining == 0:
                self._fully_read = True
                self._check_crc()
                return b""

            to_read = self._remaining if n < 0 else min(self._remaining, n)
            data = self._stream.read(to_read)
            if not data:
                raise EOFError(f"Unexpected EOF while reading {self._filename}")
            self._remaining -= len(data)
            self._actual_crc = zlib.crc32(data, self._actual_crc)

            logger.info(
                f"Read {len(data)} bytes from {self._filename}, {self._remaining} remaining: {data} ; crc={self._actual_crc:08x}"
            )
            if self._remaining == 0:
                self._fully_read = True
                self._check_crc()

            return data

    def _check_crc(self):
        if self._crc_checked:
            return
        self._crc_checked = True

        matches = check_rarinfo_crc(
            cast(rarfile.RarInfo, self._member.raw_info), self._pwd, self._actual_crc
        )
        if not matches:
            raise CRCMismatchError(self._filename)

        # if expected_crc is None and self._expected_encrypted_crc is not None:
        #     if self._pwd is None:
        #         logger.warning(f"No password available for encrypted CRC in {self._filename}")
        #         return

        #     # Convert the computed CRC to the encrypted format
        #     actual_orig = actual_crc
        #     assert self._member.extra is not None
        #     actual_crc = convert_crc_to_encrypted(actual_crc, self._pwd, self._member.extra.get("encryption_salt", b""), self._member.extra.get("kdf_count", 15))
        #     expected_crc = self._expected_encrypted_crc
        #     logger.info(f"Converted CRC: {self._filename} {self._pwd=} {actual_orig:08x} -> {actual_crc:08x} ; expected {expected_crc:08x}")
        #     return

        # assert expected_crc is not None

        # if actual_crc != expected_crc:
        #     raise CRCMismatchError(self._filename, expected_crc, actual_crc)

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False

    def write(self, b: Any) -> int:
        raise io.UnsupportedOperation("write")

    def writelines(self, lines: Iterable[Any]) -> None:
        raise io.UnsupportedOperation("writelines")

    def close(self) -> None:
        if self._closed:
            return
        try:
            with self._lock:
                while self._remaining > 0:
                    chunk = self.read(min(65536, self._remaining))
                    if not chunk:
                        raise EOFError(
                            f"Unexpected EOF while skipping {self._filename}"
                        )

            self._check_crc()
        finally:
            self._closed = True
            super().close()


class WrongPasswordMemberFile(RarStreamMemberFile):
    def __init__(
        self,
        member: ArchiveMember,
        shared_stream: IO[bytes],
        lock: threading.Lock,
        *,
        pwd: bytes | None = None,
    ):
        super().__init__(member, shared_stream, lock, pwd=pwd)
        self._closed = True

    def read(self, n: int = -1) -> bytes:
        raise ArchiveEncryptedError(f"Wrong password specified for {self._filename}")

    def close(self) -> None:
        pass


class RarStreamReader(BaseRarReader):
    """Reader for RAR archives using the solid stream reader.

    This may fail for non-solid archives where some files are encrypted and others not,
    or there are multiple passwords. If the password is incorrect for some files,
    they will be silently skipped, so the successfully output data will be associated
    with the wrong files. (ideally, use this only for solid archives, which are
    guaranteed to have the same password for all files)
    """

    def __init__(self, archive_path: str, *, pwd: bytes | str | None = None):
        super().__init__(archive_path, pwd=pwd)
        self._proc: subprocess.Popen | None = None
        self._stream: IO[bytes] | None = None
        self._lock = threading.Lock()
        self._active_member: RarStreamMemberFile | None = None
        self._active_index = -1
        self._pwd = bytes_to_str(pwd)
        self.archive_path = archive_path

    def close(self) -> None:
        if self._active_member:
            self._active_member.close()
            self._active_member = None
        if self._stream:
            self._stream.close()
            self._stream = None
        if self._proc:
            self._proc.wait()
            self._proc = None

    def _get_member_file(self, member: ArchiveMember) -> RarStreamMemberFile:
        assert self._stream is not None
        pwd_bytes = str_to_bytes(self._pwd) if self._pwd is not None else None
        if (
            member.encrypted
            and verify_rar5_password(pwd_bytes, cast(rarfile.RarInfo, member.raw_info))
            == PasswordCheckResult.INCORRECT
        ):
            # unrar silently skips encrypted files with incorrect passwords
            return WrongPasswordMemberFile(
                member, self._stream, self._lock, pwd=pwd_bytes
            )

        return RarStreamMemberFile(member, self._stream, self._lock, pwd=pwd_bytes)

    def _open_stream(self) -> None:
        try:
            # Open an unrar process that outputs the contents of all files in the archive to stdout.
            password_args = ["-p" + self._pwd] if self._pwd else ["-p-"]
            cmd = ["unrar", "p", "-inul", *password_args, self.archive_path]
            logger.info(
                f"Opening RAR archive {self.archive_path} with command: {' '.join(cmd)}"
            )
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                bufsize=1024 * 1024,
            )
            if self._proc.stdout is None:
                raise RuntimeError("Could not open unrar output stream")
            self._stream = self._proc.stdout  # type: ignore
        except Exception as e:
            raise ArchiveError(f"Error opening RAR archive {self.archive_path}: {e}")

    def open(
        self, member: ArchiveMember, *, pwd: Optional[str | bytes] = None
    ) -> IO[bytes]:
        if self._archive is None or self._members is None:
            raise ValueError("Archive is closed")

        if pwd is not None:
            pwd = bytes_to_str(pwd)
            if self._pwd is None:
                if self._stream is not None:
                    raise ValueError(
                        "RarStreamReader needs the password to be set in the constructor or first open() call"
                    )
                self._pwd = pwd

            elif pwd != self._pwd:
                raise ValueError("RarStreamReader does not support different passwords")

        if self._pwd is not None:
            # Check if the password is correct for the member
            if member.encrypted:
                pwd_check = verify_rar5_password(
                    str_to_bytes(self._pwd), cast(rarfile.RarInfo, member.raw_info)
                )
                if pwd_check == PasswordCheckResult.INCORRECT:
                    raise ArchiveEncryptedError(
                        f"Wrong password specified for {member.filename}"
                    )

        if self._stream is None:
            self._open_stream()

        try:
            index = self._members.index(member)
        except ValueError:
            raise ValueError("Requested member is not part of this archive")

        if index <= self._active_index:
            raise ValueError(
                f"Cannot re-open already closed/skipped file: {member.filename}"
            )

        # Drain previous active file
        if self._active_member:
            self._active_member.close()

        # Skip any intermediate files between last read and this one
        for i in range(self._active_index + 1, index):
            self._get_member_file(self._members[i]).close()

        f = self._get_member_file(member)
        self._active_member = f
        self._active_index = index
        return f
