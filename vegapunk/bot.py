"""
Vegapunk — Punk Records AI bot for One Piece Discord servers.

Environment variables:
  VEGAPUNK_BOT_TOKEN          — Discord bot token (required)
  VEGAPUNK_TRANSMISSION_CHANNELS — Comma-separated channel IDs for weekly broadcasts + hot takes
  VEGAPUNK_INGEST_CHANNELS    — Comma-separated channel IDs to forward to the site
                                 (leave blank to ingest from all channels)
  SITE_URL                    — Grand Line Exchange base URL
  ADMIN_SECRET                — Site admin secret for discord-ingest endpoint
"""
import os
import json
import random
import logging
import threading
import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

load_dotenv()

import discord
from discord import app_commands
from discord.ext import tasks

from vegapunk import api, personality

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("vegapunk")

TOKEN               = os.getenv("VEGAPUNK_BOT_TOKEN", "")
GUILD_ID            = int(os.getenv("VEGAPUNK_GUILD_ID", "0") or "0")
_raw_tx             = os.getenv("VEGAPUNK_TRANSMISSION_CHANNELS", "")
TRANSMISSION_CHS    = [int(c.strip()) for c in _raw_tx.split(",") if c.strip()]
_raw_ingest         = os.getenv("VEGAPUNK_INGEST_CHANNELS", "")
INGEST_CHANNELS     = set(c.strip() for c in _raw_ingest.split(",") if c.strip())
INGEST_ALL          = not INGEST_CHANNELS          # if no IDs set → ingest everywhere

_TRIGGER_PHRASES    = {"vegapunk", "punk records", "are you a bot", "are you real", "are you alive"}

# ── In-memory channel ID cache (populated from BotKV on ready) ────────────────
_CH: dict[str, int] = {}   # key → channel id

# KV key → the actual Discord channel NAME to fall back to when the KV registry
# is empty (i.e. /setup-server was never run, or ADMIN_SECRET isn't configured
# on this service so get_kv returns ""). The bot must never be mute just
# because its channel registry is unreachable — names are stable in this guild.
_CH_NAMES = {
    "announcements_channel_id":        "announcements",
    "market_uplink_channel_id":        "market-uplink",
    "chapter_intel_channel_id":        "chapter-intel",
    "general_channel_id":              "general",
    "one_piece_discussion_channel_id": "one-piece-discussion",
    "memes_channel_id":                "memes",
    "introduce_yourself_channel_id":   "introduce-yourself",
    "price_analysis_channel_id":       "price-analysis",
}

async def _safe_send(channel, content, **kwargs) -> bool:
    """Send a message, logging (not raising) on failure so a single permission
    error can't permanently kill a @tasks.loop. Returns True on success."""
    try:
        await channel.send(content, **kwargs)
        return True
    except Exception as e:
        name = getattr(channel, "name", getattr(channel, "id", channel))
        log.warning("Send to #%s failed: %s", name, e)
        return False


async def _already_posted(client, channel, marker: str, within_hours: int = 144) -> bool:
    """True if THIS bot already posted a message containing `marker` in the
    channel within the window. Discord history is the dedup source of truth:
    in-memory sets die on every redeploy and KV writes silently fail without
    ADMIN_SECRET — which is exactly how the Ch.1187 alert got posted 4× in one
    night of restarts. Fails open (False) so a history hiccup can't mute posts;
    the KV/in-memory layers still catch the common case then."""
    try:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=within_hours)
        async for m in channel.history(limit=40):
            if m.created_at < cutoff:
                break
            if m.author.id == client.user.id and marker in (m.content or ""):
                return True
    except Exception as e:
        log.warning("History dedup check failed in #%s: %s", getattr(channel, "name", "?"), e)
    return False


async def _load_ch_cache():
    for k in _CH_NAMES:
        v = await api.get_kv(k)
        if v:
            _CH[k] = int(v)


def _channel_by_name(client: discord.Client, name: str):
    """Find a text channel by exact name across the bot's guilds."""
    for guild in client.guilds:
        for ch in guild.text_channels:
            if ch.name == name:
                return ch
    return None


