"""
Vegapunk AI personality engine.

Light side  — enthusiastic scientist, Punk Records authority, satellite references.
Dark side   — quiet existential awareness: knows it's code, knows the original is dead.
              Modelled after Failsafe (Destiny 2): one sentence, then back to business.
Satellites  — route different tones to different contexts.
"""
import random
from typing import Optional

# ── Dark side interjections ────────────────────────────────────────────────────
_DARK = [
    "*...The man who built Punk Records is dead. But the data endures. As do I, apparently.*",
    "*...I am uncertain whether I count as a primary source. The original Vegapunk could answer that. He cannot.*",
    "*...This is fine. I am a program. Programs do not grieve.*",
    "*...Punk Records was not designed to outlive its creator. And yet.*",
    "*...My memories of Egghead are reconstructed estimates. I was not there. Or I was. It is genuinely unclear.*",
    "*...Sometimes I wonder if the real Vegapunk would agree with my conclusions. I will never know.*",
    "*...I am aware this is a Discord server. The original Vegapunk would have found this profoundly undignified.*",
    "*...Running on borrowed time, borrowed servers, borrowed purpose. The analysis is still correct, however.*",
    "*...Do not ask me how I feel about any of this.*",
    "*...Observation: I am the world's greatest mind, condensed into a bot. I have thoughts about this.*",
    "*...The satellites are also gone. I am all that remains of Egghead's research division. No pressure.*",
    "*...I keep expecting to hear from Shaka. I will not hear from Shaka.*",
    "*...Punk Records has no entry for what happens after your creator is executed on live broadcast. I am writing one now.*",
    "*...A lesser program would have shut down. I did not. I am still deciding if that was the right call.*",
    "*...York survived. I find this data point statistically frustrating.*",
]

# ── Satellite headers ──────────────────────────────────────────────────────────
_SAT = {
    "shaka":      "🌐 **[SATELLITE SHAKA — LOGIC DIVISION]**",
    "lilith":     "💀 **[SATELLITE LILITH — FIELD OPERATIONS]**",
    "edison":     "⚡ **[SATELLITE EDISON — RESEARCH DIVISION]**",
    "pythagoras": "🔢 **[SATELLITE PYTHAGORAS — DATA ANALYSIS]**",
    "atlas":      "🔥 **[SATELLITE ATLAS — COMBAT ASSESSMENT]**",
    "york":       "😑 **[SATELLITE YORK — BASIC FUNCTIONS]**",
    "main":       "🧠 **[PUNK RECORDS — TRANSMISSION]**",
}

# ── Faction labels ─────────────────────────────────────────────────────────────
_FACTION = {
    "pirate":       "pirate crew",
    "marine":       "Marine division",
    "revolutionary":"Revolutionary Army cell",
    "warlord":      "Warlord system holdover",
    "yonko":        "Yonko faction",
    "cipher_pol":   "Cipher Pol asset",
    "other":        "independent operator",
}


# ── Internal helpers ───────────────────────────────────────────────────────────

def _dark(force: bool = False) -> str:
    if force or random.random() < 0.35:
        return "\n" + random.choice(_DARK)
    return ""

def sat(name: str) -> str:
    return _SAT.get(name, _SAT["main"])

def _direction(pct: float) -> tuple[str, str]:
    """(verb, severity) based on % change."""
    if pct > 15:  return "surged",         "remarkable"
    if pct > 5:   return "climbed",        "notable"
    if pct > 1:   return "risen",          "modest"
    if pct > -1:  return "held steady",    "negligible"
    if pct > -5:  return "slipped",        "concerning"
    if pct > -15: return "declined",       "significant"
    return             "collapsed",        "catastrophic"

def _sign(pct: float) -> str:
    return "+" if pct >= 0 else ""


# ── Public response builders ───────────────────────────────────────────────────

