"""Report every disagreement between the visual transcription and the text layer."""
import json
import sys
import unicodedata

from textlayer_rows import read_rows

DASHES = set("－―—ー−-‐")


def diff(pdf, vis_path):
    vis = json.load(open(vis_path, encoding="utf-8"))["rows"]
    text, levels = read_rows(pdf)
    out = []
    if len(vis) != len(text):
        out.append(("ROWCOUNT", f"visual={len(vis)} text={len(text)}"))
    for i, (v, t) in enumerate(zip(vis, text), 1):
        if v["label"] != t["label"]:
            eq = unicodedata.normalize("NFKC", v["label"]) == unicodedata.normalize("NFKC", t["label"])
            out.append((i, f"label visual={v['label']!r} text={t['label']!r} nfkc_equal={eq}"))
        if v["level"] != t["level"]:
            out.append((i, f"level visual={v['level']} text={t['level']} (x0={t['x0']})"))
        for col in ("prior", "current"):
            a = v[col]
            tc = t["cells"].get(col)
            b = tc["raw"] if tc else ""
            if a != b:
                if a == "DASH" and len(b) == 1 and b in DASHES:
                    out.append((i, f"{col}: visual=DASH text={b!r} U+{ord(b):04X} -> codepoint not "
                                   "decidable from pixels; semantics agree (nil)"))
                else:
                    out.append((i, f"{col}: visual={a!r} text={b!r}  ** VALUE DISAGREEMENT **"))
            ul_t = bool(tc and tc["underlined"])
            if bool(v.get("ul_" + col)) != ul_t:
                out.append((i, f"underline {col}: visual={bool(v.get('ul_' + col))} text={ul_t}"))
    return out, len(vis), len(text), levels


if __name__ == "__main__":
    d, nv, nt, levels = diff(sys.argv[1], sys.argv[2])
    print(f"rows: visual={nv} text_layer={nt}  indent x0 levels={levels}")
    print(f"disagreements: {len(d)}")
    for x in d:
        print(" ", *x)
