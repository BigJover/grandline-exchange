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


# ── Bot client ────────────────────────────────────────────────────────────────

class VegapunkBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        if TRANSMISSION_CHS:
            self.weekly_transmission.start()
            self.random_hot_take.start()
        log.info("Punk Records systems online.")

    async def on_ready(self):
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="Punk Records | /intel"
            )
        )
        log.info("Vegapunk connected as %s", self.user)

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

        channel_id = str(message.channel.id)
        if INGEST_ALL or channel_id in INGEST_CHANNELS:
            await api.ingest_message(
                channel=getattr(message.channel, "name", channel_id),
                author=str(message.author),
                content=message.content,
            )

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
        if last_week == current_week:
            return  # already fired this week — skip

        chars = await api.fetch_all_characters()
        movers = sorted(
            [{"name": c["name"], "beri": c["beri"], "change_pct": api.recent_change_pct(c)} for c in chars],
            key=lambda x: x["change_pct"], reverse=True,
        )
        message = personality.transmission_response(movers)

        # Build target set: BotKV announcements channel (set by /setup-server) + env var fallback
        target_ids: set[int] = set()
        kv_ch = await api.get_kv("announcements_channel_id")
        if kv_ch:
            target_ids.add(int(kv_ch))
        for ch_id in TRANSMISSION_CHS:
            target_ids.add(ch_id)

        sent = False
        for ch_id in target_ids:
            ch = self.get_channel(ch_id)
            if ch:
                await ch.send(message)
                sent = True
        if sent:
            await api.set_kv("last_tx_week", current_week)
            log.info("Weekly transmission sent for %s", current_week)

    @tasks.loop(hours=6)
    async def random_hot_take(self):
        if random.random() > 0.40:   # ~40 % chance each 6-hour tick
            return
        chars = await api.fetch_all_characters()
        if not chars:
            return
        char = random.choice(chars[:30])
        pct = api.recent_change_pct(char)
        message = personality.hot_take(char["name"], pct, char.get("faction", "other"))
        for ch_id in TRANSMISSION_CHS:
            ch = self.get_channel(ch_id)
            if ch:
                await ch.send(message)

    @weekly_transmission.before_loop
    @random_hot_take.before_loop
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

    # ── Roles (created top → bottom so hierarchy is correct) ──
    role_specs = [
        # (name,                  hex colour,  hoist,  mentionable)
        ("◈ Punk Records",        0xe040fb,    True,   False),
        ("◈ Marine Admiral",      0x29b6f6,    True,   False),
        ("◈ Yonko",               0xd4a017,    True,   True),
        ("◈ Supernova",           0x9b59b6,    True,   True),
        ("◈ Revolutionary",       0x00d084,    True,   True),
        ("◈ NewKama",             0x5a4a78,    False,  False),
    ]

    created_roles: dict[str, discord.Role] = {}
    for name, colour, hoist, mentionable in role_specs:
        existing = discord.utils.get(guild.roles, name=name)
        if existing:
            created_roles[name] = existing
        else:
            r = await guild.create_role(
                name=name,
                color=discord.Color(colour),
                hoist=hoist,
                mentionable=mentionable,
                reason="Punk Records server setup",
            )
            created_roles[name] = r
            lines.append(f"✅ Role **{name}**")

    everyone   = guild.default_role
    admin_role = created_roles.get("◈ Punk Records")
    mod_role   = created_roles.get("◈ Marine Admiral")

    # ── Permission helpers ──
    def read_only() -> dict:
        ow = {everyone: discord.PermissionOverwrite(view_channel=True, send_messages=False)}
        if admin_role: ow[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True)
        if mod_role:   ow[mod_role]   = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True)
        return ow

    def private() -> dict:
        ow = {everyone: discord.PermissionOverwrite(view_channel=False)}
        if admin_role: ow[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        if mod_role:   ow[mod_role]   = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        return ow

    async def ensure_category(name: str) -> discord.CategoryChannel:
        cat = discord.utils.get(guild.categories, name=name)
        return cat or await guild.create_category(name, reason="Punk Records server setup")

    async def ensure_channel(
        category: discord.CategoryChannel,
        name: str,
        overwrites: dict | None = None,
        topic: str = "",
    ) -> discord.TextChannel:
        ch = discord.utils.get(category.channels, name=name)
        if ch:
            return ch
        kwargs: dict = {"category": category, "reason": "Punk Records server setup"}
        if overwrites: kwargs["overwrites"] = overwrites
        if topic:      kwargs["topic"] = topic
        ch = await guild.create_text_channel(name, **kwargs)
        lines.append(f"  # {name}")
        return ch

    # ── 📡 PUNK RECORDS ──
    cat_pr    = await ensure_category("📡 PUNK RECORDS")
    ch_ann    = await ensure_channel(cat_pr, "announcements",  read_only(), "Official Punk Records announcements & market alerts")
    ch_uplink = await ensure_channel(cat_pr, "market-uplink",  read_only(), "Weekly Vegapunk credibility transmission")
    ch_chap   = await ensure_channel(cat_pr, "chapter-intel",  read_only(), "Chapter drop alerts and character price impact")

    # ── 📊 EXCHANGE FLOOR ──
    cat_ex  = await ensure_category("📊 EXCHANGE FLOOR")
    ch_gen  = await ensure_channel(cat_ex, "general",           topic="Grand Line Stock Exchange — trade talk & market discussion")
    _       = await ensure_channel(cat_ex, "price-analysis",    topic="Character price analysis and predictions")
    _       = await ensure_channel(cat_ex, "character-requests",topic="Request new characters for the exchange")

    # ── 🏴‍☠️ GRAND LINE ──
    cat_gl = await ensure_category("🏴‍☠️ GRAND LINE")
    _ = await ensure_channel(cat_gl, "one-piece-discussion", topic="General One Piece discussion — spoilers welcome")
    _ = await ensure_channel(cat_gl, "memes")
    _ = await ensure_channel(cat_gl, "introduce-yourself",   topic="Tell the crew who you are")

    # ── 🔒 ADMIN DOCK ──
    cat_admin = await ensure_category("🔒 ADMIN DOCK")
    _ = await ensure_channel(cat_admin, "bot-logs",    private())
    _ = await ensure_channel(cat_admin, "admin-notes", private())

    # ── Persist key channel IDs to BotKV ──
    await api.set_kv("announcements_channel_id",  str(ch_ann.id))
    await api.set_kv("market_uplink_channel_id",  str(ch_uplink.id))
    await api.set_kv("chapter_intel_channel_id",  str(ch_chap.id))
    await api.set_kv("general_channel_id",        str(ch_gen.id))

    summary = "\n".join(lines) if lines else "All roles and channels already existed."
    msg = (
        f"**◈ Punk Records — Setup Complete**\n\n"
        f"{summary}\n\n"
        f"**Key channel IDs (update Railway env vars if needed):**\n"
        f"• `VEGAPUNK_TRANSMISSION_CHANNELS` → `{ch_ann.id}` *(weekly transmission now posts to {ch_ann.mention})*\n"
        f"• Chapter intel → `{ch_chap.id}`\n"
        f"• General (Widgetbot) → `{ch_gen.id}`\n\n"
        f"Make sure Vegapunk has **Manage Roles** and **Manage Channels** permissions."
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
