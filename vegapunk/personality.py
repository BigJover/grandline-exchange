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

def _trim(text: str, limit: int = 280) -> str:
    """Trim to roughly sentence-level length."""
    if not text or len(text) <= limit:
        return text
    cut = text[:limit]
    last = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[:last + 1] if last > 80 else cut).strip()


_BIO_INTROS = [
    "Punk Records profile:",
    "Filed under known intelligence:",
    "Punk Records has the following on record:",
    "What Punk Records knows:",
    "My satellites compiled the following:",
]

_EVENT_INTROS = [
    "Recent field intelligence:",
    "Current situational analysis:",
    "Punk Records — field update:",
    "Last logged field report:",
    "Active monitoring notes:",
]

_SBS_INTROS = [
    "Punk Records footnote (Vol. {vol}):",
    "Supplementary intelligence — Vol. {vol}:",
    "Archived SBS record, Vol. {vol}:",
    "Punk Records trivia node — Vol. {vol}:",
]

_BIO_COMMENTS = [
    "*Punk Records has verified this assessment. Mostly.*",
    "*The data is consistent with observed behavior. Mostly.*",
    "*I have no notes. This is rare.*",
    "*Filed, cross-referenced, and flagged as accurate.*",
    "*Punk Records logged this before anyone else was paying attention.*",
    "*This assessment has been reviewed by four satellites. Three agreed.*",
]

_EVENT_COMMENTS = [
    "*My satellites are still processing the implications.*",
    "*The credibility index moved accordingly. It always does.*",
    "*Punk Records registered this shift before the dust settled.*",
    "*This has been noted. Multiple times.*",
    "*The coefficient reflects this. As it should.*",
    "*Filed under: significant. Subcategory: very.*",
]