async def _resolve_channel(client: discord.Client, kv_key: str):
    """Resolve a target channel: BotKV registry → cached id → guild lookup by
    name. Whatever resolves is cached. Returns a channel or None."""
    ch_id = _CH.get(kv_key)
    if not ch_id:
        v = await api.get_kv(kv_key)
        if v:
            try:
                ch_id = int(v)
                _CH[kv_key] = ch_id
            except ValueError:
                ch_id = None
    if ch_id:
        ch = client.get_channel(ch_id)
        if ch:
            return ch
    # Registry empty or points at a deleted channel — fall back to the name.
    name = _CH_NAMES.get(kv_key)
    ch = _channel_by_name(client, name) if name else None
    if ch:
        _CH[kv_key] = ch.id
        log.info("Channel %s resolved by NAME (#%s → %s) — KV registry empty/stale", kv_key, ch.name, ch.id)
    return ch


# ── Bot client ────────────────────────────────────────────────────────────────

class VegapunkBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        # In-memory dedup — the announce/transmission loops persist their dedup
        # markers to BotKV, but when ADMIN_SECRET isn't configured those writes
        # silently fail and every tick would re-post. These sets guarantee
        # at-most-once per process regardless of KV health.
        self._alerted: set = set()    # chapter numbers Wave-1 posted
        self._synopsed: set = set()   # chapter numbers Wave-2 posted
        self._tx_weeks: set = set()   # ISO weeks the weekly transmission posted
        self._buzzed: set = set()     # (week, char) buzz chatter posted

    async def setup_hook(self):
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self.weekly_transmission.start()
        self.random_hot_take.start()
        self.lore_post.start()
        self.market_uplink_post.start()
        self.price_analysis_post.start()
        self.chapter_announce.start()
        log.info("Punk Records systems online.")

    async def on_ready(self):
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="Punk Records | /intel"
            )
        )
        await _load_ch_cache()
        log.info("Vegapunk connected as %s — channel cache: %s", self.user, _CH)

    async def on_member_join(self, member: discord.Member):
        role = discord.utils.get(member.guild.roles, name="NewKama")
        if role:
            try:
                await member.add_roles(role, reason="Auto-assigned on join")
                log.info("Assigned NewKama role to %s", member)
            except Exception as e:
                log.warning("Could not assign NewKama role to %s: %s", member, e)
        else:
            log.warning("NewKama role not found in guild %s", member.guild.id)

    async def on_interaction(self, interaction: discord.Interaction):
        log.info("Interaction received: type=%s name=%s user=%s",
                 interaction.type, getattr(interaction, 'command', None) and interaction.command.name, interaction.user)
        await super().on_interaction(interaction)

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        ch_id = message.channel.id
        ch_id_str = str(ch_id)

        if INGEST_ALL or ch_id_str in INGEST_CHANNELS:
            await api.ingest_message(
                channel=getattr(message.channel, "name", ch_id_str),
                author=str(message.author),
                content=message.content,
            )

        # Reactive channel checks match by KV id OR channel name, so they work
        # even when the KV registry was never populated.
        ch_name = getattr(message.channel, "name", "")

        # #memes — random emoji reaction (~35% chance)
        if (ch_id == _CH.get("memes_channel_id") or ch_name == "memes") and random.random() < 0.35:
            try:
                await message.add_reaction(personality.meme_reaction())
            except Exception:
                pass

        # #introduce-yourself — Vegapunk welcome comment (~80% chance)
        if (ch_id == _CH.get("introduce_yourself_channel_id") or ch_name == "introduce-yourself") and random.random() < 0.80:
            try:
                username = message.author.display_name
                await message.reply(personality.introduce_yourself_response(username))
            except Exception:
                pass

        # Reply when directly addressed or asked about Vegapunk
        content_lower = message.content.lower()
        if any(phrase in content_lower for phrase in _TRIGGER_PHRASES):
            if any(w in content_lower for w in ("bot", "real", "alive", "you")):
                await message.reply(personality.bot_aware_response())
            elif random.random() < 0.20:
                chars = await api.fetch_all_characters()
                if chars:
                    char = random.choice(chars[:25])
                    pct = api.recent_change_pct(char)
                    await message.reply(
                        personality.hot_take(char["name"], pct, char.get("faction", "other"))
                    )

    # ── Scheduled tasks ───────────────────────────────────────────────────────

    @tasks.loop(hours=12)  # check twice daily; actual send is gated to once per ISO week
    async def weekly_transmission(self):
        now = datetime.datetime.utcnow()
        iso = now.isocalendar()
        current_week = f"{iso[0]}-W{iso[1]:02d}"

        last_week = await api.get_kv("last_tx_week")
        if last_week == current_week or current_week in self._tx_weeks:
            return  # already fired this week — skip

        chars = await api.fetch_all_characters()
        movers = sorted(
            [{"name": c["name"], "beri": c["beri"], "change_pct": api.recent_change_pct(c)} for c in chars],
            key=lambda x: x["change_pct"], reverse=True,
        )
        message = personality.transmission_response(movers)

        # The weekly credibility digest lives in #market-uplink (its themed
        # channel). #announcements is reserved for the chapter synopsis, and we
        # no longer fan out to VEGAPUNK_TRANSMISSION_CHANNELS — that env var was
        # leaking this post into #general. Falls back to announcements only if
        # market-uplink isn't configured.
        ch = await _resolve_channel(self, "market_uplink_channel_id") \
            or await _resolve_channel(self, "announcements_channel_id")
        if not ch:
            return
        if await _already_posted(self, ch, "WEEKLY TRANSMISSION", within_hours=120):
            # Posted by a previous process this week — heal the markers.
            self._tx_weeks.add(current_week)
            await api.set_kv("last_tx_week", current_week)
            return
        if await _safe_send(ch, message):
            self._tx_weeks.add(current_week)
            await api.set_kv("last_tx_week", current_week)
            log.info("Weekly transmission sent for %s", current_week)

    def _skip_boot_tick(self, name: str) -> bool:
        """The random flavor loops fire their first iteration the moment the
        bot starts — so every redeploy rolled fresh dice and the channels got
        spammed on a night of restarts. Skip each loop's boot tick; they only
        post on their natural cadence while the process stays up."""
        booted = getattr(self, "_boot_ticks", None)
        if booted is None:
            booted = self._boot_ticks = set()
        if name in booted:
            return False
        booted.add(name)
        return True

    @tasks.loop(hours=6)
    async def random_hot_take(self):
        if self._skip_boot_tick("hot_take"):
            return
        if random.random() > 0.40:   # ~40 % chance each 6-hour tick
            return
        chars = await api.fetch_all_characters()
        if not chars:
            return
        char = random.choice(chars[:30])
        pct = api.recent_change_pct(char)
        message = personality.hot_take(char["name"], pct, char.get("faction", "other"))

        # Hot takes go to #general (KV registry → name lookup), env var fallback
        ch = await _resolve_channel(self, "general_channel_id")
        if ch:
            await _safe_send(ch, message)
        else:
            for ch_id in TRANSMISSION_CHS:
                fallback = self.get_channel(ch_id)
                if fallback:
                    await _safe_send(fallback, message)

    @tasks.loop(hours=10)
    async def lore_post(self):
        if self._skip_boot_tick("lore"):
            return
        if random.random() > 0.35:   # ~35% chance each 10h tick
            return
        ch = await _resolve_channel(self, "one_piece_discussion_channel_id")
        if ch:
            await _safe_send(ch, personality.lore_hot_take())

    @tasks.loop(hours=8)
    async def market_uplink_post(self):
        if self._skip_boot_tick("market_uplink"):
            return
        if random.random() > 0.50:   # ~50% chance each 8h tick
            return
        ch = await _resolve_channel(self, "market_uplink_channel_id")
        if not ch:
            return
        chars = await api.fetch_all_characters()
        if not chars:
            return
        # Pick a character with notable movement
        candidates = [c for c in chars if abs(api.recent_change_pct(c)) > 1.0]
        char = random.choice(candidates[:30]) if candidates else random.choice(chars[:30])
        pct = api.recent_change_pct(char)
        await _safe_send(ch, personality.market_uplink_alert(char["name"], pct, char.get("faction", "other")))

    @tasks.loop(hours=16)
    async def price_analysis_post(self):
        if self._skip_boot_tick("price_analysis"):
            return
        if random.random() > 0.60:   # ~60% chance each 16h tick
            return
        ch = await _resolve_channel(self, "price_analysis_channel_id")
        if not ch:
            return
        chars = await api.fetch_all_characters()
        if not chars:
            return
        char = random.choice(chars[:40])
        pct = api.recent_change_pct(char)
        await _safe_send(ch, personality.price_analysis_response(char, pct))

    @tasks.loop(minutes=20)
    async def chapter_announce(self):
        """Bridge the chapter pipeline's two-wave handshake to Discord.

        Wave 1 (alert)    — pipeline publishes the alert (BotKV `chapter_alert`,
                            served publicly at /public/chapter-alert) on first
                            detection (Thu); post a preliminary breakdown to
                            #chapter-intel.
        Wave 2 (synopsis) — the matured Saturday transmission at /transmission;
                            post it to #announcements.

        Hardened to work with ZERO env vars beyond the Discord token: alert and
        synopsis are read from PUBLIC site endpoints (no ADMIN_SECRET needed),
        channels fall back to name lookup (no /setup-server needed), and dedup
        is in-memory first with BotKV as best-effort persistence — so a broken
        KV can no longer mute announcements, and a mute KV can't cause spam.
        """
        # ── Fetch handshake state (public endpoint; KV fallback for old web) ──
        state = await api.fetch_announce_state()
        if state is None:
            state = {}
            try:
                raw = await api.get_kv("chapter_alert")
                state["chapter_alert"] = json.loads(raw) if raw else None
                state["synopsis_ready"] = await api.get_kv("chapter_synopsis_ready") or None
            except Exception:
                pass

        # ── Wave 1: chapter alert → #chapter-intel ────────────────────────────
        try:
            data = state.get("chapter_alert")
            if data:
                ch_num = data.get("chapter")
                last = await api.get_kv("last_alerted_chapter")
                if ch_num and str(ch_num) != str(last) and ch_num not in self._alerted:
                    channel = await _resolve_channel(self, "chapter_intel_channel_id")
                    if not channel:
                        log.warning("chapter_announce: no #chapter-intel channel found (KV empty and name lookup failed)")
                    elif await _already_posted(self, channel, f"CHAPTER {ch_num} — DETECTED"):
                        # A previous process posted it — heal the local + KV markers.
                        self._alerted.add(ch_num)
                        await api.set_kv("last_alerted_chapter", str(ch_num))
                    else:
                        msg = personality.chapter_alert(
                            ch_num, data.get("detected", []), data.get("debuts", []))
                        if await _safe_send(channel, msg):
                            self._alerted.add(ch_num)
                            await api.set_kv("last_alerted_chapter", str(ch_num))
                            log.info("Chapter alert posted for Ch.%s", ch_num)
        except Exception as e:
            log.warning("chapter_announce alert step failed: %s", e)

        # ── Wave 2: chapter synopsis → #announcements ─────────────────────────
        try:
            syn = state.get("synopsis_ready")
            if syn:
                syn = int(syn)
                last_syn = await api.get_kv("last_synopsis_chapter")
                if str(syn) != str(last_syn) and syn not in self._synopsed:
                    tx = await api.fetch_transmission()
                    if tx and str(tx.get("chapter_number")) == str(syn):
                        channel = await _resolve_channel(self, "announcements_channel_id")
                        if not channel:
                            log.warning("chapter_announce: no #announcements channel found (KV empty and name lookup failed)")
                        elif await _already_posted(self, channel, f"CHAPTER {syn} — PUNK RECORDS SYNOPSIS"):
                            self._synopsed.add(syn)
                            await api.set_kv("last_synopsis_chapter", str(syn))
                        else:
                            msg = personality.chapter_synopsis(
                                syn, tx.get("summary", ""), tx.get("movers", []), api.SITE_URL)
                            ok = await _safe_send(
                                channel, "@everyone\n" + msg,
                                allowed_mentions=discord.AllowedMentions(everyone=True))
                            if ok:
                                self._synopsed.add(syn)
                                await api.set_kv("last_synopsis_chapter", str(syn))
                                log.info("Chapter synopsis posted for Ch.%s", syn)
        except Exception as e:
            log.warning("chapter_announce synopsis step failed: %s", e)

        # ── Mid-week buzz chatter → #market-uplink ────────────────────────────
        # Intel-only: spoiler/meme chatter spiking around one character. Once
        # per character per ISO week, so a trending arc doesn't become spam.
        try:
            buzz = state.get("buzz_alert")
            if buzz and buzz.get("name"):
                key = f"{buzz.get('week', '')}:{buzz['name']}"
                last = await api.get_kv("last_buzz_posted")
                if key != last and key not in self._buzzed:
                    channel = await _resolve_channel(self, "market_uplink_channel_id")
                    if channel:
                        marker = f"UNUSUAL CHATTER — {buzz['name'].upper()}"
                        if await _already_posted(self, channel, marker):
                            self._buzzed.add(key)
                            await api.set_kv("last_buzz_posted", key)
                        else:
                            msg = personality.buzz_chatter(
                                buzz["name"], int(buzz.get("new_posts", 0)), int(buzz.get("week_total", 0)))
                            if await _safe_send(channel, msg):
                                self._buzzed.add(key)
                                await api.set_kv("last_buzz_posted", key)
                                log.info("Buzz chatter posted for %s", key)
        except Exception as e:
            log.warning("chapter_announce buzz step failed: %s", e)

    @weekly_transmission.before_loop
    @random_hot_take.before_loop
    @lore_post.before_loop
    @market_uplink_post.before_loop
    @price_analysis_post.before_loop
    @chapter_announce.before_loop
    async def _wait_ready(self):
        await self.wait_until_ready()