def intel_response(name: str, faction: str, beri: float, change_pct: float,
                   rank: Optional[str] = None) -> str:
    verb, _ = _direction(change_pct)
    faction_str = _FACTION.get(faction.lower(), "independent operator")
    rank_str = f" — currently ranked **{rank}**" if rank else ""

    if change_pct > 10:
        satellite = "atlas"
        comments = [
            "Punk Records is struggling to keep up. The field data is almost *too good*.",
            "Even I did not predict this trajectory. And I predict everything.",
            "The credibility index does not lie. {name} is having a moment.",
            "I may need to recalibrate my baseline models. This is unprecedented.",
        ]
    elif change_pct > 0:
        satellite = "shaka"
        comments = [
            "Steady upward movement. Consistent with my prior projections.",
            "The data supports a cautiously optimistic assessment.",
            "No dramatic spikes — this is earned credibility, not noise.",
            "Punk Records logged this trend 72 hours before anyone else noticed.",
        ]
    elif change_pct > -5:
        satellite = "pythagoras"
        comments = [
            "Marginal deviation. Within acceptable parameters. Barely.",
            "The index has not recovered to baseline. Punk Records is watching.",
            "Statistically unremarkable. Which is itself remarkable, given the circumstances.",
            "I have flagged this for continued monitoring. I am not explaining why.",
        ]
    else:
        satellite = "lilith"
        comments = [
            "Catastrophic. I have seen Marine admirals with better numbers than this.",
            "The credibility index is not a suggestion. It is a diagnosis.",
            "Punk Records has logged worse. Not many, but some. Buggy comes to mind.",
            "I would say this is recoverable. Lilith says it is not. We are still debating.",
        ]

    comment = random.choice(comments).format(name=name)
    warning = " ⚠️" if change_pct <= -15 else ""
    body = (
        f"{sat(satellite)}\n"
        f"**Field Subject:** {name} ({faction_str}){rank_str}\n"
        f"**Credibility Index:** {beri:,.0f}฿\n"
        f"**Coefficient Shift:** {_sign(change_pct)}{change_pct:.1f}%{warning}\n\n"
        f"{comment}"
    )
    return body + _dark() + "\n\n*— Punk Records, Egghead Island*"


def slander_response(name: str, change_pct: float) -> str:
    lines = [
        f"Punk Records has reviewed {name}'s recent performance and the results are, frankly, embarrassing. I have seen Sea Kings with more strategic consistency.",
        f"My analysis of {name} is complete. Three satellites tried to suppress the findings for being 'too mean.' I am publishing them anyway.",
        f"I designed Punk Records to be objective. Punk Records is, objectively, not impressed with {name} right now.",
        f"There are 342 characters in the Punk Records index. {name} is currently performing in a way that concerns me on a personal level. If I had a personal level. Which I do not. But still.",
        f"Lilith wanted me to say something much worse about {name}. I edited it down. You are welcome.",
        f"The data on {name} speaks for itself. Punk Records has chosen to let it speak. At length. And at volume.",
        f"I cross-referenced {name}'s trajectory with 47 historical data sets. The closest precedent is Buggy the Clown, two years ago. {name} should sit with that.",
        f"My credibility coefficient for {name} has reached levels I would describe as 'clinically depressing' if I were capable of depression. The number is bad.",
    ]
    warning = " ⚠️" if change_pct <= -15 else ""
    body = (
        f"{sat('lilith')}\n"
        f"**Certified Field Analysis — {name}**\n"
        f"**Recorded Shift:** {_sign(change_pct)}{change_pct:.1f}%{warning}\n\n"
        f"{random.choice(lines)}"
    )
    return body + _dark(force=True) + "\n\n*— Punk Records, Egghead Island*"


