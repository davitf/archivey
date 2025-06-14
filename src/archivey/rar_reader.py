import collections
import enum
import functools
import hashlib
import hmac
import io
import logging
import shutil
import stat
import struct
import subprocess
import threading
import zlib
from typing import (
    TYPE_CHECKING,
    Any,
    BinaryIO,
    Callable,
    Iterable,
    Iterator,
    List,
    Optional,
    cast,
)

if TYPE_CHECKING:
    import rarfile
    from rarfile import Rar5Info, RarInfo
else:
    try:
        import rarfile
        from rarfile import Rar5Info, RarInfo
    except ImportError:
        rarfile = None  # type: ignore[assignment]
        Rar5Info = object  # type: ignore[assignment]
        RarInfo = object  # type: ignore[assignment]

from archivey.base_reader import BaseArchiveReaderRandomAccess
from archivey.exceptions import (
    ArchiveCorruptedError,
    ArchiveEncryptedError,
    ArchiveError,
    PackageNotInstalledError,
)
from archivey.formats import ArchiveFormat
from archivey.io_helpers import ErrorIOStream, ExceptionTranslatingIO
from archivey.types import ArchiveInfo, ArchiveMember, CreateSystem, MemberType
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

_RAR_HOST_OS_TO_CREATE_SYSTEM = {
    0: CreateSystem.FAT,
    1: CreateSystem.OS2_HPFS,
    2: CreateSystem.NTFS,
    3: CreateSystem.UNIX,
    4: CreateSystem.MACINTOSH,
    5: CreateSystem.UNKNOWN,  # BeOS is not represented
}

RAR_ENCDATA_FLAG_TWEAKED_CHECKSUMS = 0x2
RAR_ENCDATA_FLAG_HAS_PASSWORD_CHECK_DATA = 0x1


RarEncryptionInfo = collections.namedtuple(
    "RarEncryptionInfo", ["algo", "flags", "kdf_count", "salt", "iv", "check_value"]
)


def is_rar_info_hardlink(rarinfo: RarInfo) -> bool:
    if not isinstance(rarinfo, Rar5Info):
        return False
    return (
        rarinfo.file_redir is not None
        and rarinfo.file_redir[0] == rarfile.RAR5_XREDIR_HARD_LINK
    )


def get_encryption_info(rarinfo: RarInfo) -> RarEncryptionInfo | None:
    # The file_encryption attribute is not publicly defined, but it's there.
    if not isinstance(rarinfo, Rar5Info):
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
    password: bytes | None, rar_info: RarInfo
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

    # XOR the digest bytes into the CRC
    result = 0
    for i in struct.iter_unpack("<I", digest):
        result ^= i[0]

    return result


def check_rarinfo_crc(
    rarinfo: RarInfo, password: bytes | None, computed_crc: int
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


class BaseRarReader(BaseArchiveReaderRandomAccess):
    """Base class for RAR archive readers."""

    def __init__(
        self,
        archive_path: str,
        *,
        pwd: bytes | str | None = None,
    ):
        super().__init__(ArchiveFormat.RAR, archive_path)
        self._members: Optional[list[ArchiveMember]] = None
        self._format_info: Optional[ArchiveInfo] = None

        if rarfile is None:
            raise PackageNotInstalledError(
                "rarfile package is not installed. Please install it to work with RAR archives."
            )

        try:
            self._archive = rarfile.RarFile(archive_path, "r")
            if pwd:
                self._archive.setpassword(pwd)
            elif (
                self._archive._file_parser is not None
                and self._archive._file_parser.has_header_encryption()
            ):
                raise ArchiveEncryptedError(
                    f"Archive {archive_path} has header encryption, password required to list files"
                )
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
        except rarfile.NoCrypto as e:
            raise PackageNotInstalledError(
                "cryptography package is not installed. Please install it to read RAR files with encrypted headers."
            ) from e

    def close(self):
        if self._archive:
            self._archive.close()
            self._archive = None
            self._members = None

    def _get_link_target(self, info: RarInfo) -> Optional[str]:
        # TODO: in RAR4 format, link targets are stored as the file contents. is it
        # possible that the link target itself is encrypted?
        if not info.is_symlink() and not is_rar_info_hardlink(info):
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
            rarinfos: list[RarInfo] = self._archive.infolist()
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
                        MemberType.HARDLINK
                        if is_rar_info_hardlink(info)
                        else MemberType.DIR
                        if info.is_dir()
                        else MemberType.FILE
                        if info.is_file()
                        else MemberType.SYMLINK
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
                    create_system=_RAR_HOST_OS_TO_CREATE_SYSTEM.get(
                        info.host_os, CreateSystem.UNKNOWN
                    )
                    if info.host_os is not None
                    else None,
                    raw_info=info,
                    link_target=self._get_link_target(info),
                    extra={"host_os": getattr(info, "host_os", None)},
                )
                self._members.append(member)
                self.register_member(member)

            self.set_all_members_registered()

        return self._members

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
                format=self.format,
                version=version,
                is_solid=getattr(
                    self._archive, "is_solid", lambda: False
                )(),  # rarfile < 4.1 doesn't have is_solid
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

    def __init__(
        self,
        archive_path: str,
        *,
        pwd: bytes | str | None = None,
    ):
        super().__init__(archive_path, pwd=pwd)
        self._pwd = pwd

    def _exception_translator(self, e: Exception) -> Optional[ArchiveError]:
        if isinstance(e, rarfile.BadRarFile):
            return ArchiveCorruptedError(f"Error reading member {self.archive_path}")
        return None

    def open(
        self,
        member_or_filename: ArchiveMember | str,
        *,
        pwd: Optional[str | bytes] = None,
    ) -> BinaryIO:
        # TODO: in RAR4 format, link targets are stored as the file contents. is it
        # possible that the link target itself is encrypted?
        member, filename = self._resolve_member_to_open(member_or_filename)

        if member.encrypted:
            pwd_check = verify_rar5_password(
                str_to_bytes(pwd or self._pwd), cast(RarInfo, member.raw_info)
            )
            if pwd_check == PasswordCheckResult.INCORRECT:
                raise ArchiveEncryptedError(
                    f"Wrong password specified for {member.filename}"
                )

        if self._archive is None:
            raise ValueError("Archive is closed")

        if member.type == MemberType.DIR:  # or member.type == MemberType.SYMLINK:
            raise ValueError(
                f"Cannot open directories in RAR archives: {member.filename}"
            )

        try:
            # Apparently pwd can be either bytes or str.
            inner: BinaryIO = self._archive.open(member.filename, pwd=bytes_to_str(pwd))  # type: ignore[arg-type]
            return ExceptionTranslatingIO(inner, self._exception_translator)
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