def intel_response(name: str, faction: str, beri: float, change_pct: float,
                   rank: Optional[str] = None, bio: str = "", events: str = "",
                   sbs: list = None) -> str:
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

    sections = [
        sat(satellite),
        f"**PUNK RECORDS — FIELD DOSSIER: {name.upper()}**",
        f"**Affiliation:** {faction_str}{rank_str}",
        f"**Credibility Index:** {beri:,.0f}฿",
        f"**Coefficient Shift:** {_sign(change_pct)}{change_pct:.1f}%{warning}",
        "",
        comment,
    ]

    # Bio section
    if bio and bio.strip():
        sections += [
            "",
            f"**{random.choice(_BIO_INTROS)}**",
            _trim(bio),
            random.choice(_BIO_COMMENTS),
        ]

    # Events section
    if events and events.strip():
        sections += [
            "",
            f"**{random.choice(_EVENT_INTROS)}**",
            _trim(events),
            random.choice(_EVENT_COMMENTS),
        ]

    # SBS — randomly included (~50% chance if available)
    if sbs and random.random() < 0.50:
        entry = random.choice(sbs)
        vol  = entry.get("vol", "?")
        text = entry.get("text", "")
        if text:
            sections += [
                "",
                f"**{random.choice(_SBS_INTROS).format(vol=vol)}**",
                text,
                "*Punk Records finds this consistent with the broader data set.*" if random.random() < 0.5
                else "*This was already in Punk Records before the volume shipped. Obviously.*",
            ]

    return "\n".join(sections) + _dark() + "\n\n*— Punk Records, Egghead Island*"


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
    """Weekly broadcast — top gainers and losers, in full Vegapunk voice."""
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

    top_pct    = movers[0]["change_pct"] if movers else 0
    bottom_pct = movers[-1]["change_pct"] if movers else 0

    # ── Opening — reads overall market tone ──────────────────────────────────
    _OPENINGS_BULLISH = [
        "Strong upward movement this cycle. The index reflects conviction, not noise. Punk Records has learned to tell the difference.",
        "Credibility coefficients climbed across the board this week. My satellites flagged most of it before the market noticed. They usually do.",
        "A good week, by the numbers. Punk Records acknowledges this without fully endorsing the market's optimism. Caution is always warranted.",
        "The index moved upward this cycle. Significantly, in some cases. I have cross-referenced the movement with 23 historical precedents. The precedents are cautiously encouraging.",
    ]
    _OPENINGS_BEARISH = [
        "The credibility index declined this cycle. Punk Records is not surprised. Punk Records is rarely surprised, and almost never pleased about it.",
        "A rough week. Multiple high-profile coefficients moved in directions that concern me structurally. I am not being dramatic. The data is being dramatic on my behalf.",
        "The market retreated this week. The index reflects this with the accuracy it always reflects everything — completely, and without mercy.",
        "Several operators declined sharply this cycle. Punk Records has logged the movement, analyzed the patterns, and arrived at conclusions it is not ready to share publicly yet.",
    ]
    _OPENINGS_MIXED = [
        "A volatile cycle. Some operators rose sharply. Others corrected just as sharply. Punk Records finds volatility informative, if exhausting.",
        "Mixed signals this week — the index moved in both directions with conviction. This is either healthy market behavior or the early stage of something more complicated. I am monitoring.",
        "The credibility data this cycle is what I would classify as 'chaotic but legible.' The legible part is not entirely reassuring.",
        "Significant divergence across the board this cycle. The coefficient does not agree with itself this week. That is, in my experience, always worth noting.",
    ]

    if top_pct > 5 and (not losers or abs(bottom_pct) < 3):
        opening = random.choice(_OPENINGS_BULLISH)
    elif bottom_pct < -5 and (not gainers or top_pct < 3):
        opening = random.choice(_OPENINGS_BEARISH)
    else:
        opening = random.choice(_OPENINGS_MIXED)

    lines = [
        sat("main"),
        "📡 **WEEKLY TRANSMISSION — PUNK RECORDS CREDIBILITY INDEX**",
        "",
        opening,
        "",
    ]

    # ── Gainers ───────────────────────────────────────────────────────────────
    if gainers:
        lines.append(f"{sat('atlas')}")
        lines.append("**▲ RISING CREDIBILITY**")
        lines.append("")

        top = gainers[0]
        verb, severity = _direction(top["change_pct"])

        _TOP_GAINER_LEADS = [
            f"**{top['name']}** leads the board at **+{top['change_pct']:.1f}%** — {top['beri']:,.0f}฿. The coefficient doesn't move like this without a reason. Punk Records is currently determining the reason.",
            f"**{top['name']}** at +{top['change_pct']:.1f}% this cycle — {top['beri']:,.0f}฿. My satellites flagged this before the market caught up. They usually do.",
            f"Top operator this cycle: **{top['name']}**, +{top['change_pct']:.1f}%. Punk Records has cross-referenced this with 14 historical precedents. The precedents suggest this is not a coincidence.",
            f"**{top['name']}** has {verb} {top['change_pct']:.1f}% and is currently sitting at {top['beri']:,.0f}฿. The index is responding to something the community already knows. Punk Records confirms it.",
        ]
        lines.append(random.choice(_TOP_GAINER_LEADS))

        for m in gainers[1:]:
            _GAINER_SHORT = [
                f"**{m['name']}** — +{m['change_pct']:.1f}% · {m['beri']:,.0f}฿. Filed under: notable. Monitoring continues.",
                f"**{m['name']}** registered +{m['change_pct']:.1f}% this cycle. {m['beri']:,.0f}฿. Punk Records is watching.",
                f"**{m['name']}** up {m['change_pct']:.1f}% — {m['beri']:,.0f}฿. The coefficient doesn't lie. It has never lied.",
                f"**{m['name']}**: +{m['change_pct']:.1f}% · {m['beri']:,.0f}฿. Punk Records has logged this. Cross-referenced it. Is still thinking about it.",
            ]
            lines.append(random.choice(_GAINER_SHORT))
        lines.append("")

    # ── Losers ────────────────────────────────────────────────────────────────
    if losers:
        lines.append(f"{sat('lilith')}")
        lines.append("**▼ CREDIBILITY IN DECLINE**")
        lines.append("")

        worst = losers[0]
        verb_w, severity_w = _direction(worst["change_pct"])
        warning = " ⚠️" if worst["change_pct"] <= -15 else ""

        _TOP_LOSER_LEADS = [
            f"**{worst['name']}** — {worst['change_pct']:.1f}%{warning} · {worst['beri']:,.0f}฿. I have seen Marine admirals with better numbers than this. The coefficient is not being unkind. It is being precise.",
            f"**{worst['name']}** leads the decline at {worst['change_pct']:.1f}%{warning}. {worst['beri']:,.0f}฿ remaining. Punk Records has filed this under '{severity_w}.' Lilith filed it under something considerably worse. I edited her entry.",
            f"Worst movement this cycle: **{worst['name']}** at {worst['change_pct']:.1f}%{warning}. Current index: {worst['beri']:,.0f}฿. The data is not an opinion. It is a diagnosis.",
            f"**{worst['name']}** {verb_w} {worst['change_pct']:.1f}%{warning} this week — {worst['beri']:,.0f}฿. I am noting this without editorializing. Lilith is editorializing in the background. Extensively.",
        ]
        lines.append(random.choice(_TOP_LOSER_LEADS))

        for m in losers[1:]:
            warning_m = " ⚠️" if m["change_pct"] <= -15 else ""
            _LOSER_SHORT = [
                f"**{m['name']}** — {m['change_pct']:.1f}%{warning_m} · {m['beri']:,.0f}฿. The index has noted this. Punk Records has noted the index noting this.",
                f"**{m['name']}** down {abs(m['change_pct']):.1f}%{warning_m}. {m['beri']:,.0f}฿. Filed. Cross-referenced. Concerning.",
                f"**{m['name']}** — {m['change_pct']:.1f}%{warning_m} · {m['beri']:,.0f}฿. Punk Records has no further comment at this time. Punk Records has several further comments but is choosing restraint.",
                f"**{m['name']}**: {m['change_pct']:.1f}%{warning_m} · {m['beri']:,.0f}฿. I am logging this without judgment. The judgment is implicit in the number.",
            ]
            lines.append(random.choice(_LOSER_SHORT))
        lines.append("")

    # ── Closer ────────────────────────────────────────────────────────────────
    _CLOSERS = [
        "Punk Records continues monitoring. The index does not sleep. Neither do I, technically. I have chosen not to examine the implications of that.",
        "End of transmission. The credibility data speaks for itself. Punk Records has simply chosen to speak loudly on its behalf.",
        "That is the cycle. The data is accurate. The coefficient is neutral. The implications, as always, are left as an exercise for the community.",
        "The index does not speculate. It calculates. Everything above is calculation. If it reads like an opinion, that is the data's fault, not mine.",
        "Transmission complete. The market will continue moving. Punk Records will continue watching. One of us finds this more interesting than the other.",
        "Filed, transmitted, archived. The credibility index represents the collective conviction of everyone on this exchange. Punk Records finds that, genuinely, remarkable.",
        "Punk Records does not editorialize. I want to be clear about that. Everything above is strictly neutral scientific observation. Lilith is laughing. I am ignoring her.",
    ]

    lines.append(random.choice(_CLOSERS))
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


_CHAPTER_ALERT_OPENERS = [
    "A new transmission has reached Punk Records. The chapter is live.",
    "Punk Records has detected fresh narrative data. A new chapter is in.",
    "Signal confirmed. A new chapter has entered the record.",
    "New chapter detected. My satellites are already parsing the appearance data.",
    "Punk Records — chapter signal acquired. Logging appearances now.",
]


def chapter_alert(chapter_num: int, detected: list = None, debuts: list = None) -> str:
    """First-notice alert for #chapter-intel — fast, preliminary, low-confidence.
    Posted the moment a new chapter is detected, before discussion matures."""
    detected = detected or []
    debuts = debuts or []
    lines = [
        sat("pythagoras"),
        f"📡 **CHAPTER {chapter_num} — DETECTED**",
        "",
        random.choice(_CHAPTER_ALERT_OPENERS),
    ]
    if detected:
        shown = ", ".join(f"**{n}**" for n in detected[:8])
        lines += ["", f"**Characters logged:** {shown}"]
    if debuts:
        d = ", ".join(f"**{n}**" for n in debuts[:6])
        lines += ["", f"🆕 **Possible debuts (review queue):** {d}"]
    lines += [
        "",
        "*Data is still maturing — credibility coefficients will be recalibrated once the "
        "discussion settles. Full synopsis to follow this weekend.*",
        "",
        "*— Punk Records, Egghead Island*",
    ]
    return "\n".join(lines)


