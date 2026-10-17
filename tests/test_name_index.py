"""The listing's name index: one member per name, a list only for duplicates."""

from __future__ import annotations

import io
import warnings
import zipfile

import archivey


def _zip(names: list[str]) -> bytes:
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # zipfile warns on duplicates
        with zipfile.ZipFile(buf, "w") as zf:
            for i, name in enumerate(names):
                zf.writestr(name, f"{i}")
    return buf.getvalue()


def test_index_holds_duplicates_in_listing_order_and_drops_by_identity() -> None:
    with archivey.open_archive(io.BytesIO(_zip(["a", "b", "a", "a"]))) as reader:
        members = reader.members()
        first, second, third = (m for m in members if m.name == "a")
        assert list(reader._listed_named("a")) == [first, second, third]
        assert list(reader._listed_named("b")) == [members[1]]
        assert reader._listed_named("missing") == ()
        # A name listed once holds the member itself: no list object per member.
        assert reader._listed_by_name["b"] is members[1]

        # The middle one goes by identity, though an equal member stays.
        reader._unindex_member_name(second, "a")
        held = reader._listed_named("a")
        assert len(held) == 2 and held[0] is first and held[1] is third
        reader._unindex_member_name(first, "a")
        held = reader._listed_named("a")
        assert len(held) == 1 and held[0] is third
        assert reader._listed_by_name["a"] is third
        assert reader._last_by_exact_name("a") is third
        reader._unindex_member_name(third, "a")
        assert reader._listed_named("a") == ()
        assert reader._last_by_exact_name("a") is None
        # A member that is not in the entry leaves it as it is.
        reader._unindex_member_name(first, "b")
        assert list(reader._listed_named("b")) == [members[1]]