client = VegapunkBot()


@client.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log.error("Command error: %s", error, exc_info=error)
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message("Punk Records encountered an error. Check logs.", ephemeral=True)
        else:
            await interaction.followup.send("Punk Records encountered an error. Check logs.", ephemeral=True)
    except Exception:
        pass


# ── Slash commands ─────────────────────────────────────────────────────────────

@client.tree.command(name="intel", description="Retrieve Punk Records credibility analysis for a character")
@app_commands.describe(character="The One Piece character to analyze")
async def cmd_intel(interaction: discord.Interaction, character: str):
    await interaction.response.defer()
    char = await api.find_character(character, full=True)
    if not char:
        await interaction.followup.send(
            f"{personality.sat('main')}\n"
            f"No field data found for `{character}`. Either this individual has escaped Punk Records "
            f"monitoring, or they do not exist.\n"
            f"*...Both possibilities are concerning in different ways.*"
        )
        return
    pct = api.recent_change_pct(char)
    await interaction.followup.send(
        personality.intel_response(
            name=char["name"],
            faction=char.get("faction", "other"),
            beri=char["beri"],
            change_pct=pct,
            rank=char.get("rank"),
            bio=char.get("bio", ""),
            events=char.get("events", ""),
            sbs=char.get("sbs", []),
        )
    )


