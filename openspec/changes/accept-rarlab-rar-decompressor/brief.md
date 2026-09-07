Status: ready to implement. Specs name RARLAB unrar or rar as the decompressor; the code and docs land in the same PR.

Why it matters: Ubuntu apt install rar does not put unrar on PATH. Callers who already have the RARLAB writer still got PackageNotInstalledError, even though rar p matched unrar p on the argv archivey actually spawns.

What it does: the finder probes unrar first, then rar. A RAR x.yy Alexander Roshal banner is accepted at the same 6.0 floor. A RAR token is not taken from inside UNRAR. Lookalikes stay refused. Reads still spawn only p, never extract to disk.

Decided: prefer unrar when both are usable; fall through from a lookalike or too-old unrar to rar; Windows Rar.exe banner is unmeasured and documented as such.

Your call later: whether to treat a measured Windows Rar.exe banner as a follow-up, and whether unar ever becomes an explicit opt-in engine.

Bottom line: apt install rar is enough for member data; unar, 7z, and unrar-free are still not substitutes.
