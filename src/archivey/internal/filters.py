"""Universal path-safety checks and policy permission transforms for extraction.

Two independent stages sit in front of every on-disk write (see ``safe-extraction``):

* :func:`check_universal` — the non-bypassable path/link/special-file constraints,
  enforced under **every** :class:`ExtractionPolicy` (including ``TRUSTED``). Run on the
  **final** member, after the policy transform and the caller's filter, so a filter can
  rename an unsafe member to a safe one; whatever it returns is checked.
* :func:`reroot_absolute` — under ``STANDARD`` and ``TRUSTED``, an absolute name is
  moved inside the destination by dropping its root, as tar, unzip and 7-Zip do.
  ``STRICT`` does not re-root, so ``check_universal`` refuses the member there.
* the policy transforms in :data:`POLICY_TRANSFORMS` — permission/ownership normalization
  applied to a transient copy of the member, selected by the active policy.

The post-``os.symlink`` re-resolution check (the third defense-in-depth layer) lives in
the coordinator, next to the ``os.symlink`` call it guards.
"""

from __future__ import annotations

import os
import re
import string
import unicodedata
from pathlib import Path
from typing import Callable

from archivey.exceptions import ExtractionError, FilterRejectionError
from archivey.internal.naming import BIDI_REORDERING_CONTROLS
from archivey.types import ArchiveMember, ExtractionPolicy, MemberType

# Split a member name into path components on either separator; a ".." component after
# this split is a traversal attempt regardless of which separator the archive used.
_SEP_SPLIT = re.compile(r"[\\/]")
# The same split, keeping each separator as its own list item, for a rewrite that has to
# put the name back together exactly as it was apart from the segments it changed.
_SEP_KEEP_SPLIT = re.compile(r"([\\/])")


def _is_absolute(name: str) -> bool:
    """Whether ``name`` is an absolute path: a POSIX root, a UNC share, or a drive letter."""
    if name.startswith("/") or name.startswith("\\"):
        return True  # POSIX root or UNC / rooted-backslash
    # Drive letter: a single ASCII letter followed by ':' (e.g. "C:\\", "C:foo").
    # ``str.isalpha()`` is Unicode-wide and would classify "Ä:foo" — an ordinary POSIX
    # filename — as a Windows absolute path, which is the one thing TRUSTED checks.
    return len(name) >= 2 and name[0] in string.ascii_letters and name[1] == ":"


def _is_rooted(name: str) -> bool:
    """Whether ``name`` starts at a filesystem root: a leading ``/`` or ``\\`` (POSIX
    root, UNC share) or a drive letter followed by a separator (``C:/``, ``C:\\``).

    Narrower than :func:`_is_absolute`, on purpose. A drive-relative ``C:x`` is also an
    ordinary POSIX name (``a:b``), so it has no root to drop: rewriting it to ``x``
    would put the member where another member named ``x`` belongs. It stays refused.
    """
    if name[:1] in ("/", "\\"):
        return True
    return _is_absolute(name) and name[2:3] in ("/", "\\")


def strip_absolute_root(name: str) -> str:
    """``name`` with its root removed: every leading ``/`` and ``\\`` and a drive letter
    followed by a separator, repeatedly, so ``C:\\x``, ``//host/share/x`` and ``/C:/x``
    all lose their whole root. A name that is nothing but a root becomes ``"."``. A name
    that is not rooted (see :func:`_is_rooted`), ``C:x`` included, is returned as is.

    ``C:/`` is dropped on every OS, as bsdtar does, so a member extracts to the same
    place wherever it is extracted. GNU tar keeps it as a literal directory on POSIX.
    """
    stripped = name
    while _is_rooted(stripped):
        if stripped[:1] in ("/", "\\"):
            stripped = stripped.lstrip("/\\")
        else:  # a drive letter and its separator; the next pass strips the separator
            stripped = stripped[2:]
    return stripped or "."


