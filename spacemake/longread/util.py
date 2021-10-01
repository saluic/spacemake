import os
from more_itertools import grouper
from collections import OrderedDict, defaultdict
from spacemake.util import rev_comp, fasta_chunks, ensure_path, read_fq


def load_oligos(fname=""):
    if not fname:
        base = os.path.dirname(__file__)
        fname = os.path.join(base, "../data/oligo_blocks.fa")

    blocks = OrderedDict()
    for fa_id, seq in fasta_chunks(open(fname)):
        blocks[fa_id] = seq
        blocks[fa_id + "_RC"] = rev_comp(seq)

    return blocks


def count_dict_collapse_misc(
    counts, misc_thresh=0.01, total=1, add_up=None, sig_intact=None
):
    out_counts = defaultdict(int)
    out_frac = defaultdict(float)

    misc = 0
    sum = 0
    if sig_intact is not None:
        complete = ",".join(sig_intact)
        everything = set(sig_intact)
    else:
        complete = None
        everything = set()

    def relkey(key):
        if sig_intact is None:
            return key

        if key == complete:
            return "complete"

        obs = set(key.split(","))
        there = obs & everything
        extra = obs - everything
        missing = everything - obs

        if len(missing) <= len(there):
            res = "missing_" + ",".join(sorted(missing))
        else:
            res = "only_" + ",".join(sorted(there))
        if extra:
            res += "_extra_" + ",".join(sorted(extra))

        return res

    for key, n in sorted(counts.items()):
        key = relkey(key)
        sum += n
        f = n / float(total)
        if f < misc_thresh:
            misc += n
        else:
            out_counts[key] = n
            out_frac[key] = f

    if misc > 0:
        out_counts["misc"] += misc
        out_frac["misc"] += misc / float(total)

    if add_up is None:
        other = total - sum
    else:
        other = total - counts[add_up]

    if other > 0:
        out_counts["N/A"] += other
        out_frac["N/A"] += other / float(total)

    return out_counts, out_frac


def count_dict_out(counts, title, misc_thresh=0.01, total=1, **kw):
    print(f"### {title}")
    colname = title.replace(" ", "_")
    out_counts, out_frac = count_dict_collapse_misc(counts, misc_thresh, total, **kw)
    for key, count in sorted(out_counts.items(), key=lambda x: -x[1]):
        print(f"{colname}\t{key}\t{count}\t{out_frac[key]:.3f}")

    return out_counts, out_frac


def count_dict_split(counts, pattern, name):
    import re

    out_d = defaultdict(int)
    for key, value in counts.items():
        if re.search(pattern, key):
            out_d[name] += value
        else:
            out_d[key] += value

    return out_d


def count_dict_to_df(counts, kind="", n_total=0):
    keys_values = sorted(counts.items(), key=lambda x: -x[1])
    import pandas as pd

    df = pd.DataFrame(data=keys_values, columns=["name", "count"])
    if n_total:
        df = df.append({"name": "n_total", "count": n_total}, ignore_index=True)
        df["fraction"] = df["count"] / n_total
    if kind:
        df["kind"] = kind
    return df


def count_dict_from_df(df, kind):
    df = df.query(f"kind == '{kind}'")
    # print(df)
    keys = df["name"]
    values = df["count"]
    return dict(zip(keys, values))


def digest_signatures(
    sig_counts,
    bead_related="bead_start",
    complete_signature="P5,bead_start,OP1,polyT,N70X",
    prefixes=[
        "P5",
    ],
    suffixes=[
        "N70X",
    ],
):
    bead_counts = defaultdict(int)
    ov_counts = defaultdict(int)
    n_bead_related = 0

    complete = complete_signature.split(",")
    while complete[0] in prefixes:
        complete.pop(0)

    while complete[-1] in suffixes:
        complete.pop()

    # print(f"complete={complete}")
    complete_set = set(complete)

    def describe(found):
        found_set = set(found)
        missing = complete_set - found_set
        if not missing:
            descr = "complete"
        elif len(missing) < len(found_set):
            descr = f"missing_{','.join(sorted(missing))}"
        else:
            descr = f"only_{','.join(sorted(found_set))}"

        return descr

    def bead_relation(parts):
        search = list(complete)
        found = []
        at = 0

        try:
            i = parts.index(search[0])  # look for first part, e.g. bead_start
        except ValueError:
            i = 0

        for part in parts[i:]:
            # find co-linear matches,
            # ignore extra inserted segments
            # (for now)
            if part in search[at:]:
                found.append(part)
                at = search.index(part)

        return describe(found)

    for sig, count in sig_counts.items():
        parts = sig.split(",")
        if bead_related in parts:
            br = bead_relation(parts)
            bead_counts[br] += count
            n_bead_related += count
        else:
            ov_counts[sig] = count

    ov_counts["bead-related"] = n_bead_related
    return ov_counts, bead_counts