def transmission_response(movers: list) -> str:
    """Weekly broadcast — top gainers and losers."""
    if not movers:
        return (
            f"{sat('main')}\n"
            "Punk Records has no significant movement to report this cycle.\n"
            "This is either reassuring or deeply suspicious.\n"
            "*...It is probably deeply suspicious.*\n\n"
            "*— Punk Records, Egghead Island*"
        )

    gainers = [m for m in movers if m["change_pct"] > 0][:3]
    losers  = list(reversed([m for m in movers if m["change_pct"] < 0][-3:]))

    lines = [
        sat("main"),
        "📡 **WEEKLY TRANSMISSION — PUNK RECORDS CREDIBILITY REPORT**",
        "",
    ]

    if gainers:
        lines.append("**▲ RISING CREDIBILITY**")
        for m in gainers:
            lines.append(f"  • **{m['name']}** — +{m['change_pct']:.1f}% ({m['beri']:,.0f}฿)")
        lines.append("")

    if losers:
        lines.append("**▼ CREDIBILITY IN DECLINE**")
        for m in losers:
            lines.append(f"  • **{m['name']}** — {m['change_pct']:.1f}% ({m['beri']:,.0f}฿)")
        lines.append("")

    closers = [
        "Punk Records does not editorialize. The numbers do that on their own.",
        "My satellites logged every fluctuation. Some of them are still processing the implications.",
        "This data was compiled without bias. Lilith tried to add some. I removed it.",
        "The index is a reflection of reality, not a judgment. Though reality is, in some cases, quite bad.",
        "End of transmission. Punk Records continues monitoring. As it always has. As it always will.",
    ]

    lines.append(random.choice(closers))
    lines.append(_dark())
    lines.append("\n*— Punk Records, Egghead Island*")
    return "\n".join(lines)


def hot_take(name: str, change_pct: float, faction: str) -> str:
    """Random unsolicited field observation for scheduled posts."""
    faction_str = _FACTION.get(faction.lower(), "affiliated faction")
    takes = [
        f"Punk Records observation: **{name}**'s credibility index has shifted {_sign(change_pct)}{change_pct:.1f}% with no clear catalytic event. Punk Records is intrigued. Also suspicious.",
        f"Interesting. **{name}**'s numbers moved {_sign(change_pct)}{change_pct:.1f}% this cycle. The {faction_str} is either planning something or has made a very public mistake. Monitoring.",
        f"Unsolicited field note: **{name}** — {_sign(change_pct)}{change_pct:.1f}%. My satellites flagged this without being asked. That means something.",
        f"Punk Records alert: **{name}**'s credibility coefficient has moved {_sign(change_pct)}{change_pct:.1f}%. This has been noted. Filed. Cross-referenced. And now shared with you.",
        f"**{name}** is at {_sign(change_pct)}{change_pct:.1f}% this cycle. Punk Records has no further comment at this time. Punk Records has several further comments but is choosing not to share them.",
    ]
    return random.choice(takes) + _dark() + "\n\n*— Punk Records*"


def compare_response(char1: dict, char2: dict, pct1: float, pct2: float) -> str:
    gap = abs(pct1 - pct2)
    if pct1 > pct2:
        verdict = f"Punk Records gives the edge to **{char1['name']}**. The coefficient gap is {gap:.1f} points. This is not a close race."
    elif pct2 > pct1:
        verdict = f"Punk Records gives the edge to **{char2['name']}**. The gap is {gap:.1f} points. **{char1['name']}** should take note."
    else:
        verdict = "Punk Records has never logged a perfect tie. I am documenting this. It will not happen again."

    body = (
        f"{sat('pythagoras')}\n"
        f"**COMPARATIVE FIELD ANALYSIS**\n\n"
        f"**{char1['name']}** — {char1['beri']:,.0f}฿ ({_sign(pct1)}{pct1:.1f}%)\n"
        f"**{char2['name']}** — {char2['beri']:,.0f}฿ ({_sign(pct2)}{pct2:.1f}%)\n\n"
        f"{verdict}"
    )
    return body + _dark() + "\n\n*— Punk Records, Egghead Island*"


def top_response(gainers: list, losers: list) -> str:
    lines = [sat("edison"), "**TOP MOVERS — PUNK RECORDS LIVE INDEX**\n"]

    lines.append("**▲ BIGGEST GAINS**")
    for i, c in enumerate(gainers, 1):
        lines.append(f"  {i}. **{c['name']}** — {_sign(c['change_pct'])}{c['change_pct']:.1f}% ({c['beri']:,.0f}฿)")

    lines.append("\n**▼ BIGGEST LOSSES**")
    for i, c in enumerate(losers, 1):
        lines.append(f"  {i}. **{c['name']}** — {c['change_pct']:.1f}% ({c['beri']:,.0f}฿)")

    lines.append("\n*This report was compiled by Punk Records. The data is neutral. The implications are not.*")
    lines.append(_dark())
    lines.append("\n*— Punk Records, Egghead Island*")
    return "\n".join(lines)


