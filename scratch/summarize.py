import glob

rows = []
for p in sorted(glob.glob("memlog-*.tsv")):
    w = p[7:-4]
    for line in open(p):
        f = line.rstrip("\n").split("\t")
        rows.append((w, int(f[1]), int(f[2]), int(f[3]), int(f[4]), int(f[5]), f[6], f[7:]))
for w in sorted({r[0] for r in rows}):
    wr = [r for r in rows if r[0] == w]
    last = wr[-1]
    print(f"worker {w}: tests {len(wr)} final rss {last[1]}MB vmsize {last[3]}MB fds {last[4]} threads {last[5]} peak rss {max(r[1] for r in wr)}MB")
print("== top 40 RSS jumps")
for r in sorted(rows, key=lambda r: -r[2])[:40]:
    print(r[0], f"+{r[2]}MB", f"rss={r[1]}", f"vm={r[3]}", r[6][:140], *r[7])
print("== RSS by module (sum of positive deltas)")
mod = {}
for r in rows:
    m = r[6].split("::")[0]
    mod[m] = mod.get(m, 0) + max(r[2], 0)
for m, v in sorted(mod.items(), key=lambda kv: -kv[1])[:30]:
    print(f"{v:7d} MB {m}")
print("== per-worker RSS / vmsize / fds / threads every 250 tests")
for w in sorted({r[0] for r in rows}):
    wr = [r for r in rows if r[0] == w]
    print(w, "rss", " ".join(str(r[1]) for r in wr[::250]))
    print(w, "vm ", " ".join(str(r[3]) for r in wr[::250]))
    print(w, "fds", " ".join(str(r[4]) for r in wr[::250]))
    print(w, "thr", " ".join(str(r[5]) for r in wr[::250]))
    if wr[0][7]:
        print(w, "gc ", " | ".join("/".join(r[7]) for r in wr[::250]))
