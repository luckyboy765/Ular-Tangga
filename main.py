from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from PIL import Image, ImageDraw
import os, json, random, sqlite3, asyncio

API_ID = 20409913
API_HASH = "7e5b7eb079ab46d84cde424962b020a0"
BOT_TOKEN = "7904552484:AAEkRkT3URxLdUW0ZPTzgVlYRhW2Ra1RqSk"

bot = Client("snakeludo_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

player_positions = {}
player_colors = {}
available_colors = ["red", "blue", "green", "yellow"]
game_turn_order = []
winners = []  # Untuk menyimpan urutan pemenang
current_turn_index = 0
game_created = False
paused_for_challenge = False
last_message_id = None

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

BOARD_IMAGE_PATH = "board_new.png"
GRID_SIZE = 6

def get_chat_settings(chat_id):
    cursor.execute("SELECT * FROM game_settings WHERE chat_id = ?", (chat_id,))
    row = cursor.fetchone()
    if row:
        return {
            'snakes': json.loads(row[1]),
            'ladders': json.loads(row[2]),
            'truth_positions': json.loads(row[3]),
            'dare_positions': json.loads(row[4]),
            'truth_prompts': json.loads(row[5]),
            'dare_prompts': json.loads(row[6]),
            'admins': json.loads(row[7])
        }
    else:
        cursor.execute("""
            INSERT INTO game_settings (chat_id, snakes, ladders, truth_positions, dare_positions, truth_prompts, dare_prompts, admins)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            chat_id,
            json.dumps(DEFAULT_SNAKES),
            json.dumps(DEFAULT_LADDERS),
            json.dumps(DEFAULT_TRUTH_POSITIONS),
            json.dumps(DEFAULT_DARE_POSITIONS),
            json.dumps(DEFAULT_TRUTH_PROMPTS),
            json.dumps(DEFAULT_DARE_PROMPTS),
            json.dumps([])
        ))
        conn.commit()
        return get_chat_settings(chat_id)

def update_chat_settings(chat_id, key, value):
    cursor.execute(f"UPDATE game_settings SET {key} = ? WHERE chat_id = ?", (json.dumps(value), chat_id))
    conn.commit()

def is_admin(chat_id, user_id):
    settings = get_chat_settings(chat_id)
    return user_id in settings['admins']

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

def generate_board_image():
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

@bot.on_message(filters.command("adddare"))
async def add_dare_prompt(_, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not is_admin(chat_id, user_id):
        await message.reply("❌ Hanya admin yang bisa menambah dare.")
        return
    prompt_text = message.text[len("/adddare"):].strip()
    if not prompt_text:
        await message.reply("❌ Masukkan teks dare. Format: `/adddare Dare: Contoh dare`")
        return
    settings = get_chat_settings(chat_id)
    settings['dare_prompts'].append(prompt_text)
    update_chat_settings(chat_id, 'dare_prompts', settings['dare_prompts'])
    await message.reply(f"✅ Dare ditambahkan: {prompt_text}")

@bot.on_message(filters.command("listdare"))
async def list_dare(_, message: Message):
    chat_id = message.chat.id
    settings = get_chat_settings(chat_id)
    if not settings['dare_prompts']:
        await message.reply("❌ Belum ada dare.")
        return
    dare_list = "\n".join([f"{i+1}. {d}" for i, d in enumerate(settings['dare_prompts'])])
    await message.reply(f"🎯 **Daftar Dare:**\n{dare_list}")

@bot.on_message(filters.command("removedare"))
async def remove_dare(_, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not is_admin(chat_id, user_id):
        await message.reply("❌ Hanya admin yang bisa hapus dare.")
        return
    try:
        index = int(message.text.split()[1]) - 1
        settings = get_chat_settings(chat_id)
        if 0 <= index < len(settings['dare_prompts']):
            removed = settings['dare_prompts'].pop(index)
            update_chat_settings(chat_id, 'dare_prompts', settings['dare_prompts'])
            await message.reply(f"✅ Dare dihapus: {removed}")
        else:
            await message.reply("❌ Nomor tidak valid.")
    except:
        await message.reply("❌ Format salah. Gunakan: `/removedare 1`")

@bot.on_message(filters.command("new"))
async def new_game(_, message: Message):
    global game_created
    if game_created:
        await message.reply("🎮 Game sudah dibuat. Gunakan tombol untuk bergabung.")
        return

    game_created = True

    text = """**Ruang bermain berhasil dibuat.**
Gunakan tombol join untuk bergabung ke dalam permainan dan klik tombol start untuk memulai permainan.

*Catatan:*
(1) Ketika permainan dimulai, ruang bermain akan ditutup sehingga tidak ada orang yang dapat tergabung kedalam ruang bermain tersebut.
(2) Ruang bermain akan otomatis dihapus setelah saat permainan berakhir.
(3) Maksimal pemain yang dapat bergabung dalam ruang bermain adalah 4 orang dan permainan akan otomatis dimulai apabila pemain ke-4 telah memasuki ruang bermain."""

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Gabung permainan", callback_data="join")],
        [InlineKeyboardButton("Mulai permainan", callback_data="start")],
        [InlineKeyboardButton("🗑 Hapus", callback_data="delete_room")]
    ])

    await message.reply(text, reply_markup=keyboard)


@bot.on_message(filters.command("join"))
async def join_game(_, message: Message):
    if not game_created:
        await message.reply("❌ Belum ada game yang dibuat. Gunakan /create dulu.")
        return
    user_id = message.from_user.id
    if user_id in player_positions:
        await message.reply("Kamu sudah ikut bermain.")
        return
    if len(player_colors) >= 4:
        await message.reply("⚠️ Pemain penuh (maks 4).")
        return
    color = available_colors.pop(0)
    player_positions[user_id] = 1
    player_colors[user_id] = color
    game_turn_order.append(user_id)
    await message.reply(f"✅ Kamu bergabung sebagai pion **{color}**!")

@bot.on_message(filters.command("start"))
async def start_game(_, message: Message):
    if not game_created:
        await message.reply("❌ Belum ada game. Gunakan /create.")
        return
    if len(player_positions) < 2:
        await message.reply("⏳ Minimal 2 pemain untuk memulai.")
        return
    path = generate_board_image()
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🎲 Lempar Dadu", callback_data="roll")]])
    await message.reply_photo(photo=path, caption="🎮 Game dimulai! Gunakan /roll atau tekan tombol di bawah.", reply_markup=keyboard)

async def roll_dice_for_user(user_id: int, chat_id: int, first_name: str, username: str, callback_query=None):
    global current_turn_index, paused_for_challenge, last_message_id, winners, game_created

    settings = get_chat_settings(chat_id)

    if paused_for_challenge:
        await bot.send_message(chat_id, "⏸️ Game sedang ditunda karena tantangan sedang berlangsung. Gunakan /continue jika sudah selesai.")
        return

    if user_id not in player_positions or user_id not in game_turn_order:
        if callback_query:
            await callback_query.answer("❌ Kamu bukan bagian dari game ini.", show_alert=True)
        else:
            await bot.send_message(chat_id, "❌ Kamu bukan bagian dari game ini.")
        return

    if len(player_positions) < 2:
        await bot.send_message(chat_id, "⏳ Minimal 2 pemain untuk memulai.")
        return

    if game_turn_order[current_turn_index] != user_id:
        if callback_query:
            await callback_query.answer("⏳ Bukan giliranmu.", show_alert=True)
        else:
            await bot.send_message(chat_id, "⏳ Bukan giliranmu.")
        return

    dice_msg = await bot.send_dice(chat_id, emoji="🎲")
    roll = dice_msg.dice.value
    await asyncio.sleep(4)
    await dice_msg.delete()

    current = player_positions[user_id]
    new_pos = current + roll if current + roll <= 36 else current

    snakes = {int(k): v for k, v in settings['snakes'].items()}
    ladders = {int(k): v for k, v in settings['ladders'].items()}

    if new_pos in snakes:
        new_pos = snakes[new_pos]
    elif new_pos in ladders:
        new_pos = ladders[new_pos]

    kicked = None
    for uid, pos in player_positions.items():
        if uid != user_id and pos == new_pos:
            player_positions[uid] = 1
            kicked = uid

    player_positions[user_id] = new_pos

    text = f"🎲 {first_name} melempar dadu: {roll}\n📍 Posisi: {current} ➡️ {new_pos}\n"
    if new_pos == current and roll > 0:
        text += "❗ Kamu butuh angka yang tepat untuk mencapai kotak 36.\n"
    if kicked:
        text += f"💥 Pemain lain ditendang ke kotak 1!\n"

    challenge_msg = None
    if new_pos in settings['truth_positions'] and settings['truth_prompts']:
        paused_for_challenge = True
        challenge = random.choice(settings['truth_prompts'])
        challenge_msg = {
            "text": f"🧠 **Truth Challenge untuk {first_name}:**\n{challenge}"
        }
    elif new_pos in settings['dare_positions'] and settings['dare_prompts']:
        paused_for_challenge = True
        challenge = random.choice(settings['dare_prompts'])
        challenge_msg = {
            "text": f"🎯 **Dare Challenge untuk {first_name}:**\n{challenge}"
        }
    elif new_pos == 36:
        if user_id not in winners:
            winners.append(user_id)

        text += f"🏅 {first_name} mencapai kotak 36! Urutan saat ini:\n"
        for i, uid in enumerate(winners, start=1):
            user_obj = await bot.get_users(uid)
            text += f"{i}. {user_obj.first_name}\n"

        if user_id in game_turn_order:
            game_turn_order.remove(user_id)
        if user_id in player_positions:
            del player_positions[user_id]
        if user_id in player_colors:
            available_colors.append(player_colors.pop(user_id, None))

        cursor.execute("INSERT OR IGNORE INTO scores (user_id, username, wins) VALUES (?, ?, 0)", (user_id, username))
        cursor.execute("UPDATE scores SET wins = wins + 1 WHERE user_id = ?", (user_id,))
        conn.commit()

        # Periksa apakah hanya satu pemain tersisa
        if len(game_turn_order) == 1:
            last_uid = game_turn_order[0]
            if last_uid not in winners:
                winners.append(last_uid)

            last_user = await bot.get_users(last_uid)
            last_name = last_user.first_name

            cursor.execute("INSERT OR IGNORE INTO scores (user_id, username, wins) VALUES (?, ?, 0)", (last_uid, last_name))
            cursor.execute("UPDATE scores SET wins = wins + 1 WHERE user_id = ?", (last_uid,))
            conn.commit()

            text += f"\n🏁 Game selesai! Urutan pemenang:\n"
            for i, uid in enumerate(winners, start=1):
                name = (await bot.get_users(uid)).first_name
                text += f"{i}. {name}\n"

            # Reset game
            player_positions.clear()
            player_colors.clear()
            game_turn_order.clear()
            available_colors[:] = ["red", "blue", "green", "yellow"]
            current_turn_index = 0
            game_created = False
            winners.clear()
            keyboard = None
        else:
            # Lanjut ke pemain berikutnya
            current_turn_index %= len(game_turn_order)
            next_uid = game_turn_order[current_turn_index]
            user_obj = await bot.get_users(next_uid)
            mention = f"[{user_obj.first_name}](tg://user?id={next_uid})"
            text += f"🎯 Giliran selanjutnya: {mention}"
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🎲 Lempar Dadu", callback_data="roll")]])
    else:
        # Jika tidak menang dan tidak ada challenge
        if not paused_for_challenge:
            current_turn_index = (current_turn_index + 1) % len(game_turn_order)
        next_uid = game_turn_order[current_turn_index]
        user_obj = await bot.get_users(next_uid)
        mention = f"[{user_obj.first_name}](tg://user?id={next_uid})"
        text += f"🎯 Giliran selanjutnya: {mention}"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🎲 Lempar Dadu", callback_data="roll")]])

    # Update board
    # Update board
    path = generate_board_image()

    # FIX: pastikan keyboard didefinisikan
    if 'keyboard' not in locals():
        keyboard = None

    if last_message_id:
        try:
            await bot.delete_messages(chat_id, last_message_id)
        except:
            pass

    sent = await bot.send_photo(chat_id, photo=path, caption=text, reply_markup=keyboard)
    last_message_id = sent.id

    if challenge_msg:
        await bot.send_message(
            chat_id,
            challenge_msg["text"],
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Continue", callback_data="continue")]])
        )


# Command /roll
@bot.on_message(filters.command("roll"))
async def roll_dice(_, message: Message):
    user = message.from_user
    await roll_dice_for_user(user.id, message.chat.id, user.first_name, user.username or str(user.id))

async def join_game_callback(client, callback):
    global game_created
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id

    if not game_created:
        await callback.answer("❌ Belum ada game.", show_alert=True)
        return

    if user_id in player_positions:
        await callback.answer("Kamu sudah ikut bermain.", show_alert=True)
        return

    if len(player_colors) >= 4:
        await callback.answer("⚠️ Pemain penuh (maks 4).", show_alert=True)
        return

    color = available_colors.pop(0)
    player_positions[user_id] = 1
    player_colors[user_id] = color
    game_turn_order.append(user_id)

    await callback.answer(f"✅ Kamu bergabung sebagai pion {color}!")
    await callback.message.reply(f"👤 {callback.from_user.first_name} bergabung sebagai pion **{color}**!")

async def start_game_callback(client, callback):
    global game_created
    chat_id = callback.message.chat.id

    if not game_created:
        await callback.answer("❌ Belum ada game.", show_alert=True)
        return

    if len(player_positions) < 2:
        await callback.answer("⏳ Minimal 2 pemain untuk memulai.", show_alert=True)
        return

    path = generate_board_image()
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🎲 Lempar Dadu", callback_data="roll")]])

    # Buat daftar pemain dan warnanya
    player_lines = []
    for uid in game_turn_order:
        user = await bot.get_users(uid)
        name = user.first_name
        color = player_colors.get(uid, "???")
        emoji = COLOR_EMOJIS.get(color, color)
        player_lines.append(f"• {name} dengan pion {emoji}")


    players_text = "\n".join(player_lines)

        # Giliran pertama
    first_uid = game_turn_order[0]
    first_user = await bot.get_users(first_uid)
    first_color = player_colors.get(first_uid, "???")
    first_mention = f"[{first_user.first_name}](tg://user?id={first_uid})"

    first_emoji = COLOR_EMOJIS.get(first_color, first_color)
    caption = (
        "🎮 Game dimulai! Gunakan /roll atau tekan tombol di bawah.\n"
        f"🎯 Sekarang giliran {first_mention} dengan pion {first_emoji}"
    )

    await callback.message.reply_photo(photo=path, caption=caption, reply_markup=keyboard)
    await callback.answer()

@bot.on_callback_query()
async def callback_query_handler(_, callback):
    global paused_for_challenge, current_turn_index, last_message_id

    if callback.data == "join":
        await join_game_callback(bot, callback)  # Panggil fungsi join yang sudah ada
        await callback.answer()
    elif callback.data == "start":
        await start_game_callback(bot, callback)
        await callback.answer()
    elif callback.data == "delete_room":
        global game_created
        game_created = False
        await callback.message.delete()
        await callback.answer("🗑 Ruang bermain dihapus.")
    elif callback.data == "roll":
        user = callback.from_user
        prev_last_message = last_message_id  # Simpan ID lama

        await roll_dice_for_user(user.id, callback.message.chat.id, user.first_name, user.username or str(user.id), callback)

        # Hanya hapus pesan jika giliran user yang valid
        if last_message_id != prev_last_message:
            try:
                await callback.message.delete()
            except:
                pass
        await callback.answer()
    elif callback.data == "continue":
        if not paused_for_challenge:
            await callback.answer("✅ Tidak ada tantangan yang sedang berlangsung.")
            return
        paused_for_challenge = False
        current_turn_index = (current_turn_index + 1) % len(game_turn_order)
        next_uid = game_turn_order[current_turn_index]
        user_obj = await bot.get_users(next_uid)
        mention = f"[{user_obj.first_name}](tg://user?id={next_uid})"
        path = generate_board_image()
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🎲 Lempar Dadu", callback_data="roll")]])
        text = f"▶️ Game dilanjutkan! 🎯 Giliran selanjutnya: {mention}"
        if last_message_id:
            try:
                await bot.delete_messages(callback.message.chat.id, last_message_id)
            except:
                pass
        sent = await callback.message.reply_photo(photo=path, caption=text, reply_markup=keyboard)
        last_message_id = sent.id
        try:
            await callback.message.delete()
        except:
            pass
        await callback.answer()

@bot.on_message(filters.command("reset"))
async def reset_game(_, message: Message):
    global game_created, current_turn_index, last_message_id, winners
    player_positions.clear()
    player_colors.clear()
    available_colors[:] = ["red", "blue", "green", "yellow"]
    game_turn_order.clear()
    current_turn_index = 0
    game_created = False
    last_message_id = None
    winners.clear()
    await message.reply("🔄 Game direset!")


@bot.on_message(filters.command("continue"))
async def continue_game(_, message: Message):
    global paused_for_challenge, current_turn_index
    if not paused_for_challenge:
        await message.reply("✅ Tidak ada tantangan yang sedang berlangsung.")
        return
    paused_for_challenge = False
    current_turn_index = (current_turn_index + 1) % len(game_turn_order)
    next_uid = game_turn_order[current_turn_index]
    user_obj = await bot.get_users(next_uid)
    mention = f"[{user_obj.first_name}](tg://user?id={next_uid})"
    await message.reply(f"▶️ Game dilanjutkan! 🎯 Giliran selanjutnya: {mention}")

# Tambahkan command /kick
@bot.on_message(filters.command("kick"))
async def kick_player(_, message: Message):
    global game_created, current_turn_index

    chat_id = message.chat.id
    user_id = message.from_user.id

    if not is_admin(chat_id, user_id):
        await message.reply("❌ Hanya admin yang bisa kick pemain.")
        return

    if not message.reply_to_message:
        await message.reply("❌ Reply ke pesan pemain yang ingin di-kick.")
        return

    target_id = message.reply_to_message.from_user.id

    if target_id not in player_positions:
        await message.reply("❌ User tersebut tidak sedang bermain.")
        return

    # Kick player
    color = player_colors.pop(target_id, None)
    player_positions.pop(target_id, None)
    if target_id in game_turn_order:
        was_turn = game_turn_order[current_turn_index] == target_id
        game_turn_order.remove(target_id)

        # Update giliran jika pemain yg di-kick sedang dalam giliran
        if was_turn:
            current_turn_index %= max(len(game_turn_order), 1)

    if color:
        available_colors.append(color)

    await message.reply(f"👢 Pemain {message.reply_to_message.from_user.first_name} telah di-kick dari game.")
    await check_if_game_over(chat_id)
    # Jika hanya 1 pemain tersisa, akhiri game
    if len(game_turn_order) == 1:
        winner_id = game_turn_order[0]
        winner_user = await bot.get_users(winner_id)
        winner_name = winner_user.first_name

        cursor.execute("INSERT OR IGNORE INTO scores (user_id, username, wins) VALUES (?, ?, 0)", (winner_id, winner_name))
        cursor.execute("UPDATE scores SET wins = wins + 1 WHERE user_id = ?", (winner_id,))
        conn.commit()

        player_positions.clear()
        player_colors.clear()
        game_turn_order.clear()
        available_colors[:] = ["red", "blue", "green", "yellow"]
        current_turn_index = 0
        game_created = False

        await message.reply(f"🏆 Game selesai otomatis! Pemenangnya adalah **{winner_name}**.")


# Tambahkan /addadmin
@bot.on_message(filters.command("addadmin"))
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
    global game_created, player_positions, player_colors, game_turn_order
    global current_turn_index, available_colors, last_message_id
    global paused_for_challenge  # <- Tambahkan ini

    if len(game_turn_order) == 1:
        # Jika tantangan masih aktif, matikan
        paused_for_challenge = False

        winner_id = game_turn_order[0]
        winner_user = await bot.get_users(winner_id)
        winner_name = winner_user.first_name

        cursor.execute("INSERT OR IGNORE INTO scores (user_id, username, wins) VALUES (?, ?, 0)", (winner_id, winner_name))
        cursor.execute("UPDATE scores SET wins = wins + 1 WHERE user_id = ?", (winner_id,))
        conn.commit()

        player_positions.clear()
        player_colors.clear()
        game_turn_order.clear()
        available_colors[:] = ["red", "blue", "green", "yellow"]
        current_turn_index = 0
        game_created = False
        last_message_id = None

        await bot.send_message(chat_id, f"🏆 Game selesai otomatis! Pemenangnya adalah **{winner_name}**.")
        winners.clear()
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

@bot.on_message(filters.command("setsnakes"))
async def set_snakes(_, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not is_admin(chat_id, user_id):
        await message.reply("❌ Hanya admin yang bisa mengatur posisi ular.")
        return

    try:
        args = message.text.split()[1:]
        if not args:
            settings = get_chat_settings(chat_id)
            snake_text = "\n".join([f"{start} → {end}" for start, end in settings['snakes'].items()])
            await message.reply(f"🐍 **Posisi Ular Saat Ini:**\n{snake_text}\n\n**Format:** `/setsnakes 27:11 32:19`")
            return

        new_snakes = {}
        for arg in args:
            if ':' in arg:
                start, end = map(int, arg.split(':'))
                if 1 <= start <= 36 and 1 <= end <= 36 and start != end:
                    new_snakes[start] = end

        update_chat_settings(chat_id, 'snakes', new_snakes)
        snake_text = "\n".join([f"{start} → {end}" for start, end in new_snakes.items()])
        await message.reply(f"✅ **Ular berhasil diatur:**\n{snake_text}")

    except Exception:
        await message.reply("❌ Format salah. Gunakan: `/setsnakes 27:11 32:19`")

# Tambahkan command /setladders
@bot.on_message(filters.command("setladders"))
async def set_ladders(_, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not is_admin(chat_id, user_id):
        await message.reply("❌ Hanya admin yang bisa mengatur posisi tangga.")
        return

    try:
        args = message.text.split()[1:]
        if not args:
            settings = get_chat_settings(chat_id)
            ladder_text = "\n".join([f"{start} → {end}" for start, end in settings['ladders'].items()])
            await message.reply(f"🪜 **Posisi Tangga Saat Ini:**\n{ladder_text}\n\n**Format:** `/setladders 8:21 24:35`")
            return

        new_ladders = {}
        for arg in args:
            if ':' in arg:
                start, end = map(int, arg.split(':'))
                if 1 <= start <= 36 and 1 <= end <= 36 and start != end:
                    new_ladders[start] = end

        update_chat_settings(chat_id, 'ladders', new_ladders)
        ladder_text = "\n".join([f"{start} → {end}" for start, end in new_ladders.items()])
        await message.reply(f"✅ **Tangga berhasil diatur:**\n{ladder_text}")

    except Exception:
        await message.reply("❌ Format salah. Gunakan: `/setladders 8:21 24:35`")

@bot.on_message(filters.command("addtruth"))
async def add_truth_prompt(_, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not is_admin(chat_id, user_id):
        await message.reply("❌ Hanya admin yang bisa menambah truth.")
        return

    prompt_text = message.text[len("/addtruth"):].strip()
    if not prompt_text:
        await message.reply("❌ Masukkan teks truth. Format: `/addtruth Truth: Siapa yang kamu suka?`")
        return

    settings = get_chat_settings(chat_id)
    settings['truth_prompts'].append(prompt_text)
    update_chat_settings(chat_id, 'truth_prompts', settings['truth_prompts'])
    await message.reply(f"✅ Truth ditambahkan: {prompt_text}")

@bot.on_message(filters.command("listtruth"))
async def list_truth(_, message: Message):
    chat_id = message.chat.id
    settings = get_chat_settings(chat_id)
    if not settings['truth_prompts']:
        await message.reply("❌ Belum ada truth.")
        return
    truth_list = "\n".join([f"{i+1}. {q}" for i, q in enumerate(settings['truth_prompts'])])
    await message.reply(f"🧠 **Daftar Truth:**\n{truth_list}")

@bot.on_message(filters.command("removetruth"))
async def remove_truth(_, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not is_admin(chat_id, user_id):
        await message.reply("❌ Hanya admin yang bisa hapus truth.")
        return

    try:
        index = int(message.text.split()[1]) - 1
        settings = get_chat_settings(chat_id)
        if 0 <= index < len(settings['truth_prompts']):
            removed = settings['truth_prompts'].pop(index)
            update_chat_settings(chat_id, 'truth_prompts', settings['truth_prompts'])
            await message.reply(f"✅ Truth dihapus: {removed}")
        else:
            await message.reply("❌ Nomor tidak valid.")
    except:
        await message.reply("❌ Format salah. Gunakan: `/removetruth 1`")

@bot.on_message(filters.command("settruthpos"))
async def set_truth_positions(_, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not is_admin(chat_id, user_id):
        await message.reply("❌ Hanya admin yang bisa mengatur posisi truth.")
        return

    try:
        args = message.text.split()[1:]
        if not args:
            settings = get_chat_settings(chat_id)
            pos_text = ", ".join(map(str, settings['truth_positions']))
            await message.reply(f"🧠 **Posisi Truth Saat Ini:**\n{pos_text}\n\n**Format:** `/settruthpos 3 17 25 33`")
            return

        new_positions = [int(arg) for arg in args if 1 <= int(arg) <= 36]
        update_chat_settings(chat_id, 'truth_positions', new_positions)
        pos_text = ", ".join(map(str, new_positions))
        await message.reply(f"✅ **Posisi Truth berhasil diatur:**\n{pos_text}")

    except Exception:
        await message.reply("❌ Format salah. Gunakan: `/settruthpos 3 17 25 33`")

# Tambahkan command /setdarepos
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

    except Exception:
        await message.reply("❌ Format salah. Gunakan: `/setdarepos 4 12 20 35`")

# Tambahkan command /adddare
@bot.on_message(filters.command("adddare"))
async def add_dare_prompt(_, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not is_admin(chat_id, user_id):
        await message.reply("❌ Hanya admin yang bisa menambah dare.")
        return

    prompt_text = message.text[len("/adddare"):].strip()
    if not prompt_text:
        await message.reply("❌ Masukkan teks dare. Format: `/adddare Dare: Contoh dare`")
        return

    settings = get_chat_settings(chat_id)
    settings['dare_prompts'].append(prompt_text)
    update_chat_settings(chat_id, 'dare_prompts', settings['dare_prompts'])
    await message.reply(f"✅ Dare ditambahkan: {prompt_text}")

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


bot.run()
