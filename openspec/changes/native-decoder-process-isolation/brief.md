<!--
The "coffee brief": a spoken-word-friendly summary of this change, readable (or
read aloud) in under a minute. Prose only — NO tables, NO code blocks, minimal
symbols — so text-to-speech reads cleanly. Aim for ~200–280 words. Derive it from
proposal.md / design.md / tasks.md; do not introduce new decisions here.
-->

# native-decoder-process-isolation — One setting for where native decoders run

**Status:** Approved in outline by the maintainer on 2026-10-20; being implemented.

**Why it matters:** Two native decoders already run in child processes because they crash on hostile input: rapidgzip, and PPMd for large members. Every other native decoder runs in the caller's process, so a fault in any of them takes that process down. Callers reading untrusted archives cannot ask for more protection, and callers with trusted files, or without the ability to start processes, cannot ask for less.

**What it does:** A new setting, process isolation, has three values. Known crashes, the default, keeps today's behaviour. Native decoders puts every native decoder in a child process, including the trial decodes that format detection runs. None runs everything in the caller's process, and the docs discourage it.

Each open decoder gets a child of its own, so a crash is charged to exactly the member that caused it and threads still decode in parallel. Children whose decoder finished without an error wait in a small pool and are reused, so a run of small members does not start a process each. One generic worker serves every codec and imports only the library it is asked for. The PPMd child moves onto it, and rapidgzip shares its framing and helpers.

**What stays in the caller's process:** checksums and AES decryption. There is no time limit per call; for a hard limit on a whole operation, the docs show how to run the command-line tool in a subprocess with a timeout.

**Cost:** about one and a half times slower for zlib, a few percent for bzip2 and xz, about five times for zstd, under native decoders only.
