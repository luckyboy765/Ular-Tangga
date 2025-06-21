from pyrogram import Client, filters
from pyrogram import idle
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from PIL import Image, ImageDraw
from collections import defaultdict
from config import OWNER_IDS, API_ID, API_HASH, BOT_TOKEN, BACKUP_GROUP_ID

import json, random, sqlite3, asyncio
import redis
import os
import shutil
from datetime import datetime, timedelta
import pytz

redis_client = redis.from_url("redis://default:cZ9PulRT47AsT9ZfYopsKgyLEREhoLiR@redis-14399.crce185.ap-seast-1-1.ec2.redns.redis-cloud.com:14399", decode_responses=True)
bot = Client("snakeludo_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

def fix_game_data_types(game):
    """Perbaiki tipe data yang tidak konsisten"""
    # Convert string keys ke integer untuk player_positions dan player_colors
    if game.get("player_positions"):
        game["player_positions"] = {
            int(k) if isinstance(k, str) else k: v 
            for k, v in game["player_positions"].items()
        }
    
    if game.get("player_colors"):
        game["player_colors"] = {
            int(k) if isinstance(k, str) else k: v 
            for k, v in game["player_colors"].items()
        }
    
    # Ensure game_turn_order contains integers
    if game.get("game_turn_order"):
        game["game_turn_order"] = [
            int(uid) if isinstance(uid, str) else uid 
            for uid in game["game_turn_order"]
        ]
    
    return game

def get_game_state(chat_id):
    data = redis_client.get(f"game:{chat_id}")
    if data:
        game = json.loads(data)
        return fix_game_data_types(game)
    return None

def set_game_state(chat_id, state):
    redis_client.set(f"game:{chat_id}", json.dumps(state))

def reset_game_state(chat_id):
    redis_client.delete(f"game:{chat_id}")

games = defaultdict(lambda: {
    "player_positions": {},
    "player_colors": {},
    "game_turn_order": [],
    "winners": [],
    "current_turn_index": 0,
    "paused_for_challenge": False,
    "last_message_id": None,
    "game_created": False
})

STATIC_SNAKES = {16: 3, 29: 15, 32:19, 34:28}
STATIC_LADDERS = {6:17, 11:24, 14:27, 23:26}
STATIC_TRUTH_POSITIONS = [3, 17, 25, 33]
STATIC_DARE_POSITIONS = [4, 12, 20, 35]
AVAILABLE_COLORS = ["red", "blue", "green", "yellow"]

async def set_commands():
    commands = [
        BotCommand("new", "Buat game baru"),
        BotCommand("join", "Gabung ke game"),
        BotCommand("start", "Mulai game"),
        BotCommand("roll", "Lempar dadu"),
        BotCommand("reset", "Reset game"),
        BotCommand("kick", "Kick pemain"),
        BotCommand("continue", "Lanjutkan giliran setelah tantangan"),
        BotCommand("gamesettings", "Lihat pengaturan game"),
        BotCommand("addtruth", "Tambah pertanyaan truth"),
        BotCommand("removetruth", "Hapus pertanyaan truth"),
        BotCommand("listtruth", "Lihat daftar truth"),
        BotCommand("adddare", "Tambah tantangan dare"),
        BotCommand("removedare", "Hapus tantangan dare"),
        BotCommand("listdare", "Lihat daftar dare"),
        BotCommand("addadmin", "Tambah admin"),
        BotCommand("help", "Lihat bantuan")
    ]
    await bot.set_bot_commands(commands)

def get_or_create_game(chat_id):
    game = get_game_state(chat_id)
    if not game:
        game = {
            "player_positions": {},
            "player_colors": {},
            "game_turn_order": [],
            "winners": [],
            "current_turn_index": 0,
            "paused_for_challenge": False,
            "last_message_id": None,
            "game_created": False,
            "available_colors": AVAILABLE_COLORS.copy()
        }
        set_game_state(chat_id, game)
    return game

conn = sqlite3.connect("score.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS scores (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    wins INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS truth_dare_global (
    type TEXT CHECK(type IN ('truth', 'dare')),
    prompt TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS game_settings (
    chat_id INTEGER PRIMARY KEY,
    snakes TEXT DEFAULT '{}',
    ladders TEXT DEFAULT '{}',
    truth_positions TEXT DEFAULT '[]',
    dare_positions TEXT DEFAULT '[]',
    truth_prompts TEXT DEFAULT '[]',
    dare_prompts TEXT DEFAULT '[]',
    admins TEXT DEFAULT '[]'
)
""")

conn.commit()

DEFAULT_SNAKES = {}
DEFAULT_LADDERS = {}
DEFAULT_TRUTH_POSITIONS = []
DEFAULT_DARE_POSITIONS = []
DEFAULT_TRUTH_PROMPTS = []
DEFAULT_DARE_PROMPTS = []


COLOR_EMOJIS = {
    "red": "🔴",
    "blue": "🔵",
    "green": "🟢",
    "yellow": "🟡"
}

BOARD_IMAGE_PATH = "boards.png"
GRID_SIZE = 6

def get_global_prompts():
    cursor.execute("SELECT type, prompt FROM truth_dare_global")
    rows = cursor.fetchall()
    truths = [r[1] for r in rows if r[0] == 'truth']
    dares = [r[1] for r in rows if r[0] == 'dare']
    return truths, dares


def get_chat_settings(chat_id):
    cursor.execute("SELECT * FROM game_settings WHERE chat_id = ?", (chat_id,))
    row = cursor.fetchone()

    if not row:
        cursor.execute("""
            INSERT INTO game_settings (chat_id, snakes, ladders, truth_positions, dare_positions, admins)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            chat_id,
            json.dumps(STATIC_SNAKES),  # Gunakan STATIC_SNAKES
            json.dumps(STATIC_LADDERS), # Gunakan STATIC_LADDERS
            json.dumps(STATIC_TRUTH_POSITIONS),
            json.dumps(STATIC_DARE_POSITIONS),
            json.dumps([]),
        ))
        conn.commit()
        cursor.execute("SELECT * FROM game_settings WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()

    truths, dares = get_global_prompts()
    
    # Parse data dari database, jika kosong gunakan static
    saved_snakes = json.loads(row[1]) if row[1] and row[1] != '{}' else STATIC_SNAKES
    saved_ladders = json.loads(row[2]) if row[2] and row[2] != '{}' else STATIC_LADDERS
    
    return {
        'snakes': saved_snakes,
        'ladders': saved_ladders,
        'truth_positions': STATIC_TRUTH_POSITIONS,
        'dare_positions': STATIC_DARE_POSITIONS,
        'truth_prompts': truths,
        'dare_prompts': dares,
        'admins': OWNER_IDS,
    }

def update_chat_settings(chat_id, key, value):
    cursor.execute(f"UPDATE game_settings SET {key} = ? WHERE chat_id = ?", (json.dumps(value), chat_id))
    conn.commit()

def is_admin(chat_id, user_id):
    return user_id in OWNER_IDS

def pos_to_xy_grid(pos, width, height):
    square_w = width // GRID_SIZE
    square_h = height // GRID_SIZE
    row = (pos - 1) // GRID_SIZE
    col = (pos - 1) % GRID_SIZE
    if row % 2 == 1:
        col = GRID_SIZE - 1 - col
    x = col * square_w
    y = (GRID_SIZE - 1 - row) * square_h
    return x, y, square_w, square_h

def generate_board_image(player_positions, player_colors):
    board = Image.open(BOARD_IMAGE_PATH).convert("RGBA")
    width, height = board.size

    for user_id, pos in player_positions.items():
        color = player_colors.get(user_id)
        x, y, w, h = pos_to_xy_grid(pos, width, height)
        pion = Image.new("RGBA", board.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(pion)
        same_pos = [uid for uid, p in player_positions.items() if p == pos]
        offset_idx = same_pos.index(user_id)
        cx = x + w // 2 + offset_idx * (w // 6)
        cy = y + int(h * 0.70) + offset_idx * (h // 10)
        radius = min(w, h) // 5
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=color)
        board = Image.alpha_composite(board, pion)

    out_path = "current_board.png"
    board.save(out_path)
    return out_path


@bot.on_message(filters.command("setdarepos"))
async def set_dare_positions(_, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not is_admin(chat_id, user_id):
        await message.reply("❌ Hanya admin yang bisa mengatur posisi dare.")
        return
    try:
        args = message.text.split()[1:]
        if not args:
            settings = get_chat_settings(chat_id)
            pos_text = ", ".join(map(str, settings['dare_positions']))
            await message.reply(f"🎯 **Posisi Dare Saat Ini:**\n{pos_text}\n\n**Format:** `/setdarepos 4 12 20 35`")
            return
        new_positions = [int(arg) for arg in args if 1 <= int(arg) <= 36]
        update_chat_settings(chat_id, 'dare_positions', new_positions)
        pos_text = ", ".join(map(str, new_positions))
        await message.reply(f"✅ **Posisi Dare berhasil diatur:**\n{pos_text}")
    except:
        await message.reply("❌ Format salah. Gunakan: `/setdarepos 4 12 20 35`")

@bot.on_message(filters.command("new"))
async def new_game(_, message: Message):
    chat_id = message.chat.id
    game = get_or_create_game(chat_id)

    if game["game_created"]:
        await message.reply("🎮 Game sudah dibuat. Gunakan tombol untuk bergabung.")
        return

    game["game_created"] = True
    set_game_state(chat_id, game)
    game["player_positions"].clear()
    game["player_colors"].clear()
    game["game_turn_order"].clear()
    game["winners"].clear()
    game["paused_for_challenge"] = False
    set_game_state(chat_id, game)
    game["last_message_id"] = None
    game["current_turn_index"] = 0
    game["available_colors"] = AVAILABLE_COLORS.copy()

    text = """**Ruang bermain berhasil dibuat.**
Gunakan tombol Gabung atau ketik /join untuk bergabung ke dalam permainan dan klik tombol start atau ketik /start untuk memulai permainan.

**Catatan:**
(1) Bila bidak menumpuk atau menabrak bidak lain di angka yang sama makan akan mati dan memulai dari awal.
(2) Bila terkena tantangan TRUTH reply pertanyaan yang diberikan dengan jawaban yang sesuai.
(3) Bila terkena hukuman DARE selesaikan hukuman yang diberikan dan Reply hukuman dengan DONE/SELESAI.
(4) Jumlah minimal pemain untuk memulai adalah 2 orang dan jumlah Maksimal pemain yang dapat bergabung dalam ruang bermain adalah 4 orang."""

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Gabung permainan", callback_data="join")],
        [InlineKeyboardButton("Mulai permainan", callback_data="start")],
        [InlineKeyboardButton("🗑 Hapus", callback_data="delete_room")]
    ])

    await message.reply(text, reply_markup=keyboard)

@bot.on_message(filters.command("join"))
async def join_game(_, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    # Gunakan lock yang sama dengan callback
    join_key = f"joining:{chat_id}:{user_id}"
    
    if not redis_client.set(join_key, "1", nx=True, ex=5):
        await message.reply("⏳ Sedang proses join, tunggu sebentar...")
        return

    try:
        game = get_game_state(chat_id)
        if not game:
            game = get_or_create_game(chat_id)

        if not game.get("game_created", False):
            await message.reply("❌ Belum ada game yang dibuat. Gunakan /new dulu.")
            return

        # Konsistensi tipe data - gunakan integer
        if user_id in game["player_positions"]:
            await message.reply("Kamu sudah ikut bermain.")
            return

        if len(game["player_colors"]) >= 4:
            await message.reply("⚠️ Pemain penuh (maks 4).")
            return

        if not game.get("available_colors"):
            game["available_colors"] = AVAILABLE_COLORS.copy()

        color = game["available_colors"].pop(0)
        game["player_positions"][user_id] = 1  # Integer
        game["player_colors"][user_id] = color  # Integer
        game["game_turn_order"].append(user_id)
        
        set_game_state(chat_id, game)
        await message.reply(f"✅ Kamu bergabung sebagai pion **{color}**!")
        
    except Exception as e:
        await message.reply("❌ Terjadi error saat join. Coba lagi.")
        print(f"Error in join command: {e}")
    finally:
        redis_client.delete(join_key)

@bot.on_message(filters.command("start"))
async def start_game(_, message: Message):
    chat_id = message.chat.id
    game = get_or_create_game(chat_id)

    if not game["game_created"]:
        await message.reply("❌ Belum ada game. Gunakan /new.")
        return

    if len(game["player_positions"]) < 2:
        await message.reply("⏳ Minimal 2 pemain untuk memulai.")
        return

    path = generate_board_image(game["player_positions"], game["player_colors"])
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🎲 Lempar Dadu", callback_data="roll")]])

    first_uid = game["game_turn_order"][0]
    first_user = await bot.get_users(first_uid)
    first_color = game["player_colors"].get(first_uid, "???")
    first_emoji = COLOR_EMOJIS.get(first_color, first_color)
    first_mention = f"[{first_user.first_name}](tg://user?id={first_uid})"

    caption = (
        f"🎮 Game dimulai! Gunakan /roll atau tekan tombol di bawah.\n"
        f"🎯 Sekarang giliran {first_mention} dengan pion {first_emoji}"
    )

    await message.reply_photo(photo=path, caption=caption, reply_markup=keyboard)

@bot.on_message(filters.command("addtruth"))
async def add_truth_prompt(_, message: Message):
    if message.from_user.id not in OWNER_IDS:
        return await message.reply("❌ Hanya owner yang bisa menambah truth.")
    text = message.text[len("/addtruth"):].strip()
    if not text:
        return await message.reply("❌ Format: `/addtruth Truth: ...`")
    cursor.execute("INSERT INTO truth_dare_global (type, prompt) VALUES (?, ?)", ('truth', text))
    conn.commit()
    await message.reply(f"✅ Truth ditambahkan: {text}")

@bot.on_message(filters.command("listtruth"))
async def list_truth(_, message: Message):
    truths, _ = get_global_prompts()
    if not truths:
        return await message.reply("❌ Belum ada truth.")
    text = "\n".join([f"{i+1}. {t}" for i, t in enumerate(truths)])
    await message.reply(f"🧠 **Daftar Truth:**\n{text}")

@bot.on_message(filters.command("removetruth"))
async def remove_truth(_, message: Message):
    if message.from_user.id not in OWNER_IDS:
        return await message.reply("❌ Hanya owner yang bisa menghapus truth.")
    try:
        index = int(message.text.split()[1]) - 1
        truths, _ = get_global_prompts()
        if 0 <= index < len(truths):
            removed = truths[index]
            cursor.execute("DELETE FROM truth_dare_global WHERE type = ? AND prompt = ? LIMIT 1", ('truth', removed))
            conn.commit()
            await message.reply(f"✅ Truth dihapus: {removed}")
        else:
            await message.reply("❌ Nomor tidak valid.")
    except:
        await message.reply("❌ Format: `/removetruth 1`")

@bot.on_message(filters.command("adddare"))
async def add_dare_prompt(_, message: Message):
    if message.from_user.id not in OWNER_IDS:
        return await message.reply("❌ Hanya owner yang bisa menambah dare.")
    text = message.text[len("/adddare"):].strip()
    if not text:
        return await message.reply("❌ Format: `/adddare Dare: ...`")
    cursor.execute("INSERT INTO truth_dare_global (type, prompt) VALUES (?, ?)", ('dare', text))
    conn.commit()
    await message.reply(f"✅ Dare ditambahkan: {text}")

@bot.on_message(filters.command("listdare"))
async def list_dare(_, message: Message):
    _, dares = get_global_prompts()
    if not dares:
        return await message.reply("❌ Belum ada dare.")
    text = "\n".join([f"{i+1}. {d}" for i, d in enumerate(dares)])
    await message.reply(f"🎯 **Daftar Dare:**\n{text}")

@bot.on_message(filters.command("removedare"))
async def remove_dare(_, message: Message):
    if message.from_user.id not in OWNER_IDS:
        return await message.reply("❌ Hanya owner yang bisa menghapus dare.")
    try:
        index = int(message.text.split()[1]) - 1
        _, dares = get_global_prompts()
        if 0 <= index < len(dares):
            removed = dares[index]
            cursor.execute("DELETE FROM truth_dare_global WHERE type = ? AND prompt = ? LIMIT 1", ('dare', removed))
            conn.commit()
            await message.reply(f"✅ Dare dihapus: {removed}")
        else:
            await message.reply("❌ Nomor tidak valid.")
    except:
        await message.reply("❌ Format: `/removedare 1`")


async def roll_dice_for_user(user_id: int, chat_id: int, first_name: str, username: str, callback_query=None):
    roll_key = f"rolllock:{chat_id}:{user_id}"
    if not redis_client.set(roll_key, "1", nx=True, ex=5):
        msg = "⏳ Sedang memproses giliranmu, tunggu sebentar..."
        if callback_query:
            await callback_query.answer(msg, show_alert=True)
        else:
            await bot.send_message(chat_id, msg)
        return

    keyboard = None  
    game = get_or_create_game(chat_id)

    if game["paused_for_challenge"]:
        await bot.send_message(chat_id, "⏸️ Game sedang ditunda karena tantangan sedang berlangsung. Gunakan /continue jika sudah selesai.")
        return

    if user_id not in game["player_positions"] or user_id not in game["game_turn_order"]:
        msg = "❌ Kamu bukan bagian dari game ini."
        if callback_query:
            await callback_query.answer(msg, show_alert=True)
        else:
            await bot.send_message(chat_id, msg)
        return

    if len(game["player_positions"]) < 2:
        await bot.send_message(chat_id, "⏳ Minimal 2 pemain untuk memulai.")
        return

    if game["game_turn_order"][game["current_turn_index"]] != user_id:
        msg = "⏳ Bukan giliranmu."
        if callback_query:
            await callback_query.answer(msg, show_alert=True)
        else:
            await bot.send_message(chat_id, msg)
        return

    dice_msg = await bot.send_dice(chat_id, emoji="🎲")
    roll = dice_msg.dice.value
    await asyncio.sleep(4)
    await dice_msg.delete()

    current = game["player_positions"][user_id]
    new_pos = current + roll if current + roll <= 36 else current

    text = f"🎲 {first_name} mendapat angka: {roll}\n📍 Posisi: {current} ➡️ {new_pos}\n"

    settings = get_chat_settings(chat_id)
    # PERBAIKAN: Pastikan konversi ke integer konsisten
    snakes = {int(k): int(v) for k, v in settings['snakes'].items()}
    ladders = {int(k): int(v) for k, v in settings['ladders'].items()}

    if new_pos in snakes:
        target = snakes[new_pos]
        text += f"🐍 Kamu terkena ular! Turun dari {new_pos} ke {target}.\n"
        new_pos = target
    elif new_pos in ladders:
        target = ladders[new_pos]
        text += f"🪜 Kamu naik tangga! Naik dari {new_pos} ke {target}.\n"
        new_pos = target

    # Kick pemain lain yang di posisi sama
    kicked = None
    for uid, pos in game["player_positions"].items():
        if uid != user_id and pos == new_pos:
            game["player_positions"][uid] = 1
            kicked = uid

    game["player_positions"][user_id] = new_pos

    if new_pos == current and roll > 0:
        text += "❗ Kamu butuh angka yang tepat untuk mencapai kotak 36.\n"
    if kicked:
        text += f"💥 Pemain lain ditendang ke kotak 1!\n"

    challenge_msg = None
    
    # Cek truth/dare challenge
    if new_pos in settings['truth_positions'] and settings['truth_prompts']:
        game["paused_for_challenge"] = True
        challenge = random.choice(settings['truth_prompts'])
        challenge_msg = f"🧠 **Truth Challenge untuk {first_name}:**\n{challenge}"
    elif new_pos in settings['dare_positions'] and settings['dare_prompts']:
        game["paused_for_challenge"] = True
        challenge = random.choice(settings['dare_prompts'])
        challenge_msg = f"🎯 **Dare Challenge untuk {first_name}:**\n{challenge}"
    
    # Cek apakah pemain menang (mencapai kotak 36)
    elif new_pos == 36:
        if user_id not in game["winners"]:
            game["winners"].append(user_id)

        text += f"🏅 {first_name} mencapai kotak 36! Urutan saat ini:\n"
        for i, uid in enumerate(game["winners"], 1):
            user_obj = await bot.get_users(uid)
            text += f"{i}. {user_obj.first_name}\n"

        # Hapus pemain yang menang dari game
        game["game_turn_order"].remove(user_id)
        game["player_positions"].pop(user_id, None)
        color = game["player_colors"].pop(user_id, None)
        if color:
            game["available_colors"].append(color)

        # Update skor
        cursor.execute("INSERT OR IGNORE INTO scores (user_id, username, wins) VALUES (?, ?, 0)", (user_id, username))
        cursor.execute("UPDATE scores SET wins = wins + 1 WHERE user_id = ?", (user_id,))
        conn.commit()

        # Cek apakah game selesai
        if len(game["game_turn_order"]) <= 1:
            if len(game["game_turn_order"]) == 1:
                last_uid = game["game_turn_order"][0]
                if last_uid not in game["winners"]:
                    game["winners"].append(last_uid)
                    last_user = await bot.get_users(last_uid)
                    cursor.execute("INSERT OR IGNORE INTO scores (user_id, username, wins) VALUES (?, ?, 0)", (last_uid, last_user.first_name))
                    cursor.execute("UPDATE scores SET wins = wins + 1 WHERE user_id = ?", (last_uid,))
                    conn.commit()

            text += "\n🏁 Game selesai! Urutan pemenang:\n"
            for i, uid in enumerate(game["winners"], 1):
                name = (await bot.get_users(uid)).first_name
                text += f"{i}. {name}\n"

            reset_game_state(chat_id)
            keyboard = None
        else:
            # PERBAIKAN: Adjust current_turn_index setelah pemain dihapus
            if game["current_turn_index"] >= len(game["game_turn_order"]):
                game["current_turn_index"] = 0
            
            next_uid = game["game_turn_order"][game["current_turn_index"]]
            user_obj = await bot.get_users(next_uid)
            color = game["player_colors"].get(next_uid, "???")
            emoji = COLOR_EMOJIS.get(color, color)
            mention = f"[{user_obj.first_name}](tg://user?id={next_uid})"
            text += f"\n🎯 Giliran selanjutnya: {mention} dengan pion {emoji}"
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🎲 Lempar Dadu", callback_data="roll")]])
    
    # PERBAIKAN UTAMA: Pergantian giliran normal
    else:
        # Jika tidak ada challenge dan tidak menang, ganti giliran
        if not game["paused_for_challenge"]:
            game["current_turn_index"] = (game["current_turn_index"] + 1) % len(game["game_turn_order"])
            
        next_uid = game["game_turn_order"][game["current_turn_index"]]
        user_obj = await bot.get_users(next_uid)
        color = game["player_colors"].get(next_uid, "???")
        emoji = COLOR_EMOJIS.get(color, color)
        mention = f"[{user_obj.first_name}](tg://user?id={next_uid})"
        text += f"🎯 Giliran selanjutnya: {mention} dengan pion {emoji}"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🎲 Lempar Dadu", callback_data="roll")]])

    # PERBAIKAN: Simpan state game setelah semua perubahan
    redis_client.delete(roll_key)
    set_game_state(chat_id, game)

    path = generate_board_image(game["player_positions"], game["player_colors"])

    if game.get("last_message_id"):
        try:
            await bot.delete_messages(chat_id, game["last_message_id"])
        except:
            pass

    sent = await bot.send_photo(chat_id, photo=path, caption=text, reply_markup=keyboard)
    game["last_message_id"] = sent.id
    
    # PERBAIKAN: Simpan lagi setelah update message_id
    set_game_state(chat_id, game)

    if challenge_msg:
        msg = await bot.send_message(chat_id, challenge_msg)
        game["challenge_message_id"] = msg.id
        set_game_state(chat_id, game)

# Command /roll
@bot.on_message(filters.command("roll"))
async def roll_dice(_, message: Message):
    user = message.from_user
    await roll_dice_for_user(user.id, message.chat.id, user.first_name, user.username or str(user.id))

@bot.on_callback_query()
async def callback_query_handler(_, callback):
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    
    if callback.data == "join":
        # PERBAIKAN 1: Gunakan lock yang lebih ketat
        join_key = f"joining:{chat_id}:{user_id}"
        
        # PERBAIKAN 2: Gunakan pipeline Redis untuk atomic operation
        pipe = redis_client.pipeline()
        
        try:
            # Set lock dengan waktu lebih lama dan cek apakah berhasil
            if not redis_client.set(join_key, "1", nx=True, ex=5):
                await callback.answer("⏳ Sedang proses join, tunggu sebentar...", show_alert=True)
                return
            
            # PERBAIKAN 3: Ambil dan update game state secara atomic
            game = get_game_state(chat_id)
            if not game:
                game = get_or_create_game(chat_id)

            if not game.get("game_created", False):
                await callback.answer("❌ Game belum dibuat. Gunakan /new dulu.", show_alert=True)
                redis_client.delete(join_key)
                return

            # PERBAIKAN 4: Konsistensi tipe data - gunakan integer
            if user_id in game["player_positions"]:
                await callback.answer("Kamu sudah ikut bermain.", show_alert=True)
                redis_client.delete(join_key)
                return

            if len(game["player_colors"]) >= 4:
                await callback.answer("⚠️ Pemain penuh (maks 4).", show_alert=True)
                redis_client.delete(join_key)
                return

            # PERBAIKAN 5: Pastikan available_colors ada
            if not game.get("available_colors"):
                game["available_colors"] = AVAILABLE_COLORS.copy()

            if not game["available_colors"]:
                await callback.answer("❌ Tidak ada warna tersedia.", show_alert=True)
                redis_client.delete(join_key)
                return

            # PERBAIKAN 6: Gunakan integer konsisten untuk user_id
            color = game["available_colors"].pop(0)
            game["player_positions"][user_id] = 1  # Integer, bukan string
            game["player_colors"][user_id] = color  # Integer, bukan string
            game["game_turn_order"].append(user_id)  # Integer

            # PERBAIKAN 7: Update state dan hapus lock dalam satu operasi
            set_game_state(chat_id, game)
            redis_client.delete(join_key)

            await callback.answer(f"✅ Kamu bergabung sebagai pion {color}!")
            
            # Kirim konfirmasi ke grup
            emoji = COLOR_EMOJIS.get(color, color)
            await callback.message.reply(f"👤 {callback.from_user.first_name} bergabung sebagai pion {emoji}!")
            
        except Exception as e:
            # PERBAIKAN 8: Error handling yang lebih baik
            redis_client.delete(join_key)
            await callback.answer("❌ Terjadi error saat join. Coba lagi.", show_alert=True)
            print(f"Error in join callback: {e}")

    
    elif callback.data == "roll":
        user = callback.from_user
        await roll_dice_for_user(user.id, chat_id, user.first_name, user.username or str(user.id), callback)


    elif callback.data == "start":
        # FIX: Definisikan game variable di awal
        game = get_or_create_game(chat_id)
        
        if not game["game_created"]:
            await callback.answer("❌ Belum ada game.", show_alert=True)
            return

        if len(game["player_positions"]) < 2:
            await callback.answer("⏳ Minimal 2 pemain untuk memulai.", show_alert=True)
            return

        path = generate_board_image(game["player_positions"], game["player_colors"])
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🎲 Lempar Dadu", callback_data="roll")]])

        player_lines = [
            f"• {(await bot.get_users(uid)).first_name} dengan pion {COLOR_EMOJIS.get(game['player_colors'].get(uid), '❓')}"
            for uid in game["game_turn_order"]
        ]
        players_text = "\n".join(player_lines)
        random.shuffle(game["game_turn_order"])  # Acak urutan giliran
        set_game_state(chat_id, game)
        first_uid = game["game_turn_order"][0]
        first_user = await bot.get_users(first_uid)
        first_color = game["player_colors"].get(first_uid, "???")
        first_emoji = COLOR_EMOJIS.get(first_color, first_color)
        first_mention = f"[{first_user.first_name}](tg://user?id={first_uid})"

        caption = (
            f"🎮 Game dimulai! Gunakan /roll atau tekan tombol di bawah.\n"
            f"🎯 Sekarang giliran {first_mention} dengan pion {first_emoji}"
        )

        sent = await callback.message.reply_photo(photo=path, caption=caption, reply_markup=keyboard)
        game["last_message_id"] = sent.id
        set_game_state(chat_id, game)  # Save the updated game state
        await callback.answer()
        
    elif callback.data == "delete_room":
        # FIX: Definisikan game variable di awal  
        game = get_or_create_game(chat_id)
        reset_game_state(chat_id)
        await callback.message.delete()
        await callback.answer("🗑️ Ruang permainan telah dihapus!")

@bot.on_message(filters.command("reset"))
async def reset_game(_, message: Message):
    chat_id = message.chat.id
    reset_game_state(chat_id)
    await message.reply("🔄 Game direset!")

@bot.on_message(filters.command("kick"))
async def kick_player(_, message: Message):
    chat_id = message.chat.id
    game = get_or_create_game(chat_id)

    if not message.reply_to_message:
        await message.reply("❌ Reply ke pesan pemain yang ingin di-kick.")
        return

    target_id = message.reply_to_message.from_user.id

    if target_id not in game["player_positions"]:
        await message.reply("❌ User tersebut tidak sedang bermain.")
        return

    color = game["player_colors"].pop(target_id, None)
    game["player_positions"].pop(target_id, None)
    set_game_state(chat_id, game)
    if target_id in game["game_turn_order"]:
        was_turn = game["game_turn_order"][game["current_turn_index"]] == target_id
        game["game_turn_order"].remove(target_id)
        if was_turn:
            game["current_turn_index"] %= max(len(game["game_turn_order"]), 1)

    if color:
        game["available_colors"].append(color)

    await message.reply(f"👢 Pemain {message.reply_to_message.from_user.first_name} telah di-kick dari game.")

    if len(game["game_turn_order"]) == 1:
        winner_id = game["game_turn_order"][0]
        winner_user = await bot.get_users(winner_id)
        winner_name = winner_user.first_name

        cursor.execute("INSERT OR IGNORE INTO scores (user_id, username, wins) VALUES (?, ?, 0)", (winner_id, winner_name))
        cursor.execute("UPDATE scores SET wins = wins + 1 WHERE user_id = ?", (winner_id,))
        conn.commit()

        reset_game_state(chat_id)
        await message.reply(f"🏆 Game selesai otomatis! Pemenangnya adalah **{winner_name}**.")

@bot.on_message(filters.command("addadmin") & filters.user(OWNER_IDS))
async def add_admin(_, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    settings = get_chat_settings(chat_id)
    if not settings['admins'] or user_id in settings['admins']:
        if message.reply_to_message:
            new_admin_id = message.reply_to_message.from_user.id
            if new_admin_id not in settings['admins']:
                settings['admins'].append(new_admin_id)
                update_chat_settings(chat_id, 'admins', settings['admins'])
                await message.reply(f"✅ Admin ditambahkan: {message.reply_to_message.from_user.first_name}")
            else:
                await message.reply("❌ User tersebut sudah admin.")
        else:
            await message.reply("❌ Gunakan reply ke pesan user yang ingin dijadikan admin.")
    else:
        await message.reply("❌ Hanya admin yang bisa menambah admin baru.")

async def check_if_game_over(chat_id):
    if chat_id not in games:
        return False

    game = get_or_create_game(chat_id)

    if len(game["game_turn_order"]) == 1:
        game["paused_for_challenge"] = False
        set_game_state(chat_id, game)

        winner_id = game["game_turn_order"][0]
        winner_user = await bot.get_users(winner_id)
        winner_name = winner_user.first_name

        cursor.execute("INSERT OR IGNORE INTO scores (user_id, username, wins) VALUES (?, ?, 0)", (winner_id, winner_name))
        cursor.execute("UPDATE scores SET wins = wins + 1 WHERE user_id = ?", (winner_id,))
        conn.commit()

        await bot.send_message(chat_id, f"🏆 Game selesai otomatis! Pemenangnya adalah **{winner_name}**.")
        reset_game_state(chat_id)
        return True

    return False


# Tambahkan /gamesettings
@bot.on_message(filters.command("gamesettings"))
async def show_game_settings(_, message: Message):
    chat_id = message.chat.id
    settings = get_chat_settings(chat_id)

    snake_text = "\n".join([f"  {k} → {v}" for k, v in settings['snakes'].items()]) or "  Tidak ada"
    ladder_text = "\n".join([f"  {k} → {v}" for k, v in settings['ladders'].items()]) or "  Tidak ada"
    truth_pos = ", ".join(map(str, settings['truth_positions'])) or "Tidak ada"
    dare_pos = ", ".join(map(str, settings['dare_positions'])) or "Tidak ada"
    truth_count = len(settings['truth_prompts'])
    dare_count = len(settings['dare_prompts'])
    admin_count = len(settings['admins'])

    settings_text = f"""🎮 **Pengaturan Game**

🐍 **Ular:**
{snake_text}

🪜 **Tangga:**
{ladder_text}

🧠 **Posisi Truth:** {truth_pos}
🎯 **Posisi Dare:** {dare_pos}
📝 **Jumlah Truth:** {truth_count}
📝 **Jumlah Dare:** {dare_count}
👑 **Jumlah Admin:** {admin_count}
"""
    await message.reply(settings_text)

@bot.on_message(filters.command("help"))
async def show_help(_, message: Message):
    help_text = """🎮 **Snake Ludo Bot Commands**

**Game Commands:**
• /create - Buat game baru
• /join - Bergabung ke game
• /start - Mulai game (min 2 pemain)
• /roll - Lempar dadu
• /reset - Reset game
• /kick - Kick user dari game
• /continue - Lanjutkan setelah truth/dare

**Admin Commands:**
• /addadmin - Tambah admin (reply ke user)
• /gamesettings - Lihat pengaturan game
• /setsnakes 27:11 32:19 - Atur posisi ular
• /setladders 8:21 24:35 - Atur posisi tangga
• /settruthpos 3 17 25 33 - Atur posisi truth
• /setdarepos 4 12 20 35 - Atur posisi dare
• /addtruth Truth: Siapa yang kamu suka? - Tambah pertanyaan truth
• /adddare Dare: Kirim voice note! - Tambah dare
• /listtruth /listdare - Lihat daftar pertanyaan
• /removetruth /removedare - Hapus pertanyaan"""
    await message.reply(help_text)

@bot.on_message(filters.reply)
async def handle_challenge_reply(_, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    game = get_or_create_game(chat_id)

    # Jika game tidak sedang di-pause untuk challenge, abaikan
    if not game.get("paused_for_challenge"):
        return

    # Pastikan user adalah pemain aktif dan memang menjawab challenge
    if game["game_turn_order"][game["current_turn_index"]] != user_id:
        return

    # Pastikan ini reply ke pesan tantangan
    if message.reply_to_message and message.reply_to_message.id == game.get("challenge_message_id"):
        game["paused_for_challenge"] = False
        game["challenge_message_id"] = None

        # PERBAIKAN: Lanjutkan giliran ke pemain berikutnya
        game["current_turn_index"] = (game["current_turn_index"] + 1) % len(game["game_turn_order"])
        
        # PERBAIKAN: Simpan state setelah pergantian giliran
        set_game_state(chat_id, game)
        
        next_uid = game["game_turn_order"][game["current_turn_index"]]
        user_obj = await bot.get_users(next_uid)
        color = game["player_colors"].get(next_uid, "???")
        emoji = COLOR_EMOJIS.get(color, color)
        mention = f"[{user_obj.first_name}](tg://user?id={next_uid})"

        text = f"✅ Jawaban diterima!\n🎯 Giliran selanjutnya: {mention} dengan pion {emoji}"
        path = generate_board_image(game["player_positions"], game["player_colors"])
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🎲 Lempar Dadu", callback_data="roll")]])

        if game.get("last_message_id"):
            try:
                await bot.delete_messages(chat_id, game["last_message_id"])
            except:
                pass

        sent = await bot.send_photo(chat_id, photo=path, caption=text, reply_markup=keyboard)
        game["last_message_id"] = sent.id
        
        # PERBAIKAN: Simpan state terakhir
        set_game_state(chat_id, game)


async def backup_database():
    """Backup database SQLite setiap jam 00:00 WIB dan kirim ke group"""
    global conn, cursor
    
    try:
        # Tutup koneksi database sementara untuk backup
        conn.close()
        
        # Buat folder backup sementara
        temp_backup_dir = "temp_backup"
        if not os.path.exists(temp_backup_dir):
            os.makedirs(temp_backup_dir)
        
        # Format nama file backup dengan timestamp
        wib_tz = pytz.timezone('Asia/Jakarta')
        current_time = datetime.now(wib_tz)
        backup_filename = f"score_backup_{current_time.strftime('%Y%m%d_%H%M%S')}.db"
        backup_path = os.path.join(temp_backup_dir, backup_filename)
        
        # Copy database file
        shutil.copy2("score.db", backup_path)
        
        # Buka kembali koneksi database
        conn = sqlite3.connect("score.db", check_same_thread=False)
        cursor = conn.cursor()
        
        # Kirim file backup ke group
        file_size = os.path.getsize(backup_path)
        size_kb = file_size / 1024
        
        caption = f"""🗄️ **Daily Database Backup**
        
📅 **Tanggal:** {current_time.strftime('%d/%m/%Y')}
⏰ **Waktu:** {current_time.strftime('%H:%M:%S')} WIB
📊 **Ukuran:** {size_kb:.2f} KB
📁 **File:** `{backup_filename}`

✅ Backup berhasil dibuat dan disimpan otomatis."""

        await bot.send_document(
            chat_id=BACKUP_GROUP_ID,
            document=backup_path,
            caption=caption
        )
        
        print(f"✅ Database backup berhasil dan dikirim ke group: {backup_filename}")
        
        # Hapus file backup sementara
        os.remove(backup_path)
        
        # Kirim notifikasi ke owner juga (opsional)
        for owner_id in OWNER_IDS:
            try:
                await bot.send_message(
                    owner_id, 
                    f"🗄️ **Database Backup**\n"
                    f"✅ Backup berhasil dibuat dan dikirim ke group log\n"
                    f"📁 File: `{backup_filename}`\n"
                    f"⏰ Waktu: {current_time.strftime('%d/%m/%Y %H:%M:%S')} WIB"
                )
            except Exception as e:
                print(f"Gagal kirim notifikasi backup ke {owner_id}: {e}")
                
    except Exception as e:
        print(f"❌ Error saat backup database: {e}")
        
        # Kirim notifikasi error ke group
        try:
            error_msg = f"❌ **Database Backup Error**\n\n"
            error_msg += f"⏰ Waktu: {datetime.now(pytz.timezone('Asia/Jakarta')).strftime('%d/%m/%Y %H:%M:%S')} WIB\n"
            error_msg += f"🐛 Error: `{str(e)}`"
            
            await bot.send_message(BACKUP_GROUP_ID, error_msg)
        except:
            pass
            
        # Pastikan koneksi database tetap terbuka meski backup gagal
        try:
            conn = sqlite3.connect("score.db", check_same_thread=False)
            cursor = conn.cursor()
        except:
            pass

async def schedule_daily_backup():
    """Scheduler untuk backup harian jam 00:00 WIB"""
    wib_tz = pytz.timezone('Asia/Jakarta')
    
    while True:
        try:
            now = datetime.now(wib_tz)
            
            # Hitung waktu sampai jam 00:00 berikutnya
            tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            seconds_until_midnight = (tomorrow - now).total_seconds()
            
            print(f"⏰ Backup berikutnya dalam {seconds_until_midnight/3600:.1f} jam")
            
            # Tunggu sampai jam 00:00
            await asyncio.sleep(seconds_until_midnight)
            
            # Jalankan backup
            await backup_database()
            
        except Exception as e:
            print(f"Error di scheduler backup: {e}")
            # Tunggu 1 jam jika ada error
            await asyncio.sleep(3600)

# Fungsi untuk kirim statistik harian ke group log
async def send_daily_stats():
    """Kirim statistik harian ke group log"""
    try:
        wib_tz = pytz.timezone('Asia/Jakarta')
        today = datetime.now(wib_tz)
        
        # Ambil data statistik dari database
        cursor.execute("SELECT COUNT(*) FROM scores")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT SUM(wins) FROM scores")
        total_games = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT username, wins FROM scores ORDER BY wins DESC LIMIT 5")
        top_players = cursor.fetchall()
        
        stats_msg = f"📊 **Daily Statistics**\n\n"
        stats_msg += f"📅 **Tanggal:** {today.strftime('%d/%m/%Y')}\n"
        stats_msg += f"👥 **Total Users:** {total_users}\n"
        stats_msg += f"🎮 **Total Games:** {total_games}\n\n"
        
        if top_players:
            stats_msg += "🏆 **Top 5 Players:**\n"
            for i, (username, wins) in enumerate(top_players, 1):
                stats_msg += f"{i}. {username} - {wins} wins\n"
        
        await bot.send_message(BACKUP_GROUP_ID, stats_msg)
        
    except Exception as e:
        print(f"Error send daily stats: {e}")

async def main():
    await bot.start()
    await set_commands()
    
    # Jalankan scheduler backup di background
    asyncio.create_task(schedule_daily_backup())
    
    print("🤖 Bot aktif dan menunggu...")
    print(f"🗄️ Auto backup scheduler aktif (00:00 WIB) -> Group: {BACKUP_GROUP_ID}")
    await idle()
    await bot.stop()

loop = asyncio.get_event_loop()
loop.run_until_complete(main())