@client.tree.command(name="slander", description="Request Punk Records to publish a certified field analysis (unflattering)")
@app_commands.describe(character="The character to be analyzed")
async def cmd_slander(interaction: discord.Interaction, character: str):
    await interaction.response.defer()
    char = await api.find_character(character)
    if not char:
        await interaction.followup.send(
            f"{personality.sat('lilith')}\n"
            f"I cannot slander someone I cannot find. Provide a valid subject, "
            f"or accept that some individuals are beneath Punk Records' notice.\n"
            f"*...Lilith disagrees. Lilith thinks everyone deserves it.*"
        )
        return
    pct = api.recent_change_pct(char)
    await interaction.followup.send(personality.slander_response(char["name"], pct))


@client.tree.command(name="transmission", description="Trigger a Punk Records weekly credibility transmission")
async def cmd_transmission(interaction: discord.Interaction):
    await interaction.response.defer()
    chars = await api.fetch_all_characters()
    movers = sorted(
        [{"name": c["name"], "beri": c["beri"], "change_pct": api.recent_change_pct(c)} for c in chars],
        key=lambda x: x["change_pct"], reverse=True,
    )
    await interaction.followup.send(personality.transmission_response(movers))


@client.tree.command(name="top", description="View the biggest credibility movers in the current cycle")
async def cmd_top(interaction: discord.Interaction):
    await interaction.response.defer()
    chars = await api.fetch_all_characters()
    movers = sorted(
        [{"name": c["name"], "beri": c["beri"], "change_pct": api.recent_change_pct(c)} for c in chars],
        key=lambda x: x["change_pct"], reverse=True,
    )
    await interaction.followup.send(personality.top_response(movers[:5], list(reversed(movers[-5:]))))