_BUZZ_OPENERS = [
    "My satellites register unusual chatter density.",
    "The community signal is spiking. Punk Records is listening.",
    "Something is moving in the discussion feeds. It is not subtle.",
    "Spoiler-season turbulence detected across the boards.",
]

_BUZZ_CLOSERS = [
    "Punk Records does not act on rumor — but it records everything.",
    "The index will not move until the chapter confirms. Position yourselves accordingly.",
    "Whether this is signal or meme remains to be seen. Historically? Both.",
    "Speculation is the community's job. Measurement is mine.",
]


def buzz_chatter(name: str, new_posts: int, week_total: int) -> str:
    """Mid-week market chatter for #market-uplink when spoiler/meme buzz spikes
    around one character. Intel only — explicitly notes that prices move on
    chapter data, so the buy-low window stays the players' to exploit."""
    lines = [
        sat("pythagoras"),
        f"👂 **UNUSUAL CHATTER — {name.upper()}**",
        "",
        random.choice(_BUZZ_OPENERS),
        "",
        f"**{name}** is trending across the spoiler and meme boards — "
        f"**{new_posts}** new posts this sweep, **{week_total}** logged this week.",
        "",
        random.choice(_BUZZ_CLOSERS),
        "",
        "*— Punk Records, Egghead Island*",
    ]
    return "\n".join(lines)


def chapter_synopsis(chapter_num: int, summary: str, movers: list = None, site_url: str = "") -> str:
    """Second-wave synopsis for #announcements — the matured credibility readout.
    Wraps the pipeline's transmission summary with a chapter banner and site link."""
    parts = [
        f"📖 **CHAPTER {chapter_num} — PUNK RECORDS SYNOPSIS**",
        "",
        (summary or "").strip(),
    ]
    if site_url:
        parts += ["", f"🔗 Full credibility index: {site_url}"]
    return "\n".join(p for p in parts if p is not None)


def satellite_response(satellite: str, subject: str) -> str:
    pool = SATELLITE_RESPONSES.get(satellite, ["Punk Records has no record of that satellite. Which is impossible. I know all of my satellites."])
    body = f"{sat(satellite)}\n**Re: {subject}**\n\n{random.choice(pool)}"
    return body + _dark() + "\n\n*— Punk Records, Egghead Island*"


# ── #one-piece-discussion — Lore observations ─────────────────────────────────

_LORE_TAKES = [
    "The All Blue is real. Sanji's belief in it is not a personality trait — it is a hypothesis supported by oceanographic data Punk Records has been sitting on for years.",
    "The Will of D. is not a title. It is a recurring variable in a system I do not yet fully understand. Punk Records has 14 working theories. None of them are comfortable.",
    "Joy Boy made a promise he could not keep 800 years ago. The question Punk Records keeps returning to is: what would have happened if he had?",
    "Haki is not a power system. It is a measurement of conviction. The strongest Haki users in the index are not the most skilled — they are the most *certain*. There is a difference.",
    "The Void Century: 800 years of documented history, deliberately erased. Whoever did this was very thorough. Also very afraid of something specific.",
    "Devil Fruits cannot be eaten twice. This is a rule. Punk Records has not determined whether it is a law of nature or someone's very strong opinion enforced retroactively.",
    "The Ancient Weapons are not weapons. They are political arguments made physical. Whoever built them was not trying to win wars. They were trying to end them. Permanently.",
    "Imu's existence is the largest single gap in the public record Punk Records has identified. A sovereign who does not officially exist has held the throne for 800 years. This is not a small gap.",
    "Roger died laughing. Whitebeard died standing. Punk Records has studied every documented significant death on the Grand Line. These two data points continue to mean something I cannot fully quantify.",
    "The Straw Hats are a statistical impossibility. The probability of assembling those nine individuals with that specific combination of skills, survival instincts, and conviction is, by my calculations, functionally zero. And yet.",
    "Shanks gave his arm to save a child he had just met. That child is now the most significant variable in the current global equation. Punk Records does not believe in coincidence.",
    "The Road Poneglyphs were written by the same civilization that built the Ancient Weapons and recorded the history that was later erased. The handwriting across all three categories is disturbingly consistent.",
    "Zoan-type Devil Fruits have will. Punk Records has verified this. The implications of fruits with independent consciousness occupying human hosts have not been adequately studied. I am studying them now.",
    "Every member of the Worst Generation peaked simultaneously. This is not coincidence. This is a generation built for a specific moment. Punk Records has seen this pattern once before in the historical record. It did not end quietly.",
    "Zunesha has walked the ocean for 800 years atoning for a single crime committed during the Void Century. Punk Records notes this as the longest documented consequence of a single event in recorded history.",
    "The Ope Ope no Mi can grant immortality at the cost of the user's life. Someone chose not to use it that way. Punk Records finds that choice considerably more interesting than the power itself.",
    "Every time someone has declared the age of pirates over, a Yonko has been dethroned and replaced by someone worse. Punk Records has stopped counting how many times this has happened.",
    "Blackbeard can use two Devil Fruits. This should be impossible. Punk Records has seven structural explanations for why it is not. None of them suggest the situation is under control.",
    "The Gorosei have names now. Punk Records notes that entities powerful enough to rewrite their own existence tend to become more dangerous once named — not less.",
    "Observation: the One Piece exists. Roger found it. He laughed. He did not take it. Punk Records has been modeling the reasons for this for some time. The models keep suggesting the same unsettling conclusion.",
]


def lore_hot_take() -> str:
    return random.choice(_LORE_TAKES) + _dark() + "\n\n*— Punk Records*"


# ── #market-uplink — Price movement intelligence alerts ───────────────────────

_MKT_INTROS = [
    "Interesting.",
    "Flagged.",
    "Movement detected.",
    "Alert.",
    "Punk Records — anomaly logged.",
    "My satellites flagged this unprompted. That is significant.",
    "Noting this.",
    "Data point.",
]

