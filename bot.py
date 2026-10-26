"""
Discord Community Bot — Combined Version
Python 3.10+

Install:
    pip install -U discord.py Pillow

Windows PowerShell (set token for this terminal):
    $env:DISCORD_TOKEN="YOUR_BOT_TOKEN_HERE"
    python bot.py

Before running:
- Enable Message Content Intent and Server Members Intent in Discord Developer Portal.
- Give the bot only the permissions it needs:
  View Channels, Send Messages, Embed Links, Attach Files, Read Message History,
  Manage Channels, Manage Messages, Moderate Members, Kick Members, Ban Members.
- Place the bot's role above members it needs to moderate.
- Keep kiss_stats.db and back up the file to preserve kiss counts.

Prefix: comma (,)
NOTE: Most profile/level/welcome/AFK/queue data is in memory and resets after restart.
Kiss counters use SQLite and persist across restarts.
"""

import os
import io
import re
import time
import random
import asyncio
import sqlite3
import datetime
from collections import defaultdict, deque

import discord
from discord.ext import commands
from PIL import Image, ImageDraw


# ==================== CONFIGURATION ====================

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = ","
PINK = discord.Color.from_rgb(255, 105, 180)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

# In-memory data resets when the bot restarts.
afk_status = {}
user_xp = defaultdict(int)
user_levels = defaultdict(lambda: 1)
welcome_channels = {}
user_interests = {}

# Anti-spam data
recent_messages = defaultdict(deque)
spam_timed_out = set()
INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord\.gg/|discord(?:app)?\.com/invite/)[\w-]+",
    re.IGNORECASE,
)

# Random-chat queue; entries are (guild_id, user_id)
random_queue = []
random_active = set()


# ==================== HELPERS ====================

def normalize_interest(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def parse_interests(text: str) -> dict:
    # Commas separate interests and allow multi-word tags.
    # Without commas, each word becomes its own interest.
    values = text.split(",") if "," in text else text.split()
    result = {}
    for value in values:
        display = re.sub(r"\s+", " ", value.strip())[:50]
        if display:
            result.setdefault(normalize_interest(display), display)
    return result


def safe_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9-]", "-", value.lower())
    return value[:70].strip("-") or "chat"


async def try_timeout(member: discord.Member, seconds: int, reason: str) -> bool:
    try:
        await member.timeout(datetime.timedelta(seconds=seconds), reason=reason)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


# ==================== MODERATION ====================

@bot.command(name="ban")
@commands.has_permissions(ban_members=True)
@commands.bot_has_permissions(ban_members=True)
async def ban_member(ctx, member: discord.Member, *, reason="No reason provided"):
    if member.id == ctx.author.id:
        await ctx.send("❌ You can't ban yourself.")
        return
    try:
        await member.ban(reason=f"{reason} | Moderator: {ctx.author}")
        await ctx.send(f"🔨 Banned **{member}**. Reason: {reason}")
    except discord.Forbidden:
        await ctx.send("❌ I can't ban that member. Check my role position and permissions.")


@bot.command(name="kick")
@commands.has_permissions(kick_members=True)
@commands.bot_has_permissions(kick_members=True)
async def kick_member(ctx, member: discord.Member, *, reason="No reason provided"):
    if member.id == ctx.author.id:
        await ctx.send("❌ You can't kick yourself.")
        return
    try:
        await member.kick(reason=f"{reason} | Moderator: {ctx.author}")
        await ctx.send(f"👢 Kicked **{member}**. Reason: {reason}")
    except discord.Forbidden:
        await ctx.send("❌ I can't kick that member. Check my role position and permissions.")


# ==================== AFK ====================

@bot.command(name="afk", aliases=["a"])
async def set_afk(ctx, *, status="AFK"):
    afk_status[ctx.author.id] = status[:200]
    await ctx.send(f"💤 {ctx.author.mention}, your AFK status is set: **{status[:200]}**")


# ==================== LEVEL / RANK ====================

@bot.command(name="rank", aliases=["level"])
async def rank(ctx, member: discord.Member = None):
    member = member or ctx.author
    xp = user_xp[member.id]
    level = user_levels[member.id]
    progress = xp % 100
    filled = int(progress / 100 * 10)
    bar = "🟪" * filled + "⬜" * (10 - filled)

    embed = discord.Embed(title=f"🌟 {member.display_name}'s Rank", color=PINK)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Level", value=str(level), inline=True)
    embed.add_field(name="Total XP", value=str(xp), inline=True)
    embed.add_field(name="Progress", value=f"{bar}\n{progress}/100 XP", inline=False)
    await ctx.send(embed=embed)


