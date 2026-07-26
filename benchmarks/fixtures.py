"""On-demand benchmark fixtures (not committed).

Two scales:

- ``ci`` — small archives for the PR structural gate (fast; solid O(n²) still visible).
- ``realistic`` — multi-MiB corpora for wall-time ratios vs stdlib (VISION ≤1.3× surface).

Large solid 7z/RAR archives are generated when py7zr / ``rar`` are available.
WinZip AES ZIPs need the ``[crypto]`` extra to build (and to decrypt under archivey).
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import io
import os
import shutil
import struct
import subprocess
import tarfile
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

# Password for the generated WinZip AES fixture (must match harness open).
ZIP_AES_PASSWORD = b"bench-secret"

# ---------------------------------------------------------------------------
# Scale profiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Scale:
    name: str
    common_members: int
    common_member_size: int
    gzip_size: int
    solid_members: int
    solid_member_size: int


SCALES: dict[str, Scale] = {
    # ~32 KiB ZIP/TAR, 64 KiB gzip, ~2 MiB solid — PR structural gate.
    "ci": Scale(
        name="ci",
        common_members=8,
        common_member_size=4 * 1024,
        gzip_size=64 * 1024,
        solid_members=32,
        solid_member_size=64 * 1024,
    ),
    # ~16 MiB ZIP/TAR, 32 MiB gzip, ~16 MiB solid — wall-time vs stdlib.
    "realistic": Scale(
        name="realistic",
        common_members=64,
        common_member_size=256 * 1024,
        gzip_size=32 * 1024 * 1024,
        solid_members=64,
        solid_member_size=256 * 1024,
    ),
}

DEFAULT_SCALE = "ci"


@dataclass(frozen=True)
class FixtureSet:
    """Paths to archives used by one harness run."""

    root: Path
    scale: Scale
    zip_path: Path
    zip_lzma_path: Path
    zip_aes_path: Path | None
    tar_path: Path
    targz_path: Path
    tarbz2_path: Path
    gzip_path: Path
    solid_7z: Path | None
    solid_rar: Path | None
    unpacked_solid_7z: int
    unpacked_solid_rar: int
    unpacked_zip_aes: int


def _payload(i: int, size: int) -> bytes:
    """Semi-compressible member payload (not pure zeros; not fully random).

    A 512-byte unique header plus a repeating 4 KiB patterned block — closer to
    text/log-ish ratios than ``os.urandom``, while still exercising the decompressor.
    """
    header = f"archivey-bench-{i}\n".encode()
    pattern = bytes((j * 17 + i) % 256 for j in range(4096))
    body_len = max(0, size - len(header))
    reps, rem = divmod(body_len, len(pattern))
    return header + pattern * reps + pattern[:rem]


def build_zip(path: Path, scale: Scale) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i in range(scale.common_members):
            zf.writestr(f"f{i:04d}.bin", _payload(i, scale.common_member_size))


def build_zip_lzma(path: Path, scale: Scale) -> None:
    """ZIP with LZMA members — exercises the native ZIP codec path (#106)."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_LZMA) as zf:
        # Fewer members than the deflate corpus: LZMA setup dominates at ci scale.
        n = max(2, scale.common_members // 2)
        for i in range(n):
            zf.writestr(f"lzma{i:04d}.bin", _payload(i, scale.common_member_size))


def _aes_ctr_le_encrypt(key: bytes, plaintext: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    out = bytearray(len(plaintext))
    keystream = b""
    pos = 0
    counter = 1
    for i, byte in enumerate(plaintext):
        if pos >= len(keystream):
            keystream = encryptor.update(counter.to_bytes(16, "little"))
            pos = 0
            counter += 1
        out[i] = byte ^ keystream[pos]
        pos += 1
    return bytes(out)


def build_zip_aes(
    path: Path, scale: Scale, *, password: bytes = ZIP_AES_PASSWORD
) -> int:
    """Hand-build a multi-member WinZip AES-256 (AE-2) ZIP; return unpacked bytes.

    Requires ``cryptography`` (the same ``[crypto]`` extra needed to decrypt). Returns
    0 and leaves ``path`` absent when the extra is missing.
    """
    try:
        from archivey.internal.zip_aes import (
            WinZipAesInfo,
            derive_winzip_aes_keys,
        )
    except ImportError:
        return 0
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return 0

    # AE-2 / AES-256 / underlying method = deflate. A handful of members is enough to
    # exercise per-member password confirm + HMAC VerifyingStream without dominating ci.
    n = max(2, min(4, scale.common_members))
    vendor_version, strength, method = 2, 3, 8
    aes = WinZipAesInfo(vendor_version, strength, method)

    locals_blob = bytearray()
    central = bytearray()
    total = 0
    for i in range(n):
        payload = _payload(i, scale.common_member_size)
        total += len(payload)
        name = f"aes{i:04d}.bin".encode()
        compressed = zlib.compress(payload)[2:-4]  # raw deflate
        salt = os.urandom(aes.salt_len)
        enc_key, auth_key, pw_verify = derive_winzip_aes_keys(
            password, salt=salt, key_len=aes.key_len
        )
        ciphertext = _aes_ctr_le_encrypt(enc_key, compressed)
        mac = hmac.new(auth_key, ciphertext, hashlib.sha1).digest()[:10]
        body = salt + pw_verify + ciphertext + mac
        # AE-2: CRC in headers is zero; authenticity is the AES HMAC.
        crc = 0
        aes_extra = struct.pack("<H2sBH", vendor_version, b"AE", strength, method)
        extra = struct.pack("<HH", 0x9901, len(aes_extra)) + aes_extra
        flags = 0x1
        offset = len(locals_blob)
        local = struct.pack(
            "<IHHHHHIIIHH",
            0x04034B50,
            51,
            flags,
            99,
            0,
            0,
            crc,
            len(body),
            len(payload),
            len(name),
            len(extra),
        )
        locals_blob.extend(local)
        locals_blob.extend(name)
        locals_blob.extend(extra)
        locals_blob.extend(body)
        cd = struct.pack(
            "<IHHHHHHIIIHHHHHII",
            0x02014B50,
            51,
            51,
            flags,
            99,
            0,
            0,
            crc,
            len(body),
            len(payload),
            len(name),
            len(extra),
            0,
            0,
            0,
            0,
            offset,
        )
        central.extend(cd)
        central.extend(name)
        central.extend(extra)

    eocd = struct.pack(
        "<IHHHHIIH",
        0x06054B50,
        0,
        0,
        n,
        n,
        len(central),
        len(locals_blob),
        0,
    )
    path.write_bytes(bytes(locals_blob) + bytes(central) + eocd)
    return total


def build_tar(path: Path, scale: Scale, *, mode: str = "w") -> None:
    """Build a tar archive. ``mode`` may be ``w``, ``w:gz``, or ``w:bz2``."""
    with tarfile.open(path, mode) as tf:
        for i in range(scale.common_members):
            data = _payload(i, scale.common_member_size)
            info = tarfile.TarInfo(name=f"f{i:04d}.bin")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def build_gzip(path: Path, scale: Scale) -> None:
    path.write_bytes(gzip.compress(_payload(0, scale.gzip_size), compresslevel=6))


def build_solid_7z(path: Path, scale: Scale) -> int:
    """Build a solid LZMA2 7z; return total unpacked file bytes."""
    import py7zr

    src = path.parent / f"{path.stem}-src"
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    total = 0
    files: dict[str, bytes] = {}
    for i in range(scale.solid_members):
        data = _payload(i, scale.solid_member_size)
        name = f"m{i:03d}.bin"
        files[name] = data
        total += len(data)
        (src / name).write_bytes(data)
    with py7zr.SevenZipFile(path, "w", filters=[{"id": py7zr.FILTER_LZMA2}]) as archive:
        for name in sorted(files):
            archive.write(src / name, arcname=name)
    return total


def build_solid_rar(path: Path, scale: Scale) -> int | None:
    """Build a solid RAR via the ``rar`` binary; return unpacked bytes or None if unavailable."""
    if shutil.which("rar") is None:
        return None
    src = path.parent / f"{path.stem}-src"
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    total = 0
    for i in range(scale.solid_members):
        data = _payload(i, scale.solid_member_size)
        total += len(data)
        (src / f"m{i:03d}.bin").write_bytes(data)
    cmd = ["rar", "a", "-m3", "-s", "-ep1", str(path), str(src / "*")]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return total if path.exists() else None


def _unpacked_solid(scale: Scale) -> int:
    return sum(
        len(_payload(i, scale.solid_member_size)) for i in range(scale.solid_members)
    )


def materialize_fixtures(
    root: Path | None = None,
    *,
    scale: str | Scale = DEFAULT_SCALE,
) -> FixtureSet:
    """Create (or reuse) the comparison corpus under ``root``."""
    if isinstance(scale, str):
        if scale not in SCALES:
            raise ValueError(f"unknown scale {scale!r}; choose from {sorted(SCALES)}")
        scale_obj = SCALES[scale]
    else:
        scale_obj = scale

    if root is None:
        cache = os.environ.get("ARCHIVEY_BENCH_CACHE")
        root = (
            Path(cache) if cache else Path(tempfile.mkdtemp(prefix="archivey-bench-"))
        )
    # Isolate scales so a cached ``ci`` zip is never reused as ``realistic``.
    root = root / scale_obj.name
    root.mkdir(parents=True, exist_ok=True)

    zip_path = root / "common.zip"
    zip_lzma_path = root / "common-lzma.zip"
    zip_aes_path_candidate = root / "common-aes.zip"
    tar_path = root / "common.tar"
    targz_path = root / "common.tar.gz"
    tarbz2_path = root / "common.tar.bz2"
    gzip_path = root / "common.gz"
    if not zip_path.exists():
        build_zip(zip_path, scale_obj)
    if not zip_lzma_path.exists():
        build_zip_lzma(zip_lzma_path, scale_obj)
    if not tar_path.exists():
        build_tar(tar_path, scale_obj)
    if not targz_path.exists():
        build_tar(targz_path, scale_obj, mode="w:gz")
    if not tarbz2_path.exists():
        build_tar(tarbz2_path, scale_obj, mode="w:bz2")
    if not gzip_path.exists():
        build_gzip(gzip_path, scale_obj)

    zip_aes_path: Path | None = None
    unpacked_zip_aes = 0
    if zip_aes_path_candidate.exists():
        zip_aes_path = zip_aes_path_candidate
        # Rebuild would need cryptography; reuse size from the same recipe.
        n = max(2, min(4, scale_obj.common_members))
        unpacked_zip_aes = sum(
            len(_payload(i, scale_obj.common_member_size)) for i in range(n)
        )
    else:
        built = build_zip_aes(zip_aes_path_candidate, scale_obj)
        if built > 0 and zip_aes_path_candidate.exists():
            zip_aes_path = zip_aes_path_candidate
            unpacked_zip_aes = built

    solid_7z: Path | None = None
    unpacked_7z = 0
    try:
        import py7zr  # noqa: F401
    except ImportError:
        py7zr = None  # type: ignore[assignment]
    if py7zr is not None:
        solid_7z = root / "solid-large.7z"
        if not solid_7z.exists():
            unpacked_7z = build_solid_7z(solid_7z, scale_obj)
        else:
            unpacked_7z = _unpacked_solid(scale_obj)

    solid_rar: Path | None = None
    unpacked_rar = 0
    rar_path = root / "solid-large.rar"
    if rar_path.exists():
        solid_rar = rar_path
        unpacked_rar = _unpacked_solid(scale_obj)
    else:
        built_rar = build_solid_rar(rar_path, scale_obj)
        if built_rar is not None:
            solid_rar = rar_path
            unpacked_rar = built_rar

    return FixtureSet(
        root=root,
        scale=scale_obj,
        zip_path=zip_path,
        zip_lzma_path=zip_lzma_path,
        zip_aes_path=zip_aes_path,
        tar_path=tar_path,
        targz_path=targz_path,
        tarbz2_path=tarbz2_path,
        gzip_path=gzip_path,
        solid_7z=solid_7z,
        solid_rar=solid_rar,
        unpacked_solid_7z=unpacked_7z,
        unpacked_solid_rar=unpacked_rar,
        unpacked_zip_aes=unpacked_zip_aes,
    )