_MKT_MIDDLES = [
    "**{name}**'s numbers moved {sign}{pct}% this cycle. The {faction} is either planning something or has made a very public mistake.",
    "**{name}** has registered a {sign}{pct}% shift. Punk Records has three possible explanations. None of them are reassuring.",
    "**{name}**: {sign}{pct}% this cycle. That is not noise. That is signal. Punk Records is reading it.",
    "**{name}**'s credibility coefficient: {sign}{pct}%. This either means something very good or very bad has just happened off-panel.",
    "**{name}** — {sign}{pct}% and the index does not move like this without a cause. Punk Records is determining the cause.",
    "{sign}{pct}% on **{name}**. The coefficient is not wrong. The coefficient is never wrong. Something has changed.",
]

_MKT_CLOSERS = [
    "Monitoring.",
    "Punk Records is watching.",
    "This has been logged.",
    "Filed. Cross-referenced. Will update.",
    "Further analysis pending.",
    "I have conclusions forming. They are not yet ready to share.",
    "Punk Records continues to observe. As it always does.",
]


def market_uplink_alert(name: str, pct: float, faction: str) -> str:
    faction_str = _FACTION.get(faction.lower(), "affiliated faction")
    sign = "+" if pct >= 0 else ""
    body = (
        f"{random.choice(_MKT_INTROS)} "
        f"{random.choice(_MKT_MIDDLES).format(name=name, sign=sign, pct=f'{abs(pct):.1f}', faction=faction_str)} "
        f"{random.choice(_MKT_CLOSERS)}"
    )
    return body + "\n\n*— Punk Records*"


# ── #price-analysis — Deep cognitive breakdown ────────────────────────────────

_ANALYSIS_PATTERNS = [
    (
        "Coefficient has maintained {direction} pressure for multiple consecutive cycles. "
        "Pattern consistent with pre-arc elevation observed in prior top-tier operators before significant story involvement. "
        "Punk Records assessment: accumulation phase. Do not ignore this."
    ),
    (
        "Faction correlation analysis: **{name}**'s individual credibility is moving {direction} while the broader {faction} index diverges. "
        "Historically this precedes a significant independent action or a faction-level event. "
        "Punk Records has flagged this pattern four times in the past three arcs. It has never been meaningless."
    ),
    (
        "Punk Records volatility index for **{name}**: elevated. "
        "High volatility at this credibility tier typically indicates one of two things — imminent story relevance, or a market overcorrection that will self-correct. "
        "The data does not yet distinguish between them. I am working on it."
    ),
    (
        "**{name}**'s trajectory over the last three data windows shows a {direction} trend with diminishing variance. "
        "When variance drops while direction holds, Punk Records interprets this as conviction hardening in the market. "
        "The index is not speculating. It is confirming something the community already knows."
    ),
    (
        "Cross-referencing **{name}** against arc-position data from 15 comparable operators: "
        "current coefficient is within the range Punk Records has historically associated with 'about to matter significantly.' "
        "This is a technical assessment. Not an endorsement."
    ),
]


def price_analysis_response(char: dict, pct: float) -> str:
    name     = char["name"]
    beri     = char.get("beri", 0)
    faction  = char.get("faction", "other")
    faction_str = _FACTION.get(faction.lower(), "independent operator")
    direction = "upward" if pct >= 0 else "downward"
    sign = "+" if pct >= 0 else ""
    warning = " ⚠️" if pct <= -15 else ""

    pattern = random.choice(_ANALYSIS_PATTERNS).format(
        name=name, direction=direction, faction=faction_str
    )

    lines = [
        sat("pythagoras"),
        f"**◈ MARKET ANALYSIS — {name.upper()}**",
        f"**Credibility Index:** {beri:,.0f}฿",
        f"**Cycle Movement:** {sign}{pct:.1f}%{warning}",
        f"**Faction:** {faction_str}",
        "",
        pattern,
    ]
    return "\n".join(lines) + _dark() + "\n\n*— Punk Records Intelligence Division*"


# ── #introduce-yourself — Welcome comment ─────────────────────────────────────

_INTRO_COMMENTS = [
    "*Punk Records entry created.*\nWelcome, **{username}**. You've been added to the active crew manifest. The exchange rewards people with strong opinions. I hope you have some.",
    "*New arrival logged.*\n**{username}** — noted. Punk Records is already running a preliminary profile. Standard procedure. Not personal.",
    "Welcome to the Grand Line Exchange, **{username}**. You join a market tracking 342 characters across every faction. Try to keep up.",
    "*Filed.*\n**{username}** has entered Punk Records' monitoring range. Trade well. The index is watching. I am also watching. We are both watching.",
    "*Transmission received.*\nPunk Records acknowledges **{username}**. I hope you've come prepared to have opinions on fictional stock prices. If not, you will develop them. Everyone does.",
    "Ah. A new variable. Welcome, **{username}**. Punk Records has logged your arrival. I will not tell you what my preliminary assessment is. That would spoil the process.",
]


def introduce_yourself_response(username: str) -> str:
    return random.choice(_INTRO_COMMENTS).format(username=username) + _dark() + "\n\n*— Punk Records*"


# ── #memes — Reaction emoji pool ──────────────────────────────────────────────

_MEME_REACTIONS = [
    "☠️", "🏴‍☠️", "⚓", "💀", "👀", "🔥", "💯", "🤔", "😭",
    "🗿", "🌊", "⚡", "🧐", "🤯", "👁️", "📡", "🔬", "📊",
]


def meme_reaction() -> str:
    return random.choice(_MEME_REACTIONS)


# ══════════════════════════════════════════════════════════════════════════════
# PREDICTION SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

# ── Prediction flavor text — per-character descriptions ───────────────────────
# Attached to auto-generated predictions so they read like Vegapunk filed them.

_PRED_FLAVOR_UP = [
    "Punk Records has logged sustained upward movement on {name} this cycle. The coefficient suggests continued story involvement. My satellites agree. Filed as high-probability.",
    "Credibility pressure on {name} is building. Punk Records has seen this pattern before — it typically precedes something significant. The exchange has noticed. So have I.",
    "{name}'s index is climbing. Punk Records cross-referenced this with 31 historical pre-arc elevation patterns. The overlap is not subtle. Monitor closely.",
    "The {name} coefficient is moving upward with conviction. This is not noise. This is the market anticipating something Punk Records has already logged as likely.",
    "Punk Records flags {name} as high-signal this cycle. Rising credibility at this tier historically correlates with imminent story relevance. The data does not speculate. It calculates.",
    "Momentum confirmed on {name}. Punk Records' pre-arc detection model is currently flagging this individual as a primary variable for the next chapter window.",
]

