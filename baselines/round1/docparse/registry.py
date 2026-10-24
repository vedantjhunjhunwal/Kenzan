"""Check registry: the identities every statement of a given type is expected to satisfy.

Coverage = evaluable template checks / expected template checks, so it means the same thing on
every filing. Checks found only in a particular filing (e.g. gross/net PP&E blocks) are run as
"dynamic" checks and reported separately; they never enter the coverage denominator.

Concepts are matched on label_norm (NFKC, no whitespace). Aliases cover common wording variants.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TemplateCheck:
    check_id: str
    kind: str                         # "sum_parsed" | "sum_named" | "equal"
    total: str                        # concept key
    children: tuple[str, ...] = ()    # concept keys (sum_named / equal)
    optional_children: tuple[str, ...] = ()


CONCEPT_ALIASES: dict[str, tuple[str, ...]] = {
    "流動資産合計": ("流動資産合計",),
    "有形固定資産合計": ("有形固定資産合計",),
    "無形固定資産合計": ("無形固定資産合計",),
    "投資その他の資産合計": ("投資その他の資産合計",),
    "固定資産合計": ("固定資産合計",),
    "繰延資産合計": ("繰延資産合計",),
    "資産合計": ("資産合計", "資産の部合計"),
    "流動負債合計": ("流動負債合計",),
    "固定負債合計": ("固定負債合計",),
    "負債合計": ("負債合計", "負債の部合計"),
    "株主資本合計": ("株主資本合計",),
    "純資産合計": ("純資産合計", "純資産の部合計"),
    "負債純資産合計": ("負債純資産合計", "負債及び純資産合計", "負債・純資産合計", "負債純資産の部合計"),
}

# Identities present in every J-GAAP consolidated balance sheet (optional sections such as
# 繰延資産 or その他の包括利益累計額 are not template checks; they appear as dynamic checks).
JGAAP_CONSOLIDATED_BS: tuple[TemplateCheck, ...] = (
    TemplateCheck("T01", "sum_parsed", "流動資産合計"),
    TemplateCheck("T02", "sum_parsed", "有形固定資産合計"),
    TemplateCheck("T03", "sum_parsed", "無形固定資産合計"),
    TemplateCheck("T04", "sum_parsed", "投資その他の資産合計"),
    TemplateCheck("T05", "sum_named", "固定資産合計",
                  ("有形固定資産合計", "無形固定資産合計", "投資その他の資産合計")),
    TemplateCheck("T06", "sum_named", "資産合計", ("流動資産合計", "固定資産合計"), ("繰延資産合計",)),
    TemplateCheck("T07", "sum_parsed", "流動負債合計"),
    TemplateCheck("T08", "sum_parsed", "固定負債合計"),
    TemplateCheck("T09", "sum_named", "負債合計", ("流動負債合計", "固定負債合計")),
    TemplateCheck("T10", "sum_parsed", "株主資本合計"),
    TemplateCheck("T11", "sum_parsed", "純資産合計"),
    TemplateCheck("T12", "sum_named", "負債純資産合計", ("負債合計", "純資産合計")),
    TemplateCheck("T13", "equal", "資産合計", ("負債純資産合計",)),
)

TEMPLATES = {
    "consolidated_balance_sheet": JGAAP_CONSOLIDATED_BS,
    "balance_sheet": JGAAP_CONSOLIDATED_BS,   # same identities for the non-consolidated BS
}

# Totals whose children are fixed by definition (used by hierarchy when no header matches).
NAMED_TOTALS = {t.total: t.children + t.optional_children
                for tpl in TEMPLATES.values() for t in tpl if t.kind == "sum_named"}


def concept_of(label_norm: str) -> str | None:
    for concept, aliases in CONCEPT_ALIASES.items():
        if label_norm in aliases:
            return concept
    return None