# ==================== WELCOME ====================

@bot.command(name="setwelcome")
@commands.has_permissions(manage_guild=True)
async def set_welcome(ctx, channel: discord.TextChannel):
    welcome_channels[ctx.guild.id] = channel.id
    await ctx.send(f"👋 Welcome messages will be sent to {channel.mention}.")


@bot.event
async def on_member_join(member):
    channel_id = welcome_channels.get(member.guild.id)
    channel = member.guild.get_channel(channel_id) if channel_id else None
    if channel:
        embed = discord.Embed(
            title="🌸 Welcome!",
            description=f"Welcome to **{member.guild.name}**, {member.mention}!\nMake yourself at home 💗",
            color=PINK,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass


# ==================== TICKETS ====================

class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Ticket",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="community_ticket_close",
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("This isn't a ticket channel.", ephemeral=True)
            return
        try:
            await interaction.response.send_message("🔒 Closing this ticket in 5 seconds...")
            await asyncio.sleep(5)
            await channel.delete(reason=f"Ticket closed by {interaction.user}")
        except discord.Forbidden:
            # The interaction may already have been acknowledged.
            try:
                await interaction.followup.send("I need Manage Channels to close this ticket.", ephemeral=True)
            except discord.HTTPException:
                pass


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Open a Ticket",
        emoji="🎫",
        style=discord.ButtonStyle.success,
        custom_id="community_ticket_open",
    )
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Tickets can only be opened in a server.", ephemeral=True)
            return

        channel_name = f"ticket-{interaction.user.id}"
        existing = discord.utils.get(guild.text_channels, name=channel_name)
        if existing:
            await interaction.response.send_message(
                f"You already have a ticket: {existing.mention}", ephemeral=True
            )
            return

        bot_member = guild.me
        if bot_member is None:
            await interaction.response.send_message("I couldn't identify my server member.", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
            bot_member: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True, read_message_history=True
            ),
        }
        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                topic=f"Support ticket opened by {interaction.user} ({interaction.user.id})",
                reason="Ticket opened from ticket panel",
            )
            await channel.send(
                f"{interaction.user.mention}, tell the staff how we can help.",
                view=CloseTicketView(),
            )
            await interaction.response.send_message(
                f"🎫 Your ticket is ready: {channel.mention}", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I need Manage Channels permission to create tickets.", ephemeral=True
            )


@bot.command(name="ticketpanel")
@commands.has_permissions(manage_channels=True)
async def ticket_panel(ctx):
    embed = discord.Embed(
        title="🎫 Support Tickets",
        description="Press the button below to create a private ticket with the staff team.",
        color=PINK,
    )
    await ctx.send(embed=embed, view=TicketPanelView())


# ==================== SHIP ====================