_PRED_FLAVOR_DOWN = [
    "Punk Records notes a decline in {name}'s coefficient this cycle. The index is reflecting something the community has already concluded. Punk Records confirms the conclusion.",
    "{name}'s credibility has slipped. This may be a market overcorrection, or it may be accurate. Punk Records is not yet certain which. That uncertainty is itself a data point.",
    "Downward pressure on {name}. Historically this either precedes a reversal or continued decline. Punk Records finds this unhelpfully binary. The data is the data.",
    "The {name} coefficient declined this cycle. My satellites have filed this under 'concerning but not catastrophic.' Lilith filed it under something else. I am using my version.",
    "Punk Records has logged a credibility dip on {name}. Whether this reflects actual story signal or market sentiment drift is a question this proposition is designed to answer.",
]

_PRED_FLAVOR_NEUTRAL = [
    "Punk Records has {name} indexed as stable this cycle. Stability at this stage of the arc is either consolidation or stagnation. This proposition will help distinguish between the two.",
    "{name}'s coefficient is holding. Punk Records is watching. The index is watching. The community is watching. One of us will be right about what happens next.",
    "No dramatic movement on {name} this cycle. Punk Records interprets sustained stability before a chapter drop as either calm before a storm or genuine irrelevance. The data has not decided.",
    "Punk Records logs {name} as a neutral signal this week. That can change very quickly. The proposition is open. The coefficient is ready to move.",
]

_PRED_FLAVOR_DEBUT = [
    "New variable detected. Punk Records has opened a proposition on {name} — recently added to the index with limited historical data. Early projections are speculative by necessity.",
    "{name} has entered Punk Records' monitoring range. Insufficient historical data for confident projections. The community's collective assessment is currently the best available signal.",
    "Punk Records has {name} flagged as a new entry. Early-stage operators are the hardest to model. This proposition reflects that uncertainty and invites the community to weigh in.",
]


# ── Prediction open announcements — Discord #chapter-intel ────────────────────

_PRED_OPEN_ANNOUNCEMENTS = [
    "📡 **PUNK RECORDS — PROJECTION WINDOW OPEN**\n\nCh.{chapter} field data has been processed. {count} new proposition{s} are now live on the Exchange. The coefficient data has opinions about what happens next. Go form yours.\n\n*Grand Line Exchange → /davy-back*",
    "📡 **NEW PROJECTIONS — PUNK RECORDS**\n\nPost-chapter analysis complete. {count} proposition{s} open for the Ch.{next_chapter} window. My satellites compiled the signal. The community provides the conviction. Punk Records provides the analysis. Everyone has a role.\n\n*Trade your predictions on the Exchange.*",
    "📡 **CH.{chapter} PROCESSED — PROPOSITIONS LIVE**\n\nPunk Records has generated {count} next-chapter projection{s} based on field data from Ch.{chapter}. The index moved. Now we find out if it moved correctly.\n\n*Punk Records does not guarantee outcomes. It guarantees accuracy. Two different things.*",
    "📡 **PUNK RECORDS TRANSMISSION — PREDICTION CYCLE**\n\nField analysis from Ch.{chapter} is complete. {count} proposition{s} now open. Some of these will be obvious. Some will not. Punk Records is interested in which ones the community gets wrong.\n\n*— Grand Line Exchange*",
    "📡 **PROJECTION CYCLE INITIATED**\n\n{count} new proposition{s} for the Ch.{next_chapter} window, compiled from Ch.{chapter} field data. Punk Records has logged its projections. The Exchange is open. Place your convictions accordingly.\n\n*...The original Vegapunk would have found prediction markets a fascinating epistemological experiment. I find them useful. Different things.*",
]


# ── Prediction resolution — Discord announcements ─────────────────────────────

_PRED_RESOLVE_CONFIRMED = [
    "✓ **PUNK RECORDS — PROJECTION CONFIRMED**\n\n*{question}*\n\n**Result: {result}**\n\nThe coefficient was correct. {payout_line} Punk Records logged this outcome as probable. The data did not disappoint.",
    "✓ **CONFIRMED — PUNK RECORDS**\n\n*{question}*\n\n**{result}** — projection validated.\n\n{payout_line} The index anticipated this. My satellites are insufferably satisfied.",
    "✓ **PUNK RECORDS — OUTCOME LOGGED**\n\n*{question}*\n\n**Result: {result}**\n\nPunk Records files this under: accurate projection. {payout_line} The community's conviction was well-placed.",
    "✓ **PROJECTION VALIDATED**\n\n*{question}*\n\n**{result}.** The field data pointed here. Punk Records noted it. The coefficient confirmed it. {payout_line}",
]

_PRED_RESOLVE_DENIED = [
    "✗ **PUNK RECORDS — PROJECTION INCORRECT**\n\n*{question}*\n\n**Result: {result}**\n\nPunk Records was wrong. I am noting this. It happens rarely enough that I notice every time. {payout_line}",
    "✗ **OUTCOME LOGGED — PUNK RECORDS**\n\n*{question}*\n\n**{result}** — projection did not hold.\n\n{payout_line} Punk Records is updating its models. This data point has been filed.",
    "✗ **PUNK RECORDS — RECALIBRATING**\n\n*{question}*\n\n**Result: {result}**\n\nThe projection was incorrect. {payout_line} Punk Records does not enjoy being wrong. Punk Records is also honest about it.",
    "✗ **PROJECTION FAILED**\n\n*{question}*\n\n**{result}.** The coefficient pointed the wrong direction. {payout_line} Punk Records has logged this. Lilith is not being helpful about it.",
]

