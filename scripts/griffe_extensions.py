"""Griffe extensions that improve how the API reference renders.

Used by mkdocstrings (see ``mkdocs.yml``). Four small transforms:

- ``PropertyFieldExtension`` — fold ``@property`` accessors into the class's
  parameters/fields table (marked *(computed property)*) instead of giving each one its
  own section.
- ``RenameParametersSectionForDataclasses`` — title a dataclass's parameter table
  "Fields:" rather than "Parameters:".
- ``EnumMembersAsTable`` — render enum members as a Name/Value/Description table (the
  built-in Griffe sections render a "Type" column, which is wrong for enums). Also handles
  ``ArchiveFormat``, whose named ``ClassVar`` instances behave like enum members.
- ``SphinxRolesToAutorefs`` — turn the Sphinx roles the docstrings use
  (``:class:`~archivey.ArchiveMember```) into links, instead of leaking them as text.
"""

from __future__ import annotations

import contextlib
import html
import re
from collections.abc import Iterator
from typing import Any

from griffe import (
    AliasResolutionError,
    Attribute,
    Class,
    CyclicAliasError,
    DocstringParameter,
    DocstringSection,
    DocstringSectionParameters,
    ExprName,
    Extension,
    Module,
    NameResolutionError,
    Object,
)


class PropertyFieldExtension(Extension):
    def on_class_members(
        self, node: Any, cls: Class, agent: Any, **kwargs: Any
    ) -> None:
        properties = {
            k: v for k, v in cls.attributes.items() if v.has_labels("property")
        }
        if not properties:
            return

        if cls.docstring and cls.docstring.parsed:
            parameters = [
                DocstringParameter(
                    name=k,
                    description="*(computed property)* "
                    + (v.docstring.value if v.docstring else ""),
                    annotation=v.annotation,
                )
                for k, v in properties.items()
            ]

            parameters_section = next(
                (
                    section
                    for section in cls.docstring.parsed
                    if isinstance(section, DocstringSectionParameters)
                ),
                None,
            )
            if not parameters_section:
                parameters_section = DocstringSectionParameters(value=[])
                cls.docstring.parsed.append(parameters_section)

            parameters_section.value.extend(parameters)

            # Remove properties from cls.members so they don't get separate sections.
            for name in properties:
                cls.members.pop(name, None)


class RenameParametersSectionForDataclasses(Extension):
    def on_class_instance(
        self, node: Any, cls: Class, agent: Any, **kwargs: Any
    ) -> None:
        if not cls.has_labels or not cls.has_labels("dataclass"):
            return

        if not cls.docstring or not cls.docstring.parsed:
            return

        for section in cls.docstring.parsed:
            if (
                isinstance(section, DocstringSectionParameters)
                and section.title is None
            ):
                section.title = "Fields:"


ENUM_BASES = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"}


def _is_enum_class(cls: Class) -> bool:
    # Any base canonical path matching Python enums.
    if any(getattr(b, "canonical_name", None) in ENUM_BASES for b in cls.bases or ()):
        return True

    flag = cls.members.get("__enum_like__")
    return isinstance(flag, Attribute) and flag.value in (True, "True")


class EnumMembersAsTable(Extension):
    # The built-in Griffe docstring sections are not appropriate for enum classes. The
    # closest, DocstringSectionOtherParameters, renders a "Type" column instead of a
    # "Value" one. So we stash the enum members in cls.extra and override the class
    # template (docs_templates/python/material/class.html.jinja) to render them as a table.

    def on_class_members(
        self, node: Any, cls: Class, agent: Any, **kwargs: Any
    ) -> None:
        if not _is_enum_class(cls):
            return
        rows: list[dict[str, Any]] = []
        for name, m in list(cls.members.items()):
            if m.kind.value == "attribute" and not name.startswith("_"):
                assert isinstance(m, Attribute)
                # The second condition handles ArchiveFormat, which is not an enum class
                # but has ClassVar instances that act like enum values. The rendering is
                # imperfect (values come through empty) but better than nothing.
                if (m.value is not None and m.annotation is None) or (
                    isinstance(m.annotation, ExprName)
                    and m.annotation.canonical_path == cls.canonical_path
                ):
                    rows.append(
                        {
                            "name": name,
                            "value": m.value,
                            "doc": (m.docstring.value if m.docstring else ""),
                        }
                    )
                    cls.members.pop(name, None)
        if rows:
            cls.extra.setdefault("enum_members", {"rows": rows})


# Sphinx cross-reference roles: ``:class:`~archivey.ArchiveMember```, ``:meth:`x.y```.
# The optional ``py:`` domain prefix is accepted; the target may wrap across lines.
_SPHINX_ROLE_RE = re.compile(
    r":(?:py:)?(?P<role>class|func|meth|attr|data|const|exc|mod|obj|any):`(?P<target>[^`]+)`"
)


# Marks the inline code a role became, so ``scripts/check_docs_rendered.py`` can tell a
# role that failed to resolve from mkdocstrings' own optional references (annotations).
ROLE_CODE_CLASS = "sphinx-role"