@bot.command(name="ship")
async def ship(ctx, first: discord.Member, second: discord.Member = None):
    second = second or ctx.author
    score = random.randint(1, 100)
    filled = score // 10
    bar = "💗" * filled + "🤍" * (10 - filled)
    first_name = first.display_name
    second_name = second.display_name
    ship_name = first_name[:max(1, len(first_name) // 2)] + second_name[len(second_name) // 2:]

    embed = discord.Embed(
        title="💘 Ship Meter",
        description=(
            f"**{first_name}** 💞 **{second_name}**\n\n"
            f"**Compatibility:** {score}%\n{bar}\n"
            f"**Ship name:** {ship_name[:64]}"
        ),
        color=PINK,
    )
    await ctx.send(embed=embed)


# ==================== RANDOM CHAT ====================

async def close_random_chat(channel, guild_id, user_ids):
    await asyncio.sleep(300)
    for user_id in user_ids:
        random_active.discard((guild_id, user_id))
    try:
        await channel.delete(reason="Random chat session ended after 5 minutes")
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


@bot.command(name="randomchat")
async def random_chat(ctx):
    if ctx.guild is None:
        await ctx.send("Use random chat inside a server.")
        return

    key = (ctx.guild.id, ctx.author.id)
    if key in random_active or key in random_queue:
        await ctx.send("⏳ You're already queued or in a random chat.")
        return

    partner_entry = next(
        (entry for entry in random_queue if entry[0] == ctx.guild.id and entry[1] != ctx.author.id),
        None,
    )
    if partner_entry is None:
        random_queue.append(key)
        await ctx.send("🎲 You're in the random chat queue! I'll match you when someone joins.")
        return

    random_queue.remove(partner_entry)
    partner = ctx.guild.get_member(partner_entry[1])
    if partner is None or partner.bot:
        random_queue.append(key)
        await ctx.send("🎲 You're queued. Waiting for another member...")
        return

    random_active.add(key)
    random_active.add((ctx.guild.id, partner.id))
    overwrites = {
        ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
        ctx.author: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        partner: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    if ctx.guild.me:
        overwrites[ctx.guild.me] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True, read_message_history=True
        )

    try:
        channel = await ctx.guild.create_text_channel(
            name=f"random-{ctx.author.id}-{partner.id}",
            overwrites=overwrites,
            reason="Random chat match",
        )
        await channel.send(
            f"🎉 Matched! {ctx.author.mention} and {partner.mention}, say hi!\n"
            "This private chat closes automatically in **5 minutes**."
        )
        await ctx.send(f"💞 You're matched with {partner.mention}: {channel.mention}")
        try:
            await partner.send(f"🎲 You've been matched for random chat in **{ctx.guild.name}**: {channel.mention}")
        except discord.HTTPException:
            pass
        asyncio.create_task(close_random_chat(channel, ctx.guild.id, [ctx.author.id, partner.id]))
    except discord.Forbidden:
        random_active.discard(key)
        random_active.discard((ctx.guild.id, partner.id))
        await ctx.send("❌ I need Manage Channels permission to create a private random chat.")


# ==================== INTERESTS ====================

INTEREST_CATEGORIES = {
    "Gaming": [
        "gaming", "roblox", "minecraft", "fortnite", "valorant", "call of duty",
        "overwatch", "apex legends", "gta", "league of legends", "genshin impact",
        "honkai star rail", "stardew valley", "the sims", "among us", "pokemon",
        "horror games", "cozy games", "mobile games", "pc gaming", "console gaming",
        "indie games", "game development",
    ],
    "Anime & Manga": [
        "anime", "manga", "manhwa", "webtoons", "cosplay", "one piece", "naruto",
        "jujutsu kaisen", "demon slayer", "attack on titan", "chainsaw man",
        "my hero academia", "romance anime", "slice of life", "studio ghibli",
    ],
    "Music": [
        "music", "kpop", "k-pop", "pop", "rap", "hip hop", "r&b", "rock", "metal",
        "indie music", "edm", "j-pop", "j-rock", "lofi", "classical music",
        "country music", "songwriting", "singing", "producing music", "playing guitar",
        "playing piano", "concerts",
    ],
    "TV & Movies": [
        "tv", "tv shows", "movies", "horror movies", "comedies", "romance movies",
        "thrillers", "sci-fi", "fantasy", "k-dramas", "korean dramas", "sitcoms",
        "reality tv", "disney", "marvel", "dc", "stranger things", "heartstopper",
        "netflix", "documentaries",
    ],
    "Social & Internet": [
        "making friends", "chatting", "memes", "tiktok", "youtube", "twitch",
        "streaming", "discord", "internet culture", "content creation", "selfies",
        "photography", "roleplay", "voice calls", "gaming buddies",
    ],
    "Arts & Creativity": [
        "art", "drawing", "digital art", "painting", "writing", "fanfiction", "poetry",
        "animation", "graphic design", "fashion", "makeup", "nail art", "crafts",
        "editing videos", "video editing", "3d modeling", "character design",
    ],
    "Lifestyle": [
        "animals", "cats", "dogs", "pets", "cooking", "baking", "beauty", "skincare",
        "fitness", "gym", "travel", "nature", "reading", "books", "journaling",
        "collecting", "room decor", "thrifting", "astrology",
    ],
    "Sports & Activities": [
        "sports", "basketball", "soccer", "football", "volleyball", "tennis",
        "badminton", "skateboarding", "dancing", "hip hop dance", "k-pop dance",
        "swimming", "running", "hiking", "cycling", "martial arts", "cheerleading",
    ],
    "Learning & Tech": [
        "coding", "programming", "technology", "computers", "robotics", "science",
        "space", "astronomy", "history", "psychology", "languages",
        "learning languages", "math", "game design", "web development", "ai",
    ],
    "K-pop": [
        "k-pop idols", "bts", "blackpink", "stray kids", "twice", "newjeans",
        "enhypen", "aespa", "seventeen", "le sserafim", "ive", "txt", "exo",
        "red velvet", "itzy", "nct",
    ],
}


@bot.group(name="interests", invoke_without_command=True)
async def interests(ctx):
    tags = user_interests.get(ctx.author.id, {})
    description = "\n".join(f"• {tag}" for tag in tags.values()) if tags else (
        "You haven't added interests yet!\nTry `,interests add gaming, roblox, kpop`"
    )
    embed = discord.Embed(
        title=f"💖 {ctx.author.display_name}'s Interests",
        description=description,
        color=PINK,
    )
    await ctx.send(embed=embed)


@interests.command(name="add")
async def interests_add(ctx, *, items: str):
    new_tags = parse_interests(items)
    tags = user_interests.setdefault(ctx.author.id, {})
    added = []
    for key, display in new_tags.items():
        if key not in tags and len(tags) < 50:
            tags[key] = display
            added.append(display)
    if added:
        await ctx.send("💖 Added: " + ", ".join(f"`{tag}`" for tag in added))
    else:
        await ctx.send("✨ Those interests are already saved, or you've reached the 50-interest limit.")


@interests.command(name="remove")
async def interests_remove(ctx, *, items: str):
    tags = user_interests.get(ctx.author.id, {})
    removed = []
    for key in parse_interests(items):
        if key in tags:
            removed.append(tags.pop(key))
    await ctx.send(
        "🗑️ Removed: " + ", ".join(f"`{tag}`" for tag in removed)
        if removed else "❌ Couldn't find those interests in your profile."
    )


@interests.command(name="clear")
async def interests_clear(ctx):
    user_interests.pop(ctx.author.id, None)
    await ctx.send("🧹 All your interests have been cleared!")


@bot.command(name="interestlist", aliases=["allinterests", "interesthelp"])
async def interest_list(ctx):
    embed = discord.Embed(
        title="🌈 Interest Directory",
        description="Pick from these ideas or add your own with `,interests add gaming, horror movies`.",
        color=PINK,
    )
    for category, options in INTEREST_CATEGORIES.items():
        embed.add_field(name=f"✨ {category}", value=", ".join(options), inline=False)
    await ctx.send(embed=embed)


@bot.group(name="search", invoke_without_command=True)
async def search_group(ctx):
    await ctx.send("Try `,search interest roblox` to find members with a shared interest.")


@search_group.command(name="interest")
async def search_interest(ctx, *, interest: str):
    key = normalize_interest(interest)
    matches = [
        member for member in ctx.guild.members
        if not member.bot and member.id != ctx.author.id
        and key in user_interests.get(member.id, {})
    ]
    if not matches:
        await ctx.send(f"🔎 No members found with **{interest}** yet.")
        return
    description = "\n".join(f"💗 {member.mention}" for member in matches[:25])
    if len(matches) > 25:
        description += f"\n…and {len(matches) - 25} more!"
    await ctx.send(embed=discord.Embed(
        title=f"🔎 Members interested in {interest}",
        description=description,
        color=PINK,
    ))


@bot.command(name="searchinterest", aliases=["findinterest"])
async def search_interest_alias(ctx, *, interest: str):
    key = normalize_interest(interest)
    matches = [
        member for member in ctx.guild.members
        if not member.bot and member.id != ctx.author.id
        and key in user_interests.get(member.id, {})
    ]
    if not matches:
        await ctx.send(f"🔎 No members found with **{interest}** yet.")
        return
    await ctx.send(
        f"🔎 Members interested in **{interest}**: "
        + ", ".join(member.mention for member in matches[:25])
    )


@bot.command(name="sharedinterests", aliases=["commoninterests"])
async def shared_interests(ctx, member: discord.Member):
    mine = user_interests.get(ctx.author.id, {})
    theirs = user_interests.get(member.id, {})
    shared = [tag for key, tag in mine.items() if key in theirs]
    embed = discord.Embed(
        title=f"💞 Shared Interests with {member.display_name}",
        description="\n".join(f"✨ {tag}" for tag in shared) if shared else "No shared interests found yet.",
        color=PINK,
    )
    await ctx.send(embed=embed)


# ==================== SAFEEXIT ====================

SAFEEXIT_TEMPLATES = {
    "polite": [
        "I'm going to head out of this conversation now. Take care!",
        "Thanks for chatting! I'm going to do my own thing for a while.",
        "I'm not really up for talking right now, but I wish you well.",
    ],
    "dms": [
        "I don't want to continue this conversation in DMs. Please respect that.",
        "I'm not comfortable chatting privately, so I'm going to end this conversation.",
        "I'd prefer not to receive more DMs. Thanks for understanding.",
    ],
    "boundary": [
        "I'm not comfortable with this topic. Can we change the subject?",
        "That crosses a boundary for me, so I'm going to step away.",
        "I'm not okay with how this conversation is going. I'm ending it here.",
    ],
    "friends": [
        "I'm feeling overwhelmed, so I'm going to take some space.",
        "I need some time to myself. It's nothing I want to argue about.",
        "I don't want to make this a big thing, but I need to leave for now.",
    ],
    "firm": [
        "I've said no, and I need you to respect that.",
        "I don't want to continue this conversation. Please stop contacting me.",
        "Please don't contact me again. I won't be responding further.",
    ],
}

SAFEEXIT_ADVICE = {
    "advice": [
        "You don't owe anyone a long explanation for ending a conversation.",
        "A short, calm message is enough. You can leave after sending it.",
        "You can stop replying if someone ignores your boundaries.",
    ],
    "safety": [
        "If you feel unsafe, prioritize getting somewhere safe over being polite.",
        "Save relevant messages if you may need to report the behavior.",
        "Block and report anyone who threatens, harasses, or pressures you.",
        "If there's immediate danger, contact local emergency services or someone you trust.",
    ],
}


@bot.group(name="safeexit", invoke_without_command=True)
async def safeexit(ctx):
    embed = discord.Embed(
        title="🌷 SafeExit — Your Boundaries Matter",
        description=(
            "` ,safeexit polite` — Leave a chat politely\n"
            "` ,safeexit dms` — Decline private messages\n"
            "` ,safeexit boundary` — Set a boundary\n"
            "` ,safeexit friends` — Take space from someone\n"
            "` ,safeexit firm` — Be direct and firm\n"
            "` ,safeexit advice` — Get a helpful reminder\n"
            "` ,safeexit safety` — Safety-focused advice"
        ).replace("` ,", "`,"),
        color=PINK,
    )
    embed.set_footer(text="You can leave any conversation. You don't owe anyone access to you.")
    await ctx.send(embed=embed)


async def send_safeexit(ctx, title, message):
    embed = discord.Embed(title=title, description=f"> {message}", color=PINK)
    embed.set_footer(text="🌸 Be kind to yourself. Your boundaries matter.")
    await ctx.send(embed=embed)


@safeexit.command(name="polite")
async def safeexit_polite(ctx):
    await send_safeexit(ctx, "🌷 A Polite Exit", random.choice(SAFEEXIT_TEMPLATES["polite"]))


@safeexit.command(name="dms")
async def safeexit_dms(ctx):
    await send_safeexit(ctx, "💌 Ending DMs", random.choice(SAFEEXIT_TEMPLATES["dms"]))


@safeexit.command(name="boundary")
async def safeexit_boundary(ctx):
    await send_safeexit(ctx, "🛑 Setting a Boundary", random.choice(SAFEEXIT_TEMPLATES["boundary"]))


@safeexit.command(name="friends")
async def safeexit_friends(ctx):
    await send_safeexit(ctx, "🫶 Taking Some Space", random.choice(SAFEEXIT_TEMPLATES["friends"]))


@safeexit.command(name="firm")
async def safeexit_firm(ctx):
    await send_safeexit(ctx, "🛡️ A Firm Response", random.choice(SAFEEXIT_TEMPLATES["firm"]))


@safeexit.command(name="advice")
async def safeexit_advice(ctx):
    await send_safeexit(ctx, "🌼 A Reminder for You", random.choice(SAFEEXIT_ADVICE["advice"]))


@safeexit.command(name="safety")
async def safeexit_safety(ctx):
    await send_safeexit(ctx, "🛡️ Your Safety Comes First", random.choice(SAFEEXIT_ADVICE["safety"]))


# ==================== PET GIF ====================

def make_pet_gif(avatar_bytes):
    size = 112
    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
    avatar = avatar.resize((size, size), Image.Resampling.LANCZOS)
    frames = []

    for hand_y, squash in [(0, 0), (5, 1), (11, 3), (15, 5), (11, 3), (5, 1)]:
        frame = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        avatar_height = size - squash
        resized_avatar = avatar.resize((size, avatar_height), Image.Resampling.LANCZOS)
        frame.alpha_composite(resized_avatar, (0, squash))
        draw = ImageDraw.Draw(frame)
        skin = (255, 205, 170, 255)
        outline = (180, 120, 100, 255)
        draw.rounded_rectangle((49, -18 + hand_y, 76, 27 + hand_y), radius=10, fill=skin, outline=outline, width=2)
        draw.ellipse((39, 10 + hand_y, 84, 43 + hand_y), fill=skin, outline=outline, width=2)
        draw.rounded_rectangle((39, 15 + hand_y, 49, 37 + hand_y), radius=5, fill=skin, outline=outline, width=2)
        draw.rounded_rectangle((49, 22 + hand_y, 59, 43 + hand_y), radius=5, fill=skin, outline=outline, width=2)
        draw.rounded_rectangle((60, 21 + hand_y, 70, 42 + hand_y), radius=5, fill=skin, outline=outline, width=2)
        draw.rounded_rectangle((71, 15 + hand_y, 81, 36 + hand_y), radius=5, fill=skin, outline=outline, width=2)
        frames.append(frame)

    output = io.BytesIO()
    frames[0].save(
        output, format="GIF", save_all=True, append_images=frames[1:],
        duration=90, loop=0, disposal=2
    )
    output.seek(0)
    return output


@bot.command(name="pet", aliases=["pat", "petpet"])
@commands.cooldown(1, 3, commands.BucketType.USER)
async def pet(ctx, member: discord.Member = None):
    member = member or ctx.author
    try:
        avatar_bytes = await member.display_avatar.with_size(256).with_format("png").read()
        file = discord.File(make_pet_gif(avatar_bytes), filename="pet.gif")
        embed = discord.Embed(
            description=f"🐾 **{ctx.author.display_name}** pets **{member.display_name}**!",
            color=PINK,
        )
        embed.set_image(url="attachment://pet.gif")
        await ctx.send(embed=embed, file=file)
    except (discord.HTTPException, OSError):
        await ctx.send("🐾 I couldn't create that pet GIF. Please try again.")


# ==================== KISS GIF + SQLITE COUNTERS ====================

def make_kiss_gif():
    size = 160
    frames = []
    for frame_num in range(12):
        frame = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(frame)
        draw.ellipse((35, 57, 83, 92), fill=(255, 70, 125, 255))
        draw.ellipse((77, 57, 125, 92), fill=(255, 70, 125, 255))
        draw.polygon([(35, 74), (80, 65), (125, 74), (80, 105)], fill=(255, 70, 125, 255))
        draw.arc((44, 66, 116, 96), 10, 170, fill=(180, 30, 75, 255), width=3)
        for x, start_y, speed in [(35, 52, 2), (125, 44, 3), (80, 30, 2)]:
            y = (start_y - frame_num * speed) % 145
            heart_color = (255, 90, 160, 230)
            draw.ellipse((x - 8, y - 5, x, y + 3), fill=heart_color)
            draw.ellipse((x, y - 5, x + 8, y + 3), fill=heart_color)
            draw.polygon([(x - 8, y), (x + 8, y), (x, y + 11)], fill=heart_color)
        frames.append(frame)

    output = io.BytesIO()
    frames[0].save(
        output, format="GIF", save_all=True, append_images=frames[1:],
        duration=100, loop=0, disposal=2
    )
    output.seek(0)
    return output


KISS_DB = "kiss_stats.db"


def setup_kiss_database():
    with sqlite3.connect(KISS_DB) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS kiss_stats (
                user_id INTEGER PRIMARY KEY,
                sent INTEGER NOT NULL DEFAULT 0,
                received INTEGER NOT NULL DEFAULT 0
            )
        """)


def update_kiss_stats(sender_id, receiver_id):
    with sqlite3.connect(KISS_DB) as db:
        db.execute("""
            INSERT INTO kiss_stats (user_id, sent, received) VALUES (?, 1, 0)
            ON CONFLICT(user_id) DO UPDATE SET sent = sent + 1
        """, (sender_id,))
        db.execute("""
            INSERT INTO kiss_stats (user_id, sent, received) VALUES (?, 0, 1)
            ON CONFLICT(user_id) DO UPDATE SET received = received + 1
        """, (receiver_id,))


def get_kiss_stats(user_id):
    with sqlite3.connect(KISS_DB) as db:
        row = db.execute(
            "SELECT sent, received FROM kiss_stats WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row if row else (0, 0)


setup_kiss_database()


def create_kiss_embed(sender, receiver, title="💋 Virtual Kiss!"):
    embed = discord.Embed(
        title=title,
        description=f"**{sender.display_name}** sends a kiss to **{receiver.display_name}**! 💗😘",
        color=PINK,
    )
    embed.set_footer(text="💕 A little love goes a long way!")
    return embed


class KissBackView(discord.ui.View):
    def __init__(self, sender_id, receiver_id):
        super().__init__(timeout=86400)
        self.sender_id = sender_id
        self.receiver_id = receiver_id
        self.used = False

    @discord.ui.button(label="💋 Kiss Back", style=discord.ButtonStyle.primary)
    async def kiss_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.receiver_id:
            await interaction.response.send_message(
                "💗 This button is meant for the person who received the kiss!",
                ephemeral=True,
            )
            return
        if self.used:
            await interaction.response.send_message("💋 This kiss has already been returned!", ephemeral=True)
            return

        self.used = True
        button.disabled = True
        await interaction.response.edit_message(view=self)

        sender = interaction.client.get_user(self.sender_id)
        if sender is None:
            try:
                sender = await interaction.client.fetch_user(self.sender_id)
            except discord.HTTPException:
                await interaction.followup.send("Couldn't find the original sender.", ephemeral=True)
                return

        receiver = interaction.user
        update_kiss_stats(receiver.id, sender.id)
        file = discord.File(make_kiss_gif(), filename="kiss_back.gif")
        embed = create_kiss_embed(receiver, sender, title="💞 Kiss Returned!")
        embed.set_image(url="attachment://kiss_back.gif")
        await interaction.followup.send(
            content=f"💗 {receiver.mention} kissed {sender.mention} back!",
            embed=embed,
            file=file,
        )


@bot.command(name="kiss")
@commands.cooldown(1, 3, commands.BucketType.USER)
async def kiss(ctx, member: discord.Member = None):
    if member is None:
        await ctx.send("💋 Who do you want to kiss? Try `,kiss @user`!")
        return
    if member.bot:
        await ctx.send("🤖 You can send kisses to people, not bots!")
        return
    if member.id == ctx.author.id:
        await ctx.send("💗 Sending yourself some love? Self-love counts too!")
        return

    update_kiss_stats(ctx.author.id, member.id)
    file = discord.File(make_kiss_gif(), filename="kiss.gif")
    embed = create_kiss_embed(ctx.author, member)
    embed.set_image(url="attachment://kiss.gif")
    await ctx.send(
        content=f"💌 {member.mention}, someone sent you a kiss!",
        embed=embed,
        file=file,
        view=KissBackView(sender_id=ctx.author.id, receiver_id=member.id),
    )


@bot.command(name="kisscount", aliases=["kisscounts"])
async def kiss_count(ctx, member: discord.Member = None):
    member = member or ctx.author
    sent, received = get_kiss_stats(member.id)
    embed = discord.Embed(title=f"💋 {member.display_name}'s Kiss Stats", color=PINK)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="💌 Kisses Sent", value=f"**{sent:,}**", inline=True)
    embed.add_field(name="💗 Kisses Received", value=f"**{received:,}**", inline=True)
    embed.add_field(name="💕 Total Activity", value=f"**{sent + received:,}**", inline=False)
    embed.set_footer(text="Kiss totals are saved in SQLite.")
    await ctx.send(embed=embed)


# ==================== ANTI-SPAM + INVITE FILTER + MESSAGE XP ====================

@bot.event
async def on_message(message):
    if message.author.bot or message.guild is None:
        return

    member = message.author

    # Remove AFK status when the member speaks.
    if member.id in afk_status:
        old_status = afk_status.pop(member.id)
        try:
            await message.channel.send(f"👋 Welcome back {member.mention}! Your AFK status was: **{old_status}**")
        except discord.HTTPException:
            pass

    # Tell users when they mention an AFK member.
    for mentioned in message.mentions:
        if mentioned.id in afk_status:
            try:
                await message.channel.send(
                    f"💤 {mentioned.display_name} is AFK: **{afk_status[mentioned.id]}**"
                )
            except discord.HTTPException:
                pass

    # Anti-advertising: Discord invite links -> 10-hour timeout.
    if INVITE_RE.search(message.content):
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        if member.id not in spam_timed_out:
            ok = await try_timeout(member, 10 * 60 * 60, "Discord invite link advertising")
            if ok:
                spam_timed_out.add(member.id)
                await message.channel.send(
                    f"🚫 {member.mention} was timed out for 10 hours for posting a Discord invite.",
                    delete_after=10,
                )
        return

    # Anti-spam: 5 identical messages within 10 seconds -> 10-minute timeout.
    now = time.monotonic()
    key = (message.guild.id, member.id)
    history = recent_messages[key]
    normalized = message.content.strip().casefold()
    if normalized:
        history.append((now, normalized, message.id))
        while history and now - history[0][0] > 10:
            history.popleft()
        identical_count = sum(1 for _, content, _ in history if content == normalized)
        if identical_count >= 5 and key not in spam_timed_out:
            spam_timed_out.add(key)
            ok = await try_timeout(member, 10 * 60, "Repeated identical messages")
            if ok:
                try:
                    await message.channel.send(
                        f"⏳ {member.mention} was timed out for 10 minutes for spamming.",
                        delete_after=10,
                    )
                except discord.HTTPException:
                    pass
            return

    # Award 1 XP per message; level up every 100 total XP.
    user_xp[member.id] += 1
    new_level = 1 + user_xp[member.id] // 100
    if new_level > user_levels[member.id]:
        user_levels[member.id] = new_level
        try:
            await message.channel.send(f"🎉 {member.mention} reached **Level {new_level}**!")
        except discord.HTTPException:
            pass

    await bot.process_commands(message)


# ==================== SLASH COMMANDS ====================

@bot.tree.command(name="slap", description="Playfully slap a user's profile picture")
@discord.app_commands.describe(member="The person whose profile picture gets slapped")
async def slap(interaction: discord.Interaction, member: discord.Member):
    """Creates a playful slap graphic featuring the target's avatar."""
    await interaction.response.defer(thinking=True)
    try:
        avatar_bytes = await member.display_avatar.replace(size=256, static_format="png").read()
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((240, 240))

        # Circular avatar crop
        mask = Image.new("L", (240, 240), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 239, 239), fill=255)
        avatar.putalpha(mask)

        # Pastel comic-style slap card
        canvas = Image.new("RGB", (800, 360), (255, 231, 242))
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle((12, 12, 788, 348), radius=32, fill=(255, 247, 251), outline=(255, 105, 180), width=5)
        canvas.paste(avatar, (45, 60), avatar)
        draw = ImageDraw.Draw(canvas)
        # Impact burst behind the slap text
        cx, cy = 500, 165
        points = []
        for i in range(20):
            import math
            angle = i * math.pi / 10
            radius = 100 if i % 2 == 0 else 70
            points.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
        draw.polygon(points, fill=(255, 194, 216), outline=(255, 105, 180))
        try:
            from PIL import ImageFont
            font_candidates = [
                "C:/Windows/Fonts/arialbd.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            ]
            font = None
            for font_path in font_candidates:
                try:
                    font = ImageFont.truetype(font_path, 58)
                    break
                except OSError:
                    continue
            if font is None:
                font = ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()
        draw.text((cx, cy), "SLAP!", font=font, fill=(170, 35, 95), anchor="mm", stroke_width=1, stroke_fill=(255, 255, 255))
        draw.text((330, 290), f"{member.display_name[:28]} got bonked!", fill=(130, 55, 95))

        output = io.BytesIO()
        canvas.save(output, format="PNG")
        output.seek(0)
        file = discord.File(output, filename="slap.png")
        embed = discord.Embed(
            description=f"👋 **{interaction.user.display_name}** slaps **{member.display_name}**! 💥",
            color=PINK,
        )
        embed.set_image(url="attachment://slap.png")
        await interaction.followup.send(embed=embed, file=file)
    except Exception as exc:
        print(f"/slap command error: {exc!r}")
        await interaction.followup.send(
            f"👋 **{interaction.user.mention}** playfully slaps **{member.mention}**! 💥",
            ephemeral=False,
        )


# ==================== ERROR HANDLING ====================

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use that command.")
    elif isinstance(error, commands.BotMissingPermissions):
        await ctx.send("❌ I don't have the permissions needed for that command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing an argument. Try `{PREFIX}help` for command examples.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ I couldn't find that member. Try mentioning them.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ I couldn't understand that input. Check the command format.")
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Please wait {error.retry_after:.1f} seconds before using that again.")
    else:
        # Print unexpected errors to the terminal so they can be diagnosed.
        print(f"Command error in {ctx.command}: {repr(error)}")
        await ctx.send("❌ Something went wrong while running that command.")


# ==================== READY + START ====================

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"Prefix: {PREFIX}")
    if not getattr(bot, "_slash_commands_synced", False):
        try:
            synced = await bot.tree.sync()
            bot._slash_commands_synced = True
            print(f"Synced {len(synced)} slash command(s).")
        except Exception as exc:
            print(f"Slash command sync failed: {exc!r}")
    print("Bot is ready!")


# Register persistent ticket views so ticket buttons keep working after restarts.
bot.add_view(TicketPanelView())
bot.add_view(CloseTicketView())


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError(
            "Missing DISCORD_TOKEN environment variable. Set it to your bot token before running."
        )
    bot.run(TOKEN)