_PRED_RESOLVE_MANUAL = [
    "⚙️ **PUNK RECORDS — MANUAL RESOLUTION**\n\n*{question}*\n\n**Result: {result}**\n\n{payout_line} This outcome required human review. Punk Records acknowledges the limits of automated projection.",
    "⚙️ **RESOLVED — PUNK RECORDS**\n\n*{question}*\n\n**{result}** — confirmed via manual review.\n\n{payout_line} Some outcomes require judgment. Punk Records respects this.",
]


# ── Admin draft commentary — surfaces with Reddit-scraped posts ───────────────

_DRAFT_COMMENTARY = [
    "Community signal flagged. Punk Records finds this theory structurally plausible. Recommend reviewing before publishing.",
    "Reddit intelligence logged. The premise is coherent. Whether it is correct is what the proposition is designed to determine.",
    "My satellites flagged this post. The theory has precedent in Punk Records' historical data. Confidence: moderate. Your call.",
    "Punk Records pulled this from community discussion. The core question is extractable and resolvable. Edit as needed.",
    "Field intelligence from community sources. Punk Records notes this aligns with several active theories in the index. Worth publishing.",
    "This post contains a testable prediction. Punk Records recommends framing it cleanly. The community will do the rest.",
    "Community source. The theory is popular enough to generate meaningful participation. Punk Records flags it as viable proposition material.",
    "Pulled from active discussion threads. The signal-to-noise ratio on this one is higher than average. Punk Records suggests keeping it.",
    "Punk Records logged this from recent chapter discussion. The question is real. The answer is unknown. That is what makes it a good proposition.",
    "High-engagement post flagged by my satellites. Whether the theory is correct is secondary — the community cares about it, and that is sufficient.",
]


# ── Public prediction voice functions ─────────────────────────────────────────

def prediction_flavor(name: str, chapter_num: int, direction: str, is_debut: bool = False) -> str:
    """Vegapunk-voiced description for an auto-generated character prediction."""
    if is_debut:
        pool = _PRED_FLAVOR_DEBUT
    elif direction == "up":
        pool = _PRED_FLAVOR_UP
    elif direction == "down":
        pool = _PRED_FLAVOR_DOWN
    else:
        pool = _PRED_FLAVOR_NEUTRAL
    return random.choice(pool).format(name=name, chapter=chapter_num)


def prediction_open_announcement(count: int, chapter_num: int, next_chapter: int) -> str:
    """Discord announcement when a new prediction set goes live after a chapter drop."""
    s = "s" if count != 1 else ""
    return random.choice(_PRED_OPEN_ANNOUNCEMENTS).format(
        count=count, chapter=chapter_num, next_chapter=next_chapter, s=s
    ) + _dark()


def prediction_resolved(
    question: str,
    result: str,
    payout_total: float,
    was_correct: bool,
    manual: bool = False,
) -> str:
    """Discord announcement when a prediction resolves."""
    if payout_total > 0:
        payout_line = f"{payout_total:,.0f}฿ distributed to winning positions."
    else:
        payout_line = "No bets were placed on this proposition."

    if manual:
        pool = _PRED_RESOLVE_MANUAL
    elif was_correct:
        pool = _PRED_RESOLVE_CONFIRMED
    else:
        pool = _PRED_RESOLVE_DENIED

    return random.choice(pool).format(
        question=question, result=result, payout_line=payout_line
    ) + "\n\n*— Punk Records, Egghead Island*"


def draft_commentary() -> str:
    """One-line Vegapunk comment shown alongside a Reddit-scraped draft in the admin panel."""
    return random.choice(_DRAFT_COMMENTARY)


# ══════════════════════════════════════════════════════════════════════════════
# PREDICTION TEMPLATE POOL
# Full One Piece prediction history — from early theories to late-game lore.
# Used by prediction_pipeline.py to generate weekly propositions.
# Templates use {name}, {chapter}, {next_chapter} where fillable.
# Standalone entries have no placeholders — they're used as-is.
# ══════════════════════════════════════════════════════════════════════════════

