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