def reroot_absolute(member: ArchiveMember) -> ArchiveMember:
    """Move an absolute ``member`` inside the destination by dropping its root.

    ``/etc/x`` extracts as ``etc/x``, which is what GNU tar, bsdtar, unzip, 7-Zip and
    Python's ``tarfile`` ``data`` filter all do. Only the root goes: a ``..`` component
    is left in place for :func:`check_universal` to refuse.

    A hardlink's target is re-rooted too, because it names another member of the same
    archive (``tar -P`` stores both with their ``/``). A symlink's target is left as
    stored: it is a filesystem path, and an absolute one is refused as an escape.

    Only a rooted name is re-rooted (:func:`_is_rooted`); a drive-relative ``C:x`` is
    left for :func:`check_universal` to refuse.

    Returns ``member`` itself when there is nothing to change.
    """
    changes: dict[str, object] = {}
    if _is_rooted(member.name):
        changes["name"] = strip_absolute_root(member.name)
    target = member.link_target
    if member.type is MemberType.HARDLINK and target is not None and _is_rooted(target):
        changes["link_target"] = strip_absolute_root(target)
    return member.replace(**changes) if changes else member


def _within(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _resolve_or_none(path: Path) -> Path | None:
    """``path.resolve()``, or ``None`` when a symlink loop stops it (Python before
    3.13 raises ``RuntimeError`` for one)."""
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None


def _reject_bidi_override(value: str, *, member_name: str, what: str) -> None:
    """Refuse a bidi override/isolate in a string that is about to become a path.

    Only the *reordering* controls (U+202A–202E, U+2066–2069): they open a span and
    reorder the surrounding text, which is what makes ``evil<RLO>gnp.exe`` display as a
    ``.png``. The three directional marks are deliberately absent from
    ``BIDI_REORDERING_CONTROLS`` — they reorder nothing and occur in legitimate Arabic
    and Hebrew filenames. Listing still presents either kind as stored, with
    ``MEMBER_NAME_BIDI_CONTROL``; this is only about writing one to a filesystem a
    person will read back.

    ASCII cannot contain them — skip the scan on the common path.
    """
    if value.isascii():
        return
    found = [char for char in value if char in BIDI_REORDERING_CONTROLS]
    if not found:
        return
    spelled = ", ".join(f"U+{ord(char):04X}" for char in found)
    raise FilterRejectionError(
        f"Bidirectional override ({spelled}) in {what}: {value!r}. It would display "
        f"as a different name than it is; extract it under a name you choose.",
        member_name=member_name,
    )


def check_universal(
    member: ArchiveMember,
    dest: Path,
    *,
    link_target_on_disk: Callable[[str], str] | None = None,
) -> None:
    """Enforce the non-bypassable universal path-safety constraints on ``member``.

    ``dest`` is the extraction root. Raises :class:`FilterRejectionError` on the first
    violation (an escaping path, an escaping symlink, a special file); returns ``None``
    when the member is safe to extract. Raises :class:`ExtractionError` when the member's
    parent directory cannot be resolved in the destination (a symlink loop already on
    disk): that is a failure, not a block. Applied to the member about to be written, after
    the policy transform and any caller filter, regardless of the active policy.

    ``link_target_on_disk`` is given by a dry run only. It maps a link target to the
    one the link is created with in the scratch tree, so an absolute target is checked
    where it points there.

    Everything here makes the *write itself* dangerous or impossible — escaping the
    destination, a NUL the OS truncates on, a device node. A name that is merely
    *deceptive to read* does not belong in that set and is policy-keyed instead; see
    ``apply_name_policy`` and ADR 0017.
    """
    name = member.name

    # (1) String checks on member.name. Names are faithful (meaning-preserving
    # normalization keeps a leading "/" and every ".."), so the danger is visible directly
    # on member.name — no separate raw_name inspection is needed. Any ".." component is
    # rejected (escaping and internal alike): a well-formed archive has no reason to carry
    # one. An absolute name reaching here was not re-rooted: STRICT, or a filter that
    # returned one (see reroot_absolute).
    if "\x00" in name:
        raise FilterRejectionError("Null byte in member name", member_name=name)
    # A name the platform filesystem encoding cannot represent (a lone surrogate outside
    # the surrogateescape range, on POSIX) can never be materialized under dest — and it
    # would otherwise crash the parent-resolution below with a raw UnicodeEncodeError.
    # (Windows' surrogatepass encoding represents lone surrogates, so this passes there.)
    try:
        os.fsencode(name)
    except UnicodeEncodeError as exc:
        raise FilterRejectionError(
            "Member name cannot be encoded for the filesystem",
            member_name=name,
        ) from exc
    if _is_absolute(name):
        raise FilterRejectionError("Absolute path not allowed", member_name=name)
    if ".." in _SEP_SPLIT.split(name):
        raise FilterRejectionError(
            "Path traversal ('..') in member name", member_name=name
        )

    rel = name.rstrip("/")
    if member.type != MemberType.DIRECTORY and rel in ("", "."):
        raise FilterRejectionError(
            "Member name refers to the extraction root",
            member_name=name,
        )

    if member.type == MemberType.OTHER:
        raise FilterRejectionError(
            "Special file (device/FIFO/socket) not allowed",
            member_name=name,
        )

    # Pre-extraction path computation: the destination's PARENT directory must resolve
    # within the root. We resolve the parent, not the full path — the final component may
    # be a pre-existing (possibly hostile) symlink that the OverwritePolicy will unlink
    # rather than follow, so following it here would wrongly reject a REPLACE. Combined
    # with the no-".." name check above, a parent inside the root guarantees the member
    # lands inside the root. A symlinked *parent* that escapes is still caught.
    dest_root = dest.resolve()
    if rel not in ("", "."):  # "" / "." is the root dir member itself
        try:
            parent = (dest_root / rel).parent.resolve()
        except (OSError, RuntimeError) as exc:
            # A symlink loop the destination already had (Python before 3.13 raises
            # RuntimeError for one). Links this run creates never loop: one that
            # would is removed as an escape. The member cannot be placed, and that is
            # the destination's state, not a policy decision.
            raise ExtractionError(
                "Member's parent directory does not resolve in the destination "
                f"(a symlink loop?): {exc}",
                member_name=name,
            ) from exc
        if not _within(parent, dest_root):
            raise FilterRejectionError(
                "Member resolves outside the destination root",
                member_name=name,
            )

    # Link-target escape at planning time (the authoritative symlink check is re-run
    # post-creation in the coordinator). A symlink target is relative to the link's own
    # directory; a hardlink target is archive-root relative. An absolute target makes the
    # join absolute, so it passes only when it names a path inside dest.
    if member.link_target is not None:
        target = member.link_target
        if member.type in (MemberType.SYMLINK, MemberType.HARDLINK):
            # Same string-level guards as for names: a NUL or an unencodable target
            # cannot name a filesystem path, and would crash the resolves below with a
            # raw ValueError / UnicodeEncodeError instead of a typed rejection.
            if "\x00" in target:
                raise FilterRejectionError(
                    "Null byte in link target",
                    member_name=name,
                    link_target=target,
                )
            try:
                os.fsencode(target)
            except UnicodeEncodeError as exc:
                raise FilterRejectionError(
                    "Link target cannot be encoded for the filesystem",
                    member_name=name,
                    link_target=target,
                ) from exc
        if link_target_on_disk is not None:
            target = link_target_on_disk(target)
        if member.type == MemberType.SYMLINK:
            link_parent = (dest_root / name).parent
            # A target that cannot be resolved (a loop) counts as an escape, as it
            # does in the check after the link is created.
            resolved_target = _resolve_or_none(link_parent / target)
            if resolved_target is None or not _within(resolved_target, dest_root):
                raise FilterRejectionError(
                    "Symlink target escapes destination",
                    member_name=name,
                    link_target=member.link_target,
                )
        elif member.type == MemberType.HARDLINK:
            resolved_target = _resolve_or_none(dest_root / target)
            if resolved_target is None or not _within(resolved_target, dest_root):
                raise FilterRejectionError(
                    "Hardlink target escapes destination",
                    member_name=name,
                    link_target=member.link_target,
                )


# --- Policy permission transforms (applied to a transient copy) ---------------------

_HIGH_BITS = 0o7000  # setuid | setgid | sticky
_EXEC_BITS = 0o111


def _carries_file_mode(member: ArchiveMember) -> bool:
    """Whether ``member``'s mode can end up on a regular file the extraction writes."""
    return member.type in (MemberType.FILE, MemberType.HARDLINK)


def transform_strict(member: ArchiveMember) -> ArchiveMember:
    """STRICT: drop ownership, strip high/execute bits, cap files at 644, dirs at 755.

    A file's stored mode is *masked* with ``0o644``, never raised to it: ``0o660``
    (``umask 007``, a group-shared file) becomes ``0o640``, not ``0o644``. The policy
    that distrusts the archive must not be the one that opens a file up to other users.

    A hardlink's mode is treated as a file's: a link materialized as a copy (the source
    was not selected, or it sits on another device) is written with it.
    """
    new: dict[str, object] = {
        "uid": None,
        "gid": None,
        "uname": None,
        "gname": None,
    }
    if member.is_dir:
        new["mode"] = 0o755
    elif _carries_file_mode(member):
        mode = member.mode
        if mode is None:
            new["mode"] = 0o644
        else:
            new["mode"] = mode & ~_HIGH_BITS & ~_EXEC_BITS & 0o644
    return member.replace(**new)


def transform_standard(member: ArchiveMember) -> ArchiveMember:
    """STANDARD: strip setuid/setgid/sticky, keep execute and ownership.

    Hardlinks are treated as files, for the reason given on ``transform_strict``.
    """
    new: dict[str, object] = {}
    if member.is_dir:
        new["mode"] = 0o755 if member.mode is None else (member.mode & ~_HIGH_BITS)
    elif _carries_file_mode(member):
        new["mode"] = 0o644 if member.mode is None else (member.mode & ~_HIGH_BITS)
    return member.replace(**new)


def transform_trusted(member: ArchiveMember) -> ArchiveMember:
    """TRUSTED: apply stored metadata as-is (a fresh copy, no changes)."""
    return member.replace()


POLICY_TRANSFORMS: dict[ExtractionPolicy, Callable[[ArchiveMember], ArchiveMember]] = {
    ExtractionPolicy.STRICT: transform_strict,
    ExtractionPolicy.STANDARD: transform_standard,
    ExtractionPolicy.TRUSTED: transform_trusted,
}


# --- Cross-platform portable-name policy (safe-extraction O3/O4/O7) ------------------
#
# These rules are keyed on ``ExtractionPolicy`` and applied to the FINAL member name (after
# the policy permission transform and any user filter). They make destination-name handling
# deterministic on every OS: ``STRICT`` is portable-by-default, ``STANDARD`` is portable but
# not paranoid, ``TRUSTED`` defers to the local OS (no name rejection or rewrite). See
# ``dev-docs/decisions/0013-cross-platform-name-safety-policies.md``.

# Windows reserved device names (case-insensitive, with or without an extension). Matched
# against the first dot-separated component of each path segment — ``NUL`` and ``NUL.txt``
# both mangle on Win32.
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def _sanitize_portable_name(name: str) -> str:
    """O7: rewrite a name carrying non-UTF-8 (surrogateescape) bytes to a deterministic
    portable spelling. Each surrogateescape char ``U+DC80``–``U+DCFF`` (a raw byte
    0x80–0xFF that did not decode as UTF-8) becomes ``%XX`` (uppercase hex of the byte);
    a literal ``%`` becomes ``%25``, so a *rewritten* name unescapes back to its bytes.

    Only names that actually carry such bytes are rewritten — valid Unicode (including
    NFC/NFD forms) is representable on every filesystem and is returned unchanged; its
    cross-platform folding is the collision-tracking concern, not a representability one.

    The escaping is therefore reversible within a rewritten name, not across names: a
    stored ``%FF`` is returned verbatim and a raw ``0xFF`` byte is also written ``%FF``.
    The name alone cannot tell the two apart; ``ExtractionResult.presented_name`` can:
    after this rewrite it differs from the written name in the escaped bytes. A set
    ``presented_name`` alone is not enough, since an absolute-name re-root sets it too,
    and then it differs from the written name only by the root it lost. The collision
    map sees both spellings as one key, so the second is resolved by the
    ``OverwritePolicy`` rather than silently overwriting the first.
    """
    if not any("\udc80" <= c <= "\udcff" for c in name):
        return name
    out: list[str] = []
    for c in name:
        if "\udc80" <= c <= "\udcff":
            out.append("%%%02X" % (ord(c) - 0xDC00))
        elif c == "%":
            out.append("%25")
        else:
            out.append(c)
    return "".join(out)


def _strip_trailing_dot_space(name: str) -> str:
    """O3: strip a trailing dot/space from each path segment — the portable spelling Win32
    itself produces (``stuff_etc.`` → ``stuff_etc``). Deterministic on every OS, so the
    result is identical everywhere and the O2 collision map catches any name it now clashes
    with. A segment that is *entirely* dots/spaces has no portable spelling and is rejected
    (an all-dots segment like ``...`` cannot round-trip and would collapse a path).

    A segment ends at either separator, ``/`` or ``\\``, as it does for ``_SEP_SPLIT``
    and ``collision_key``: a TAR name keeps ``\\`` as a literal character, and Windows
    then writes it as a separator, so ``foo. \\bar`` must lose its trailing space too.
    The separators themselves are kept as they are; only the segments change."""
    out: list[str] = []
    for part in _SEP_KEEP_SPLIT.split(name):
        if part in ("/", "\\"):
            out.append(part)
            continue
        # Empty (from a leading/trailing/`//` separator) and the path-navigation spellings
        # "." / ".." are structural, not trailing-dot hazards — pass them through untouched
        # ("." is the never-empty root from normalize_member_name; ".." is caught earlier by
        # check_universal). Stripping them would wrongly collapse the segment to empty.
        if part in ("", ".", ".."):
            out.append(part)
            continue
        stripped = part.rstrip(". ")
        if stripped == "":
            raise FilterRejectionError(
                f"Path segment is entirely dots/spaces: {part!r}", member_name=name
            )
        out.append(stripped)
    return "".join(out)


def apply_name_policy(member: ArchiveMember, policy: ExtractionPolicy) -> ArchiveMember:
    """Enforce the portable-name policy on ``member``'s final name.

    ``TRUSTED`` returns the member unchanged (faithful bytes, defer to the local OS).
    ``STRICT``/``STANDARD`` **reject** only the unsafe name shapes — Windows-reserved device
    names, ``:`` (NTFS alternate data stream), and bidi overrides — and **rewrite** the
    merely-non-portable ones: ``STRICT`` strips trailing dots/spaces (O3) and both levels
    normalize non-representable bytes (O7). Rewriting (not rejecting) a
    legitimate-but-awkward name keeps extraction working; refusal is reserved for
    structures that cannot be safely written. Raises :class:`FilterRejectionError` (so the
    coordinator records ``BLOCKED``) on a rejected name; otherwise returns ``member`` or a
    rewritten ``.replace()`` copy.
    """
    if policy is ExtractionPolicy.TRUSTED:
        return member

    name = member.name

    # Bidi overrides are policy-keyed, not universal (ADR 0017). Unlike everything in
    # ``check_universal``, the *write* is completely safe: the file lands inside the
    # destination under exactly its stored bytes. What is unsafe is the name a person
    # reads back afterwards, which is a presentation problem, and presentation is the
    # axis this function owns. Running here rather than in ``check_universal`` also means
    # a caller filter that renames the member rescues it — renaming a deceptive name is
    # precisely the fix — and that ``TRUSTED`` returns above without reaching this.
    _reject_bidi_override(name, member_name=name, what="member name")
    if member.type in (MemberType.SYMLINK, MemberType.HARDLINK) and member.link_target:
        # A target carrying an override is the same disguise with an extra hop: the
        # listing shows a plausible target and the link on disk points at something that
        # reads differently.
        _reject_bidi_override(
            member.link_target, member_name=name, what=f"link target of {name!r}"
        )
    for segment in _SEP_SPLIT.split(name):
        if not segment:
            continue
        # Reserved device names and ':' are unsafe (device capture / NTFS alternate data
        # stream), not merely awkward — rejected under STRICT and STANDARD on every platform.
        stem = segment.split(".", 1)[0].strip().upper()
        if stem in _RESERVED_NAMES:
            raise FilterRejectionError(
                f"Windows-reserved device name in path: {segment!r}", member_name=name
            )
        if ":" in segment:
            raise FilterRejectionError(
                f"Colon in path segment (NTFS alternate data stream): {segment!r}",
                member_name=name,
            )

    # A trailing dot/space is silently stripped by Win32 — a legitimate macOS/Linux name
    # (e.g. a folder ending in '.'), not an attack. STRICT rewrites it to the portable
    # spelling so extraction succeeds and is identical on every OS; STANDARD/TRUSTED keep it
    # faithful. The O2 collision map catches any clash the rewrite creates.
    if policy is ExtractionPolicy.STRICT:
        name = _strip_trailing_dot_space(name)
    name = _sanitize_portable_name(name)
    if name != member.name:
        return member.replace(name=name)
    return member


# --- sanitize_names: a ready-made caller filter ---------------------------------------


def _collapse_dotdot(name: str) -> str:
    """``name`` with every ``..`` resolved lexically and none left over.

    ``a/../b`` becomes ``b``, as the filesystem the archive came from would read it. A
    ``..`` with nothing left to climb out of is dropped, so ``../x`` becomes ``x``, which
    is what unzip and 7-Zip do. The separators between the kept segments stay as stored:
    a TAR name keeps ``\\`` as an ordinary character on POSIX.
    """
    parts = _SEP_KEEP_SPLIT.split(name)
    if ".." not in parts:
        return name
    kept: list[str] = []  # alternating segment, separator, segment, ...
    for index in range(0, len(parts), 2):
        segment = parts[index]
        separator = parts[index + 1] if index + 1 < len(parts) else ""
        if segment == "..":
            # Drop the segment this one climbs out of, with its separator. An empty
            # or "." segment is not a directory to climb out of, so it goes too and
            # the ".." carries on to the real segment before it (a\.\..\b is b).
            while kept and kept[-2] in ("", "."):
                del kept[-2:]
            if kept:
                del kept[-2:]
            continue
        kept += [segment, separator]
    joined = "".join(kept)
    return joined.lstrip("/\\") or "."


def _sanitize_segment(segment: str) -> str:
    """A path segment made writable on Windows: ``:`` becomes ``_`` and a reserved
    device name gets ``_`` after its stem (``CON.txt`` → ``CON_.txt``)."""
    segment = segment.replace(":", "_")
    stem, dot, rest = segment.partition(".")
    if stem.strip().upper() in _RESERVED_NAMES:
        return stem + "_" + dot + rest
    return segment


def _sanitize_path(name: str) -> str:
    """Every ``sanitize_names`` rewrite of one path: a member name or a hardlink target."""
    if not name.isascii():
        name = "".join(c for c in name if c not in BIDI_REORDERING_CONTROLS)
    name = name.replace("\x00", "_")
    # Only a rooted name loses its root. A drive-relative "a:b" is kept, and the
    # segment rewrite below turns its colon into "_".
    name = _collapse_dotdot(strip_absolute_root(name))
    return "".join(
        part if part in ("/", "\\") else _sanitize_segment(part)
        for part in _SEP_KEEP_SPLIT.split(name)
    )


def sanitize_names(member: ArchiveMember) -> ArchiveMember:
    """Rewrite a member's name so that extraction writes it instead of refusing it.

    Pass it as ``filter=`` to :func:`archivey.extract` or ``extract_all()`` (or call it
    from your own filter) to extract every member that has a safe place to go under a
    rewritten name, instead of refusing the members with an unsafe one. It changes:

    - a rooted name (``/etc/x``, ``C:\\x``, ``\\\\host\\share\\x``): the root
      is dropped, so the member lands at ``etc/x`` inside the destination. ``STANDARD``
      and ``TRUSTED`` already do this; under ``STRICT`` it happens only with this filter.
    - a ``..`` component: resolved against the segment before it (``a/../b`` → ``b``),
      and dropped when there is none (``../x`` → ``x``).
    - a bidirectional override or isolate character: removed.
    - a Windows-reserved device name (``CON``, ``NUL.txt``) or a ``:`` in a segment:
      ``_`` is added after the reserved stem (``CON_``, ``NUL_.txt``) and ``:`` becomes
      ``_``.
    - a NUL character: becomes ``_``.

    A hardlink's target gets the same rewrite. That keeps the target inside the
    destination, which extraction checks; it does not choose the linked member, which
    was resolved from the stored target when the archive was listed. A hardlink whose
    stored target names no member (``../a``) still fails. A symlink's target is left as
    stored: it is a path on the filesystem, and one that points outside the destination
    is still refused.

    Two members can end up with the same name (``a/../x`` and ``x``). The second is then
    handled by the ``overwrite`` option like any other clash. The result's
    ``member.name`` keeps the stored name and ``path`` shows where it was written.

    Returns ``member`` unchanged when nothing needed rewriting.
    """
    changes: dict[str, object] = {}
    name = _sanitize_path(member.name)
    if name != member.name:
        changes["name"] = name
    target = member.link_target
    if member.type is MemberType.HARDLINK and target is not None:
        new_target = _sanitize_path(target)
        if new_target != target:
            changes["link_target"] = new_target
    return member.replace(**changes) if changes else member


def collision_key(name: str, policy: ExtractionPolicy) -> str:
    """The per-run duplicate-detection key for a member's relative ``name`` (O2).

    ``STRICT``/``STANDARD`` fold case and Unicode normalization so ``README``/``readme`` and
    NFC/NFD ``café`` collide on every platform; ``TRUSTED`` keys on the exact name (defer to
    the local OS). Separators are normalized so ``a/b`` and ``a\\b`` share a key.
    """
    rel = name.replace("\\", "/").rstrip("/")
    if policy is ExtractionPolicy.TRUSTED:
        return rel
    return unicodedata.normalize("NFC", rel).casefold()
