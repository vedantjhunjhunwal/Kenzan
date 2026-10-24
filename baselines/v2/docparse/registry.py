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
    sections: tuple[str, ...] = ()    # statement sections the check needs (for acceptance)


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
    TemplateCheck("T01", "sum_parsed", "流動資産合計", sections=("資産",)),
    TemplateCheck("T02", "sum_parsed", "有形固定資産合計", sections=("資産",)),
    TemplateCheck("T03", "sum_parsed", "無形固定資産合計", sections=("資産",)),
    TemplateCheck("T04", "sum_parsed", "投資その他の資産合計", sections=("資産",)),
    TemplateCheck("T05", "sum_named", "固定資産合計",
                  ("有形固定資産合計", "無形固定資産合計", "投資その他の資産合計"), sections=("資産",)),
    TemplateCheck("T06", "sum_named", "資産合計", ("流動資産合計", "固定資産合計"), ("繰延資産合計",),
                  sections=("資産",)),
    TemplateCheck("T07", "sum_parsed", "流動負債合計", sections=("負債",)),
    TemplateCheck("T08", "sum_parsed", "固定負債合計", sections=("負債",)),
    TemplateCheck("T09", "sum_named", "負債合計", ("流動負債合計", "固定負債合計"), sections=("負債",)),
    TemplateCheck("T10", "sum_parsed", "株主資本合計", sections=("純資産",)),
    TemplateCheck("T11", "sum_parsed", "純資産合計", sections=("純資産",)),
    TemplateCheck("T12", "sum_named", "負債純資産合計", ("負債合計", "純資産合計"), sections=("負債", "純資産")),
    TemplateCheck("T13", "equal", "資産合計", ("負債純資産合計",), sections=("資産", "負債", "純資産")),
)

TEMPLATES = {
    "consolidated_balance_sheet": JGAAP_CONSOLIDATED_BS,
    "balance_sheet": JGAAP_CONSOLIDATED_BS,   # same identities for the non-consolidated BS
    "balance_sheet_unspecified": JGAAP_CONSOLIDATED_BS,   # BS recognised from content, title unread
}

# Labels (label_norm, exact match) that show a balance-sheet section is on the page. Used to infer
# the statement type when the title is unreadable, and by the acceptance rule.
SECTION_MARKERS: dict[str, frozenset[str]] = {
    "資産": frozenset({"資産の部", "流動資産", "固定資産", "有形固定資産", "無形固定資産", "投資その他の資産",
                       "繰延資産", "流動資産合計", "有形固定資産合計", "無形固定資産合計", "投資その他の資産合計",
                       "固定資産合計", "繰延資産合計", "資産合計", "資産の部合計"}),
    "負債": frozenset({"負債の部", "流動負債", "固定負債", "流動負債合計", "固定負債合計", "負債合計",
                       "負債の部合計", "負債純資産合計", "負債及び純資産合計"}),
    "純資産": frozenset({"純資産の部", "株主資本", "株主資本合計", "その他の包括利益累計額",
                         "その他の包括利益累計額合計", "純資産合計", "純資産の部合計", "負債純資産合計",
                         "負債及び純資産合計"}),
}
MIN_CONTENT_HITS = 2   # distinct marker labels needed before calling a page a balance sheet


def sections_present(label_norms: list[str]) -> set[str]:
    labels = set(label_norms)
    return {sec for sec, marks in SECTION_MARKERS.items() if labels & marks}


def infer_statement_type(label_norms: list[str]) -> str | None:
    """Content-based fallback when the title is not recognised: a balance sheet if at least
    MIN_CONTENT_HITS distinct section-marker labels appear. Consolidated vs non-consolidated cannot
    be told from the body, hence 'balance_sheet_unspecified'."""
    hits = set(label_norms) & frozenset().union(*SECTION_MARKERS.values())
    return "balance_sheet_unspecified" if len(hits) >= MIN_CONTENT_HITS else None

# Totals whose children are fixed by definition (used by hierarchy when no header matches).
NAMED_TOTALS = {t.total: t.children + t.optional_children
                for tpl in TEMPLATES.values() for t in tpl if t.kind == "sum_named"}


def concept_of(label_norm: str) -> str | None:
    for concept, aliases in CONCEPT_ALIASES.items():
        if label_norm in aliases:
            return concept
    return None