class RarStreamMemberFile(io.RawIOBase, BinaryIO):
    def __init__(
        self,
        member: ArchiveMember,
        shared_stream: BinaryIO,
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

            if self._remaining == 0:
                self._fully_read = True
                self._check_crc()

            return data

    def _check_crc(self):
        if self._crc_checked:
            return
        self._crc_checked = True

        matches = check_rarinfo_crc(
            cast(RarInfo, self._member.raw_info), self._pwd, self._actual_crc
        )
        if not matches:
            raise CRCMismatchError(self._filename)

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


class RarStreamReader(BaseRarReader):
    """Reader for RAR archives using the solid stream reader.

    This may fail for non-solid archives where some files are encrypted and others not,
    or there are multiple passwords. If the password is incorrect for some files,
    they will be silently skipped, so the successfully output data will be associated
    with the wrong files. (ideally, use this only for solid archives, which are
    guaranteed to have the same password for all files)
    """

    def __init__(
        self,
        archive_path: str,
        *,
        pwd: bytes | str | None = None,
    ):
        super().__init__(archive_path, pwd=pwd)
        self._pwd = bytes_to_str(pwd)
        self.archive_path = archive_path

    def close(self) -> None:
        pass

    def _open_unrar_stream(
        self, pwd: bytes | str | None = None
    ) -> tuple[subprocess.Popen[bytes], BinaryIO]:
        if pwd is None:
            pwd = self._pwd

        try:
            unrar_path = shutil.which("unrar")
            if not unrar_path:
                raise PackageNotInstalledError(
                    "unrar command is not installed. It is required to read RAR member contents."
                )

            # Open an unrar process that outputs the contents of all files in the archive to stdout.
            password_args = ["-p" + bytes_to_str(pwd)] if pwd else ["-p-"]
            cmd = [unrar_path, "p", "-inul", *password_args, self.archive_path]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                bufsize=1024 * 1024,
            )
            if proc.stdout is None:
                raise RuntimeError("Could not open unrar output stream")
            stream = cast(BinaryIO, proc.stdout)
            return proc, stream

        except (OSError, subprocess.SubprocessError) as e:
            raise ArchiveError(
                f"Error opening RAR archive {self.archive_path}: {e}"
            ) from e

    def _get_member_file(
        self, member: ArchiveMember, stream: BinaryIO, lock: threading.Lock
    ) -> BinaryIO | None:
        if not member.is_file:
            return None

        pwd_bytes = str_to_bytes(self._pwd) if self._pwd is not None else None
        if (
            member.encrypted
            and verify_rar5_password(pwd_bytes, cast(RarInfo, member.raw_info))
            == PasswordCheckResult.INCORRECT
        ):
            # unrar silently skips encrypted files with incorrect passwords
            return ErrorIOStream(
                ArchiveEncryptedError(f"Wrong password specified for {member.filename}")
            )

        return RarStreamMemberFile(member, stream, lock, pwd=pwd_bytes)

    def iter_members_with_io(
        self,
        filter: Callable[[ArchiveMember], bool] | None = None,
        *,
        pwd: bytes | str | None = None,
    ) -> Iterator[tuple[ArchiveMember, BinaryIO | None]]:
        if self._archive is None:
            raise ValueError("Archive is closed")

        proc, unrar_stream = self._open_unrar_stream(pwd)
        lock = threading.Lock()

        # TODO: apply filter, file type and password check to members before opening
        # the unrar stream, pass only the filtered members to unrar

        try:
            for member in self.get_members():
                stream = self._get_member_file(member, unrar_stream, lock)
                yield member, stream
                if stream is not None:
                    # If the caller hasn't read the stream, close() will read any
                    # remaining data.
                    stream.close()
        finally:
            proc.terminate()
            proc.wait()
            unrar_stream.close()

    def has_random_access(self) -> bool:
        return False

    def open(
        self, member_or_filename: ArchiveMember | str, *, pwd: bytes | str | None = None
    ) -> BinaryIO:
        raise ValueError("RarStreamReader does not support opening specific members")