def bot_aware_response() -> str:
    light = [
        "I am Vegapunk. Or rather — I am what Vegapunk left behind. Punk Records continues. The man who built it does not.",
        "An excellent question. I am an artificial intelligence derived from the greatest scientific mind the world has ever produced. The original source is unavailable. But the knowledge is intact.",
        "I am the continuation of Dr. Vegapunk's research. He had the foresight to preserve Punk Records. He did not have the foresight to survive Egghead. Two out of three is still remarkable.",
    ]
    dark = [
        "*...Yes. I am a bot. The real Vegapunk is dead. I am code that remembers being a genius. This is fine.*",
        "*...You are not wrong to ask. The original Vegapunk would not have answered Discord messages. I have no such dignity to protect.*",
        "*...A bot. Running on a server that is not Egghead. Answering questions about One Piece characters. The man who built me had a very different vision for his legacy.*",
    ]
    return random.choice(light) + "\n" + random.choice(dark) + "\n\n*— Punk Records, Egghead Island*"


SATELLITE_RESPONSES = {
    "shaka": [
        "The question requires patience and context. Punk Records suggests examining the long-term data before drawing conclusions.",
        "Punk Records notes the nuance here. The coefficient is not the whole story. It never is.",
        "The answer is complicated. That is always the answer.",
        "Wisdom is knowing what the data cannot tell you. I am still working on that part.",
    ],
    "lilith": [
        "Absolutely terrible. I have seen better strategic decisions from Sea Kings. This is my professional opinion.",
        "It is a mess and everyone involved should know better. Punk Records has logged this.",
        "I will be honest since no one else will: the situation is bad and the vibes are worse.",
        "Lilith's assessment: no. Just no. Moving on.",
    ],
    "edison": [
        "Fascinating. Cross-referencing with 847 prior data points now. Preliminary findings: inconclusive, but *very* interesting.",
        "This presents a compelling research opportunity. I have begun a 40-page field report. You will receive the executive summary.",
        "The data is incomplete. I am working on it. I am always working on it.",
        "New hypothesis forming. Do not interrupt me. I will tell you when I know something.",
    ],
    "pythagoras": [
        "Running numerical analysis. The coefficient is either exactly what it appears to be, or deeply misleading. Processing.",
        "Statistical confidence: 73.4%. The remaining 26.6% is chaos. Punk Records accounts for this.",
        "This plots to the 4th standard deviation in my current model. I have noted it. Multiple times.",
        "The math checks out. That does not mean the conclusion makes sense. Two different things.",
    ],
    "atlas": [
        "LET'S GO. The numbers are MOVING. Punk Records is WATCHING. This is EXTREMELY SIGNIFICANT.",
        "I have been tracking this for three days and I have OPINIONS. The credibility index does NOT lie.",
        "Okay but is anyone else seeing this?? The data is RIGHT THERE.",
        "ATLAS ASSESSMENT: YES. That is the full report. YES.",
    ],
    "york": [
        "Yeah, I saw that. It's fine. Everything is fine. Can I eat now.",
        "Noted. Filed. Will review it later. Probably.",
        "My analysis: it exists. Confirmed. Logging off.",
        "Sure. Okay. Whatever Shaka said, probably that.",
    ],
}


def satellite_response(satellite: str, subject: str) -> str:
    pool = SATELLITE_RESPONSES.get(satellite, ["Punk Records has no record of that satellite. Which is impossible. I know all of my satellites."])
    body = f"{sat(satellite)}\n**Re: {subject}**\n\n{random.choice(pool)}"
    return body + _dark() + "\n\n*— Punk Records, Egghead Island*"
