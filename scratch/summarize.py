import glob

rows = []
for p in sorted(glob.glob("memlog-*.tsv")):
    w = p[7:-4]
    for line in open(p):
        f = line.rstrip("\n").split("\t")
        # w, rss, drss, vm, fds, big, nodeid, total_priv, dbig, gc...
        rows.append((w, int(f[1]), int(f[2]), int(f[3]), int(f[4]), int(f[5]), f[6], int(f[7]), int(f[8]), f[9:]))
for w in sorted({r[0] for r in rows}):
    wr = [r for r in rows if r[0] == w]
    last = wr[-1]
    print(f"worker {w}: tests {len(wr)} final rss {last[1]}MB vm {last[3]}MB biggest-vma {last[5]}MB peak rss {max(r[1] for r in wr)}MB peak vma {max(r[5] for r in wr)}MB")
print("== top 25 RSS jumps")
for r in sorted(rows, key=lambda r: -r[2])[:25]:
    print(r[0], f"+{r[2]}MB", f"rss={r[1]}", f"vm={r[3]}", f"vma={r[5]}", r[6][:140], *r[9])
print("== top 30 largest-VMA growth")
for r in sorted(rows, key=lambda r: -r[8])[:30]:
    print(r[0], f"vma+{r[8]}MB", f"vma={r[5]}", f"rss={r[1]}", r[6][:150])
print("== tests after which the biggest private writable VMA exceeds 4 GB (first 40)")
n = 0
for r in rows:
    if r[5] > 4096:
        print(r[0], f"vma={r[5]}", r[6][:150])
        n += 1
        if n >= 40:
            break
print("== RSS by module (sum of positive deltas)")
mod = {}
for r in rows:
    m = r[6].split("::")[0]
    mod[m] = mod.get(m, 0) + max(r[2], 0)
for m, v in sorted(mod.items(), key=lambda kv: -kv[1])[:20]:
    print(f"{v:7d} MB {m}")
print("== per-worker every 250 tests: rss / vm / biggest vma / gc")
for w in sorted({r[0] for r in rows}):
    wr = [r for r in rows if r[0] == w]
    print(w, "rss", " ".join(str(r[1]) for r in wr[::250]))
    print(w, "vm ", " ".join(str(r[3]) for r in wr[::250]))
    print(w, "vma", " ".join(str(r[5]) for r in wr[::250]))
    if wr[0][9]:
        print(w, "gc ", " | ".join(r[9][0] for r in wr[::250]))
for p in sorted(glob.glob("enomem-*.txt")):
    print("==", p)
    print(open(p).read())