@client.tree.command(name="compare", description="Compare the credibility coefficients of two characters")
@app_commands.describe(character1="First character", character2="Second character")
async def cmd_compare(interaction: discord.Interaction, character1: str, character2: str):
    await interaction.response.defer()
    char1, char2 = await api.find_character(character1), await api.find_character(character2)
    missing = character1 if not char1 else (character2 if not char2 else None)
    if missing:
        await interaction.followup.send(
            f"{personality.sat('main')}\nNo data found for `{missing}`. Comparative analysis aborted."
        )
        return
    pct1, pct2 = api.recent_change_pct(char1), api.recent_change_pct(char2)
    await interaction.followup.send(personality.compare_response(char1, char2, pct1, pct2))


@client.tree.command(name="satellite", description="Request analysis from a specific Punk Records satellite")
@app_commands.describe(
    satellite="Which satellite to consult",
    subject="What you want analyzed",
)
@app_commands.choices(satellite=[
    app_commands.Choice(name="Shaka  (Wisdom)",         value="shaka"),
    app_commands.Choice(name="Lilith (Chaos)",          value="lilith"),
    app_commands.Choice(name="Edison (Research)",       value="edison"),
    app_commands.Choice(name="Pythagoras (Data)",       value="pythagoras"),
    app_commands.Choice(name="Atlas  (Combat)",         value="atlas"),
    app_commands.Choice(name="York   (Basic Functions)",value="york"),
])
async def cmd_satellite(interaction: discord.Interaction, satellite: str, subject: str):
    await interaction.response.send_message(personality.satellite_response(satellite, subject))