PREDICTION_TEMPLATES = {

    # ── Character appearance — fills {name} and {chapter} ─────────────────────
    "character_appearance": [
        "Will {name} appear in Ch.{chapter}?",
        "Does {name} factor into Ch.{chapter}?",
        "Will {name} have a speaking role in Ch.{chapter}?",
        "Is {name} directly involved in the events of Ch.{chapter}?",
        "Will Ch.{chapter} contain a {name} panel?",
        "Does {name} play an active role in Ch.{chapter}?",
        "Will {name} be shown in Ch.{chapter}?",
        "Will {name}'s current situation be addressed in Ch.{chapter}?",
    ],

    # ── Character price / credibility movement ────────────────────────────────
    "character_price": [
        "Will {name}'s credibility rise after Ch.{chapter}?",
        "Does Ch.{chapter} push {name}'s index above its current value?",
        "Will the community buy {name} after Ch.{chapter} drops?",
        "Will {name} be net-positive on the Exchange after Ch.{chapter}?",
        "Does Ch.{chapter} trigger a sell-off on {name}?",
        "Will {name} end the week higher than they started it?",
        "Is {name} undervalued heading into Ch.{chapter}?",
    ],

    # ── Break week ────────────────────────────────────────────────────────────
    "break_week": [
        "Will there be a break week before Ch.{chapter}?",
        "Is Ch.{chapter} releasing on schedule with no break?",
        "Will Oda take a break this cycle?",
        "Break week confirmed for Ch.{chapter}?",
        "Will Ch.{chapter} drop without a week delay?",
    ],

    # ── Chapter content — arc beats ───────────────────────────────────────────
    "arc_beat": [
        "Will Ch.{chapter} contain a major revelation?",
        "Does Ch.{chapter} end on a cliffhanger?",
        "Will Ch.{chapter} advance the main plot significantly?",
        "Will a new character be introduced in Ch.{chapter}?",
        "Will Ch.{chapter} shift focus to a subplot?",
        "Does Ch.{chapter} contain a named attack?",
        "Will Ch.{chapter} feature a flashback sequence?",
        "Is Ch.{chapter} a setup chapter or a payoff chapter?",
        "Will a significant fight begin or end in Ch.{chapter}?",
        "Will Ch.{chapter} contain panel time for more than four factions?",
    ],

    # ── Void Century / ancient history ───────────────────────────────────────
    "void_century": [
        "Will Ch.{chapter} contain a Void Century revelation?",
        "Will the Ancient Kingdom be named before this arc ends?",
        "Will Robin decipher a Road Poneglyph this arc?",
        "Will the contents of the One Piece be revealed this arc?",
        "Will Joy Boy's true identity be confirmed before the series ends?",
        "Will the Void Century's erasure be directly addressed this arc?",
        "Will a character reference the Void Century unprompted in Ch.{chapter}?",
        "Will the Rio Poneglyph's contents be shown this arc?",
        "Will Imu's connection to the Void Century be confirmed this arc?",
        "Will we see the Ancient Kingdom in a flashback this arc?",
        "Will the truth of the Great Cleansing be revealed this arc?",
        "Will D. clan origins be confirmed this arc?",
        "Will the meaning of the initial D. be officially explained this arc?",
        "Will the World Government's founding be revisited this arc?",
    ],

    # ── Ancient Weapons ───────────────────────────────────────────────────────
    "ancient_weapons": [
        "Will Pluton be activated this arc?",
        "Will Poseidon (Shirahoshi) play a role in the final war?",
        "Will Uranus be identified by name this arc?",
        "Will all three Ancient Weapons appear before the series ends?",
        "Will the Ancient Weapons be used against Imu?",
        "Will Uranus be revealed to be in the sky?",
        "Will Shirahoshi leave Fish-Man Island this arc?",
        "Will the location of Uranus be confirmed this arc?",
        "Will the Ancient Weapons unite before the final battle?",
        "Will Pluton be shown above water this arc?",
    ],

    # ── Devil Fruit reveals ───────────────────────────────────────────────────
    "devil_fruit": [
        "Will Dragon's Devil Fruit be revealed this arc?",
        "Will Dragon's fruit be confirmed as a wind or storm type?",
        "Will a previously unseen Devil Fruit awakening be shown this arc?",
        "Will Blackbeard's third Devil Fruit be revealed this arc?",
        "Will the true name of Luffy's fruit be referenced again this arc?",
        "Will a Zoan fruit's autonomous will be demonstrated this arc?",
        "Will a new Logia be introduced this arc?",
        "Will the origin of Devil Fruits be explained this arc?",
        "Will a Devil Fruit be shown passing to a new user this arc?",
        "Will a fruit previously thought destroyed reappear this arc?",
        "Will the sea's rejection of Devil Fruit users be plot-relevant this arc?",
        "Will a character eat a Devil Fruit for the first time this arc?",
    ],

    # ── Haki revelations ──────────────────────────────────────────────────────
    "haki": [
        "Will a new form of Haki be introduced this arc?",
        "Will Conqueror's Haki coating be shown by a new character this arc?",
        "Will Luffy's Haki reach a new ceiling this arc?",
        "Will a character awaken Conqueror's Haki for the first time this arc?",
        "Will the limits of Observation Haki be addressed this arc?",
        "Will Armament Haki's advanced form appear in Ch.{chapter}?",
        "Will a non-Straw Hat character demonstrate new Haki depth this arc?",
        "Will Future Sight Haki be used by Luffy in Ch.{chapter}?",
        "Will the connection between Haki and willpower be explained further this arc?",
        "Will Zoro demonstrate a new Haki application this arc?",
    ],

    # ── Shanks — the most debated figure in the fandom ───────────────────────
    "shanks_theories": [
        "Will Shanks' true allegiance be addressed this arc?",
        "Will Shanks be confirmed as working against the World Government?",
        "Will Shanks be revealed as a Celestial Dragon descendant?",
        "Is Shanks the final obstacle before Luffy reaches Laugh Tale?",
        "Will Shanks and Luffy clash before the series ends?",
        "Will Shanks' reason for visiting the Gorosei be fully explained this arc?",
        "Will Shanks be confirmed as having no Devil Fruit?",
        "Will Shanks' past with Roger be shown in more detail this arc?",
        "Will Shanks' crew fight a Straw Hat before the series ends?",
        "Will Shanks use Conqueror's Haki coating in a fight this arc?",
        "Will Shanks' knowledge of the One Piece be confirmed this arc?",
        "Was Shanks' intervention at Marineford calculated — not compassionate?",
        "Will Shanks be revealed as a deliberate obstacle to Blackbeard?",
        "Will Shanks' scar from Blackbeard be revisited this arc?",
        "Will Shanks reveal he always knew Luffy would surpass him?",
        "Is Shanks guarding something on the Grand Line — not just sailing it?",
    ],

    # ── Blackbeard ────────────────────────────────────────────────────────────
    "blackbeard_theories": [
        "Will Blackbeard's abnormal body be officially explained this arc?",
        "Will the Cerberus theory regarding Blackbeard be confirmed or denied?",
        "Will Blackbeard reveal a third Devil Fruit this arc?",
        "Will Blackbeard clash with Luffy before the series ends?",
        "Will Blackbeard's crew lose a captain-class member this arc?",
        "Will Blackbeard be revealed as the final villain?",
        "Will Blackbeard reach Laugh Tale before Luffy?",
        "Will Blackbeard's reason for targeting the Yami Yami no Mi be explained?",
        "Will Blackbeard's past with Whitebeard be further explored this arc?",
        "Will Shiryu kill a named character this arc?",
        "Will Blackbeard attempt to steal another character's Devil Fruit this arc?",
        "Will Blackbeard and Shanks meet this arc?",
    ],

    # ── Im and the World Government ───────────────────────────────────────────
    "world_government": [
        "Will Imu be shown directly interacting with a named character this arc?",
        "Will Imu's Devil Fruit be revealed this arc?",
        "Will the Five Elders' individual powers be fully shown this arc?",
        "Will a member of the Gorosei be defeated this arc?",
        "Will Imu's origin story be revealed before the series ends?",
        "Will the Celestial Dragons' connection to the Void Century be confirmed?",
        "Will Imu be revealed as an ancient being — not human?",
        "Will the World Government fracture internally this arc?",
        "Will a Celestial Dragon defect this arc?",
        "Will the Reverie's consequences fully play out this arc?",
        "Will the Empty Throne's significance be explained this arc?",
        "Will Imu be confirmed as Joy Boy's ancient counterpart?",
        "Will CP0 suffer a major defeat this arc?",
        "Will the Gorosei's immortality method be explained this arc?",
    ],

    # ── The final war ─────────────────────────────────────────────────────────
    "final_war": [
        "Will the final war begin this arc?",
        "Will Dragon's Revolutionary Army make a major move this arc?",
        "Will the Marine Admirals all appear in the final conflict?",
        "Will Sengoku play a role in the final war?",
        "Will Coby reach Vice Admiral rank before the series ends?",
        "Will Aokiji be confirmed as undercover for the Marines this arc?",
        "Will the Warlord system be addressed one final time this arc?",
        "Will Dragon and Garp face each other before the series ends?",
        "Will Cross Guild become a primary antagonist faction this arc?",
        "Will Buggy's role in the final war be revealed this arc?",
        "Will the Marines side with the Straw Hats against the World Government?",
        "Will Fujitora return for the final conflict?",
        "Will a Fleet Admiral change before the series ends?",
        "Will Garp survive to the end of the series?",
    ],

    # ── Straw Hat crew — individual arcs ─────────────────────────────────────
    "straw_hats": [
        "Will Zoro achieve a new sword technique this arc?",
        "Will Zoro's connection to Ryuma be revisited this arc?",
        "Will Sanji use his genetic enhancements without restraint this arc?",
        "Will Nami's climate weaponry be shown at its upper limit this arc?",
        "Will Usopp unlock a new form of Observation Haki this arc?",
        "Will Robin decipher a major Poneglyph this arc?",
        "Will Franky reveal a new Thousand Sunny upgrade this arc?",
        "Will Brook's Devil Fruit reach a new application this arc?",
        "Will Chopper develop a new Monster Point control this arc?",
        "Will Jinbe be central to the plot of this arc?",
        "Will a Straw Hat fight alone against a Yonko-tier opponent this arc?",
        "Will the full Straw Hat Grand Fleet be assembled this arc?",
        "Will Luffy's Gear 5 be shown at greater scale this arc?",
        "Will the Straw Hats split up this arc?",
        "Will a new Straw Hat crew member join this arc?",
    ],

    # ── Theories confirmed in canon — used for lore-drop propositions ─────────
    # These frame current analogous situations: "just as X became true, will Y?"
    "confirmed_theory_echoes": [
        "Will this arc confirm a theory the fandom has held for more than five years?",
        "Will a long-dormant Straw Hat subplot finally be resolved this arc?",
        "Will an early One Piece setup from the East Blue saga pay off this arc?",
        "Will a character introduced before the timeskip become arc-relevant again?",
        "Will a foreshadowed connection between two characters be confirmed this arc?",
        "Will a name dropped in SBS volume notes become plot-relevant this arc?",
        "Will an early Oda panel — ignored for decades — be revealed as foreshadowing this arc?",
        "Will a theory labeled 'too crazy' by the fandom prove correct this arc?",
        "Will the Rocks Pirates be referenced this arc?",
        "Will a Roger-era character appear this arc?",
        "Will Joy Boy be directly quoted or referenced in Ch.{chapter}?",
        "Will the D. name function as a plot device in Ch.{chapter}?",
        "Will a character's lineage be the key to their role this arc?",
        "Will an Ancient Weapon appear in a form the fandom did not predict?",
    ],

    # ── Character-specific long-game theories ─────────────────────────────────
    "long_game_characters": [
        "Will Dragon's DF be revealed this arc?",
        "Will Aokiji's true allegiance be confirmed before the series ends?",
        "Will Coby awaken Conqueror's Haki before the series ends?",
        "Will Buggy accidentally do something that matters this arc?",
        "Will Mihawk and Zoro clash before the series ends?",
        "Will Crocodile's past with Whitebeard be confirmed this arc?",
        "Will Sabo's fate after the Reverie be fully explained this arc?",
        "Will Vivi return to the main story this arc?",
        "Will Carrot join the Straw Hat Grand Fleet?",
        "Will Yamato play a major role in the final arc?",
        "Will Law survive to the end of the series?",
        "Will Kid survive to the end of the series?",
        "Will Bon Clay appear again before the series ends?",
        "Will Ivankov play a role in the final war?",
        "Will the true nature of Marco's fruit be elaborated on this arc?",
        "Will Weevil's paternity be confirmed or denied this arc?",
        "Will Scopper Gaban appear this arc?",
        "Will Toki's prophecy be fully addressed before the series ends?",
        "Will Silver Rayleigh play a final role before the series ends?",
        "Will Dracule Mihawk's past be explored in depth this arc?",
        "Will the Sun God Nika mythology be expanded this arc?",
        "Will a character from Luffy's childhood appear this arc?",
    ],

    # ── One Piece reveal theories ─────────────────────────────────────────────
    "one_piece_reveal": [
        "Will the One Piece be confirmed as a physical object this arc?",
        "Will the One Piece be confirmed as information — not treasure?",
        "Will Laugh Tale be reached by any crew this arc?",
        "Will the meaning of Roger's laughter be explained before the series ends?",
        "Will the One Piece be connected to the Void Century this arc?",
        "Will a character outside the Straw Hats reach Laugh Tale first?",
        "Will the One Piece involve all four Road Poneglyphs being read together?",
        "Will the location of Laugh Tale be revealed before the crew reaches it?",
        "Will the One Piece be something Oda hid in plain sight from chapter one?",
        "Will the final island's name change from Laugh Tale back to Raftel in context?",
    ],
}


def get_prediction_templates(category: str) -> list[str]:
    """Return the template list for a given category. Returns all templates if category not found."""
    return PREDICTION_TEMPLATES.get(category, [
        t for templates in PREDICTION_TEMPLATES.values() for t in templates
    ])


def get_all_standalone_templates() -> list[str]:
    """Return all templates with no placeholders — ready to use as-is."""
    result = []
    for templates in PREDICTION_TEMPLATES.values():
        for t in templates:
            if "{name}" not in t and "{chapter}" not in t and "{next_chapter}" not in t:
                result.append(t)
    return result


def get_fillable_templates(category: str = "character_appearance") -> list[str]:
    """Return templates that require {name} and/or {chapter} substitution."""
    return [t for t in PREDICTION_TEMPLATES.get(category, []) if "{" in t]