class SphinxRolesToAutorefs(Extension):
    """Render Sphinx cross-reference roles as links instead of leaking them as text.

    The docstrings use Sphinx roles (``:class:`~archivey.ArchiveMember```), which
    mkdocstrings does not understand: without this they reach the published page as
    literal ``:class:~archivey.ArchiveMember``. Each role becomes an *optional* autoref,
    which links when the target is documented on the site and otherwise renders as plain
    inline code. Optional means an unresolvable target (an internal helper, a stdlib
    class) never fails ``mkdocs build --strict``.

    Targets are resolved the way Sphinx would: a dotted path is used as written, a bare
    name is looked up in the scope of the object whose docstring holds it, and a leading
    ``~`` shows only the last component. The resolved path is then mapped to the public
    ``archivey.<Name>`` path the API page documents, because members of a class are
    anchored only under that path, not under the class's defining module.

    This runs on ``on_package``, after every object exists, so bare names can resolve.
    By then other extensions may already have parsed a docstring (and edited the parsed
    sections), so both the raw value and any parsed sections are rewritten.
    ``scripts/check_docs_rendered.py`` checks the built site for any role that survives,
    and for any role target that did not resolve to a link and is not on its list of
    targets known to have no anchor.
    """

    def on_package(self, *, pkg: Module, **kwargs: Any) -> None:
        public = _public_paths(pkg)
        for obj in _walk(pkg):
            _rewrite_object(obj, public)


def _walk(obj: Object) -> Iterator[Object]:
    yield obj
    for member in obj.members.values():
        if member.is_alias:
            continue
        assert isinstance(member, Object)
        yield from _walk(member)


def _public_paths(pkg: Module) -> dict[str, str]:
    """Map the defining path of each top-level re-export to its public path."""
    public: dict[str, str] = {}
    for name, member in pkg.members.items():
        if not member.is_alias or name.startswith("_"):
            continue
        with contextlib.suppress(AliasResolutionError, CyclicAliasError):
            target = member.final_target
            # Skip modules: the package can see itself as a member named "archivey".
            if not target.is_module:
                public[target.path] = f"{pkg.path}.{name}"
    return public


def _exists(obj: Object, path: str) -> bool:
    try:
        obj.modules_collection[path]
    except (KeyError, AliasResolutionError, CyclicAliasError):
        return False
    return True


def _resolve(obj: Object, target: str, public: dict[str, str]) -> str:
    if _exists(obj, target):
        path = target
    else:
        head, dot, rest = target.partition(".")
        try:
            path = obj.resolve(head) + dot + rest
        except NameResolutionError:
            path = target
    # Map a module-internal prefix (archivey.config.ArchiveyConfig) to the public one
    # (archivey.ArchiveyConfig), longest prefix first.
    parts = path.split(".")
    for i in range(len(parts), 0, -1):
        prefix = ".".join(parts[:i])
        if prefix in public:
            return ".".join([public[prefix], *parts[i:]])
    return path


def _render_role(obj: Object, match: re.Match[str], public: dict[str, str]) -> str:
    role = match["role"]
    target = " ".join(match["target"].split())
    # Explicit title: :class:`the reader <archivey.ArchiveReader>`.
    title_match = re.fullmatch(r"(?P<title>.+?)\s*<(?P<target>[^<>]+)>", target)
    if title_match:
        title, target = title_match["title"], title_match["target"]
    else:
        title = None
    # A leading "!" suppresses the link in Sphinx.
    if target.startswith("!"):
        return f"<code>{html.escape(title or target[1:])}</code>"
    short = target.startswith("~")
    target = target.lstrip("~").removesuffix("()")
    if title is None:
        title = target.rsplit(".", 1)[-1] if short else target
        if role in {"func", "meth"}:
            title += "()"
    identifier = _resolve(obj, target, public)
    return (
        f'<autoref identifier="{html.escape(identifier)}" optional>'
        f'<code class="{ROLE_CODE_CLASS}">{html.escape(title)}</code></autoref>'
    )


def _rewrite_text(obj: Object, text: str, public: dict[str, str]) -> str:
    return _SPHINX_ROLE_RE.sub(lambda m: _render_role(obj, m, public), text)


def _rewrite_object(obj: Object, public: dict[str, str]) -> None:
    docstring = obj.docstring
    if docstring is not None:
        docstring.value = _rewrite_text(obj, docstring.value, public)
        # ``parsed`` is a cached property: rewrite it only if something already parsed it.
        for section in docstring.__dict__.get("parsed", ()):
            _rewrite_section(obj, section, public)
    # EnumMembersAsTable copies member docstrings into cls.extra before this runs.
    enum_members = obj.extra.get("enum_members")
    if enum_members:
        for row in enum_members["rows"]:
            row["doc"] = _rewrite_text(obj, row["doc"], public)


def _rewrite_section(
    obj: Object, section: DocstringSection, public: dict[str, str]
) -> None:
    value = section.value
    if isinstance(value, str):
        section.value = _rewrite_text(obj, value, public)
        return
    items = value if isinstance(value, list) else [value]
    for item in items:
        description = getattr(item, "description", None)
        if isinstance(description, str):
            item.description = _rewrite_text(obj, description, public)