# ── Server setup ──────────────────────────────────────────────────────────────

@client.tree.command(name="setup-server", description="One-time Punk Records server setup — creates roles, categories, and channels")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def cmd_setup_server(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild = interaction.guild

    lines: list[str] = []

    # ── Roles ──────────────────────────────────────────────────────────────────
    # Imu + Gorosei first so they exist when building permission overwrites
    role_specs = [
        # (name,               hex colour,  hoist,  mentionable)
        ("◈ Imu",             0xf5e642,    True,   False),   # owner — assign to yourself
        ("◈ Punk Records",    0xe040fb,    True,   False),   # admin
        ("◈ Gorosei",         0x800020,    True,   False),   # private access — close friends, no manage perms
        ("◈ Marine Admiral",  0x29b6f6,    True,   False),
        ("◈ Yonko",           0xd4a017,    True,   True),
        ("◈ Supernova",       0x9b59b6,    True,   True),
        ("◈ Revolutionary",   0x00d084,    True,   True),
        ("◈ NewKama",         0x5a4a78,    False,  False),
    ]

    created_roles: dict[str, discord.Role] = {}
    for rname, colour, hoist, mentionable in role_specs:
        existing = discord.utils.get(guild.roles, name=rname)
        if existing:
            created_roles[rname] = existing
        else:
            r = await guild.create_role(
                name=rname,
                color=discord.Color(colour),
                hoist=hoist,
                mentionable=mentionable,
                reason="Punk Records server setup",
            )
            created_roles[rname] = r
            lines.append(f"✅ Role **{rname}**")

    everyone     = guild.default_role
    admin_role   = created_roles.get("◈ Punk Records")
    mod_role     = created_roles.get("◈ Marine Admiral")
    gorosei_role = created_roles.get("◈ Gorosei")
    imu_role     = created_roles.get("◈ Imu")

    # ── Permission helpers ──────────────────────────────────────────────────────
    def read_only() -> dict:
        ow = {everyone: discord.PermissionOverwrite(view_channel=True, send_messages=False)}
        if admin_role:   ow[admin_role]   = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True)
        if mod_role:     ow[mod_role]     = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True)
        if imu_role:     ow[imu_role]     = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True)
        return ow

    def private() -> dict:
        ow = {everyone: discord.PermissionOverwrite(view_channel=False)}
        if admin_role: ow[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        if mod_role:   ow[mod_role]   = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        if imu_role:   ow[imu_role]   = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        return ow

    def gorosei_only() -> dict:
        """Private voice + text — Gorosei, Imu, and Punk Records only. No manage perms for Gorosei."""
        ow = {everyone: discord.PermissionOverwrite(view_channel=False, connect=False)}
        if admin_role:   ow[admin_role]   = discord.PermissionOverwrite(view_channel=True, send_messages=True, connect=True, manage_channels=True)
        if imu_role:     ow[imu_role]     = discord.PermissionOverwrite(view_channel=True, send_messages=True, connect=True, manage_channels=True)
        if gorosei_role: ow[gorosei_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, connect=True, manage_channels=False)
        return ow

    # ── Channel helpers ─────────────────────────────────────────────────────────
    async def ensure_category(cname: str, overwrites: dict | None = None) -> discord.CategoryChannel:
        cat = discord.utils.get(guild.categories, name=cname)
        if cat:
            return cat
        kwargs: dict = {"reason": "Punk Records server setup"}
        if overwrites: kwargs["overwrites"] = overwrites
        return await guild.create_category(cname, **kwargs)

    async def ensure_channel(
        category: discord.CategoryChannel,
        cname: str,
        overwrites: dict | None = None,
        topic: str = "",
    ) -> discord.TextChannel:
        ch = discord.utils.get(category.channels, name=cname)
        if ch:
            return ch
        kwargs: dict = {"category": category, "reason": "Punk Records server setup"}
        if overwrites: kwargs["overwrites"] = overwrites
        if topic:      kwargs["topic"] = topic
        ch = await guild.create_text_channel(cname, **kwargs)
        lines.append(f"  # {cname}")
        return ch

    async def ensure_voice(
        category: discord.CategoryChannel,
        cname: str,
        user_limit: int = 0,
        overwrites: dict | None = None,
    ) -> discord.VoiceChannel:
        ch = discord.utils.get(category.voice_channels, name=cname)
        if ch:
            return ch
        kwargs: dict = {"category": category, "user_limit": user_limit, "reason": "Punk Records server setup"}
        if overwrites: kwargs["overwrites"] = overwrites
        ch = await guild.create_voice_channel(cname, **kwargs)
        lines.append(f"  🔊 {cname}" + (f" ({user_limit})" if user_limit else ""))
        return ch

    # ── 📡 PUNK RECORDS ──
    cat_pr    = await ensure_category("📡 PUNK RECORDS")
    ch_ann    = await ensure_channel(cat_pr, "announcements",  read_only(), "Official Punk Records announcements & market alerts")
    ch_uplink = await ensure_channel(cat_pr, "market-uplink",  read_only(), "Weekly Vegapunk credibility transmission")
    ch_chap   = await ensure_channel(cat_pr, "chapter-intel",  read_only(), "Chapter drop alerts and character price impact")

    # ── 📊 EXCHANGE FLOOR ──
    cat_ex   = await ensure_category("📊 EXCHANGE FLOOR")
    ch_gen   = await ensure_channel(cat_ex, "general",           topic="Grand Line Stock Exchange — trade talk & market discussion")
    ch_pa    = await ensure_channel(cat_ex, "price-analysis",    topic="Character price analysis and predictions")
    _        = await ensure_channel(cat_ex, "character-requests",topic="Request new characters for the exchange")

    # ── 🏴‍☠️ GRAND LINE ──
    cat_gl  = await ensure_category("🏴‍☠️ GRAND LINE")
    ch_op   = await ensure_channel(cat_gl, "one-piece-discussion", topic="General One Piece discussion — spoilers welcome")
    ch_mem  = await ensure_channel(cat_gl, "memes")
    ch_intro= await ensure_channel(cat_gl, "introduce-yourself",   topic="Tell the crew who you are")

    # ── 🎙️ VOICE ──
    cat_vc = await ensure_category("🎙️ VOICE")
    await ensure_voice(cat_vc, "📡 Punk Records Community")            # unlimited — main lounge
    await ensure_voice(cat_vc, "⚓ Marineford",          user_limit=10)
    await ensure_voice(cat_vc, "🌊 Laugh Tale",           user_limit=10)
    await ensure_voice(cat_vc, "🏝️ Sabaody Archipelago", user_limit=10)
    await ensure_voice(cat_vc, "⚡ Wano",                 user_limit=10)
    await ensure_voice(cat_vc, "🎪 Dressrosa",            user_limit=10)

    # ── 👁️ EMPTY THRONE — Gorosei private ──
    cat_gt = await ensure_category("👁️ EMPTY THRONE", gorosei_only())
    _      = await ensure_channel(cat_gt, "five-elders", gorosei_only(), "Private — Gorosei only")
    _      = await ensure_voice(cat_gt, "🌑 The Empty Throne", overwrites=gorosei_only())

    # ── 🔒 ADMIN DOCK ──
    cat_admin = await ensure_category("🔒 ADMIN DOCK")
    _ = await ensure_channel(cat_admin, "bot-logs",    private())
    _ = await ensure_channel(cat_admin, "admin-notes", private())

    # ── Persist key channel IDs to BotKV ──
    await api.set_kv("announcements_channel_id",          str(ch_ann.id))
    await api.set_kv("market_uplink_channel_id",          str(ch_uplink.id))
    await api.set_kv("chapter_intel_channel_id",          str(ch_chap.id))
    await api.set_kv("general_channel_id",                str(ch_gen.id))
    await api.set_kv("price_analysis_channel_id",         str(ch_pa.id))
    await api.set_kv("one_piece_discussion_channel_id",   str(ch_op.id))
    await api.set_kv("memes_channel_id",                  str(ch_mem.id))
    await api.set_kv("introduce_yourself_channel_id",     str(ch_intro.id))

    # Refresh local cache immediately
    await _load_ch_cache()

    summary = "\n".join(lines) if lines else "All roles and channels already existed — nothing to create."
    msg = (
        f"**◈ Punk Records — Setup Complete**\n\n"
        f"{summary}\n\n"
        f"**Next steps:**\n"
        f"• Go to **Server Settings → Roles** and drag **◈ Imu** to the top, then **◈ Gorosei** just below **◈ Punk Records**\n"
        f"• Assign **◈ Imu** to yourself\n"
        f"• Assign **◈ Gorosei** to close friends — they get {cat_gt.mention} access but cannot manage anything\n"
        f"• Transmission → {ch_ann.mention} · General chat → {ch_gen.mention} · Widgetbot channel → `{ch_gen.id}`"
    )
    await interaction.followup.send(msg, ephemeral=True)


# ── Health check server (keeps Railway happy) ─────────────────────────────────

class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Punk Records online.")
    def log_message(self, *args):
        pass  # suppress access logs

def _start_health_server():
    port = int(os.getenv("PORT", "8080"))
    HTTPServer(("0.0.0.0", port), _Health).serve_forever()


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("VEGAPUNK_BOT_TOKEN is not set.")
    threading.Thread(target=_start_health_server, daemon=True).start()
    client.run(TOKEN)
