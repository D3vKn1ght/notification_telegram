# pip install python-telegram-bot
# pip install python-telegram-bot[job-queue]
import asyncio
import ast
import datetime as dt
import json
import logging
import os
import time
from datetime import timedelta
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import pytz
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, ContextTypes

from config_telegram import TOKEN, weekday_dict, weekday_data

TIME_FMT = "%Y-%m-%d %H:%M"
DATA_FILE = "data.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# groups schema:
# groups[chat_id] = {
#   "chat_id": int,
#   "name": str,
#   "settings": {"tz": "Asia/Ho_Chi_Minh", "enabled": True},
#   "data": [
#       {"id": int, "time_receive": str, "duration": int, "message": str, "enabled": True}
#   ]
# }
groups: Dict[int, Dict[str, Any]] = {}
data_lock = asyncio.Lock()


# -----------------------------
# Helpers: timezone & datetime
# -----------------------------
def get_tz(tz_name: str) -> pytz.BaseTzInfo:
    return pytz.timezone(tz_name)


def ensure_group_defaults(group: Dict[str, Any]) -> None:
    group.setdefault("settings", {})
    group["settings"].setdefault("tz", "Asia/Ho_Chi_Minh")
    group["settings"].setdefault("enabled", True)
    group.setdefault("data", [])
    # add enabled for existing reminders
    for m in group["data"]:
        m.setdefault("enabled", True)


def aware_from_timestr(timestr: str, tz: pytz.BaseTzInfo) -> dt.datetime:
    naive = dt.datetime.strptime(timestr, TIME_FMT)
    return tz.localize(naive)


def timestr_from_aware(t: dt.datetime, tz: pytz.BaseTzInfo) -> str:
    return t.astimezone(tz).strftime(TIME_FMT)


def format_vn_day(timestr: str) -> str:
    t = dt.datetime.strptime(timestr, TIME_FMT)
    return f"{weekday_dict[str(t.strftime('%A'))]}, {t.hour} giờ {t.minute} phút, {t.day}/{t.month}/{t.year}"


def get_next_datetime_from_weekday(weekday_en: str, hour: int, minute: int, tz: pytz.BaseTzInfo) -> str:
    """
    Next occurrence of weekday at hour:minute in tz.
    If today is that weekday but time passed => next week.
    """
    now = dt.datetime.now(tz)
    target = weekday_dict[weekday_en]

    t = now
    today_week = weekday_dict[t.strftime("%A")]
    while today_week != target:
        t = t + timedelta(days=1)
        today_week = weekday_dict[t.strftime("%A")]

    candidate = t.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate + timedelta(days=7)
    return candidate.strftime(TIME_FMT)


# -----------------------------
# Persistence (atomic write)
# -----------------------------
def _atomic_write_json(path: str, obj: Any) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


async def save_data() -> None:
    async with data_lock:
        payload = list(groups.values())
        _atomic_write_json(DATA_FILE, payload)
        logging.info("Saved %d groups", len(payload))


async def load_data() -> None:
    global groups
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        new_groups: Dict[int, Dict[str, Any]] = {}
        for g in payload:
            cid = int(g["chat_id"])
            g["chat_id"] = cid
            ensure_group_defaults(g)
            new_groups[cid] = g
        async with data_lock:
            groups = new_groups
        logging.info("Loaded %d groups", len(groups))
    except FileNotFoundError:
        logging.warning("No data.json found. Start fresh.")
        async with data_lock:
            groups = {}
    except Exception as e:
        logging.exception("Load error: %s", e)
        async with data_lock:
            groups = {}


# -----------------------------
# Validation / Parsing
# -----------------------------
def parse_json_from_command(text: str, command: str) -> Dict[str, Any]:
    raw = text.replace(command, "", 1).strip()
    if not raw:
        raise ValueError("Missing JSON payload")
    return json.loads(raw)


def validate_time_str(value: Any, fmt: str) -> Optional[str]:
    try:
        if not isinstance(value, str):
            return None
        time.strptime(value, fmt)
        return value
    except Exception:
        return None


def validate_duration_days(value: Any) -> Optional[int]:
    try:
        if isinstance(value, str):
            value = int(value)
        if isinstance(value, int) and value > 0:
            return value
        return None
    except Exception:
        return None


def validate_list_week(value: Any) -> Optional[List[str]]:
    """
    Accept:
    - list: ["T2","T3","CN"]
    - string repr of list: "['T2','T3','CN']" parsed by literal_eval (SAFE)
    """
    try:
        if isinstance(value, str):
            value = ast.literal_eval(value)  # SAFE
        if not isinstance(value, list):
            return None
        for w in value:
            if w not in weekday_data.keys():
                return None
        return value
    except Exception:
        return None


def get_next_id(group: Dict[str, Any]) -> int:
    max_id = 0
    for m in group.get("data", []):
        try:
            max_id = max(max_id, int(m.get("id", 0)))
        except Exception:
            pass
    return max_id + 1


# -----------------------------
# Admin check (security)
# -----------------------------
async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return False
    # private chat: allow
    if chat.type == "private":
        return True
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
        return any(a.user.id == user.id for a in admins)
    except Exception:
        return False


async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    ok = await is_admin(update, context)
    if not ok:
        await update.message.reply_text("Bạn cần là admin của group để dùng lệnh này.")
    return ok


# -----------------------------
# Commands
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return

    chat_id = int(chat.id)
    title = getattr(chat, "title", None) or "private"

    async with data_lock:
        if chat_id not in groups:
            groups[chat_id] = {
                "chat_id": chat_id,
                "name": title,
                "settings": {"tz": "Asia/Ho_Chi_Minh", "enabled": True},
                "data": []
            }
        else:
            groups[chat_id]["name"] = title
            ensure_group_defaults(groups[chat_id])

    # ensure job exists
    exists = any(j.name == "auto_send" for j in context.job_queue.jobs())
    if not exists:
        context.job_queue.run_repeating(send_due_messages, name="auto_send", interval=10, first=0)

    await save_data()

    msg = (
        f"Xin chào {update.effective_user.first_name}, bot nhắc nhở đã sẵn sàng trong: {title}\n\n"
        "Lệnh cơ bản:\n"
        "- /set_message {\"time_receive\":\"2026-01-30 20:00\",\"duration\":1,\"message\":\"Nhắc nhở\"}\n"
        "- /set_message_week {\"list_week\":\"['T2','T3','CN']\",\"time\":\"20:32\",\"message\":\"Nhắc nhở\"}\n"
        "- /get_message\n"
        "- /delete_message {\"id\":1}\n\n"
        "Tính năng mới:\n"
        "- /pause {\"id\":1} | /resume {\"id\":1}\n"
        "- /pause_all | /resume_all\n"
        "- /snooze {\"id\":1,\"minutes\":15}\n"
        "- /set_timezone {\"tz\":\"Asia/Bangkok\"}\n"
        "- /export\n"
    )
    await update.message.reply_text(msg)


async def set_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return

    chat_id = int(update.effective_chat.id)
    try:
        payload = parse_json_from_command(update.message.text, "/set_message")

        duration = validate_duration_days(payload.get("duration"))
        time_receive = validate_time_str(payload.get("time_receive"), TIME_FMT)
        text = payload.get("message") or ""
        msg_id = payload.get("id")

        if duration is None or time_receive is None:
            raise ValueError("Invalid duration/time_receive")

        async with data_lock:
            if chat_id not in groups:
                await update.message.reply_text("Không tìm thấy nhóm này, vui lòng nhập /start để bắt đầu")
                return

            group = groups[chat_id]
            ensure_group_defaults(group)

            if msg_id is None:
                new_id = get_next_id(group)
                group["data"].append({
                    "id": new_id,
                    "time_receive": time_receive,
                    "duration": duration,
                    "message": text,
                    "enabled": True
                })
                reply = f"Đã thêm nhắc nhở (ID={new_id})"
            else:
                msg_id = int(msg_id)
                found = False
                for m in group["data"]:
                    if int(m.get("id")) == msg_id:
                        m["time_receive"] = time_receive
                        m["duration"] = duration
                        m["message"] = text
                        found = True
                        break
                reply = "Đã cập nhật nhắc nhở" if found else "Không tồn tại id này"

        await save_data()
        await update.message.reply_text(reply)

    except Exception as e:
        logging.info("set_message error: %s", e)
        await update.message.reply_text("Sai định dạng, vui lòng nhập lại")


async def set_message_week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return

    chat_id = int(update.effective_chat.id)
    try:
        payload = parse_json_from_command(update.message.text, "/set_message_week")

        list_week = validate_list_week(payload.get("list_week"))
        time_hm = validate_time_str(payload.get("time"), "%H:%M")
        text = payload.get("message") or ""
        duration = 7

        if list_week is None or time_hm is None:
            raise ValueError("Invalid list_week/time")

        hhmm = dt.datetime.strptime(time_hm, "%H:%M")
        hour, minute = hhmm.hour, hhmm.minute

        async with data_lock:
            if chat_id not in groups:
                await update.message.reply_text("Không tìm thấy nhóm này, vui lòng nhập /start để bắt đầu")
                return

            group = groups[chat_id]
            ensure_group_defaults(group)
            tz = get_tz(group["settings"]["tz"])

            for w in list_week:
                weekday_en = weekday_data[w]["EN"]
                next_time = get_next_datetime_from_weekday(weekday_en, hour, minute, tz)
                new_id = get_next_id(group)
                group["data"].append({
                    "id": new_id,
                    "time_receive": next_time,
                    "duration": duration,
                    "message": text,
                    "enabled": True
                })

        await save_data()
        await update.message.reply_text("Đã thêm nhắc nhở theo tuần")

    except Exception as e:
        logging.info("set_message_week error: %s", e)
        await update.message.reply_text("Sai định dạng, vui lòng nhập lại")


async def get_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = int(update.effective_chat.id)

    async with data_lock:
        group = groups.get(chat_id)
        if not group:
            await update.message.reply_text("Không tìm thấy nhóm này, vui lòng nhập /start để bắt đầu")
            return

        ensure_group_defaults(group)
        tz_name = group["settings"]["tz"]
        enabled_all = group["settings"]["enabled"]

        data = list(group["data"])
        # sort by time_receive
        def _key(m: Dict[str, Any]) -> Tuple[int, str]:
            # enabled first, then time
            return (0 if m.get("enabled", True) else 1, m.get("time_receive", "9999-99-99 99:99"))
        data.sort(key=_key)

    if not data:
        await update.message.reply_text(f"Chưa có nhắc nhở nào.\nTimezone: {tz_name}\nGroup enabled: {enabled_all}")
        return

    lines = [
        "Danh sách nhắc nhở",
        f"- Timezone: {tz_name}",
        f"- Group enabled: {enabled_all}",
        ""
    ]
    for m in data:
        lines.append("*" * 20)
        lines.append(f"ID: {m['id']} | Enabled: {m.get('enabled', True)}")
        lines.append(f"Thời gian nhận: {format_vn_day(m['time_receive'])}")
        lines.append(f"Chu kỳ: {m['duration']} ngày")
        lines.append(f"Nội dung: {m.get('message','')}")
        lines.append("*" * 20)

    await update.message.reply_text("\n".join(lines))


async def delete_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return

    chat_id = int(update.effective_chat.id)
    try:
        payload = parse_json_from_command(update.message.text, "/delete_message")
        if payload.get("id") is None:
            await update.message.reply_text("Thiếu id")
            return

        target_id = int(payload["id"])

        async with data_lock:
            group = groups.get(chat_id)
            if not group:
                await update.message.reply_text("Không tìm thấy nhóm này, vui lòng nhập /start để bắt đầu")
                return
            ensure_group_defaults(group)

            before = len(group["data"])
            group["data"] = [m for m in group["data"] if int(m.get("id", -1)) != target_id]
            after = len(group["data"])

        await save_data()
        await update.message.reply_text("Đã xóa nhắc nhở" if after < before else "Không tìm thấy id")

    except Exception as e:
        logging.info("delete_message error: %s", e)
        await update.message.reply_text("Sai định dạng, vui lòng nhập lại")


# -------- New: pause/resume/snooze/pause_all/resume_all --------
async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return

    chat_id = int(update.effective_chat.id)
    try:
        payload = parse_json_from_command(update.message.text, "/pause")
        if payload.get("id") is None:
            await update.message.reply_text("Thiếu id")
            return
        target_id = int(payload["id"])

        async with data_lock:
            group = groups.get(chat_id)
            if not group:
                await update.message.reply_text("Không tìm thấy nhóm này, vui lòng /start")
                return
            ensure_group_defaults(group)

            found = False
            for m in group["data"]:
                if int(m.get("id")) == target_id:
                    m["enabled"] = False
                    found = True
                    break

        if found:
            await save_data()
            await update.message.reply_text("Đã tạm dừng nhắc nhở")
        else:
            await update.message.reply_text("Không tìm thấy id")

    except Exception as e:
        logging.info("pause error: %s", e)
        await update.message.reply_text("Sai định dạng, vui lòng nhập lại")


async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return

    chat_id = int(update.effective_chat.id)
    try:
        payload = parse_json_from_command(update.message.text, "/resume")
        if payload.get("id") is None:
            await update.message.reply_text("Thiếu id")
            return
        target_id = int(payload["id"])

        async with data_lock:
            group = groups.get(chat_id)
            if not group:
                await update.message.reply_text("Không tìm thấy nhóm này, vui lòng /start")
                return
            ensure_group_defaults(group)

            found = False
            for m in group["data"]:
                if int(m.get("id")) == target_id:
                    m["enabled"] = True
                    found = True
                    break

        if found:
            await save_data()
            await update.message.reply_text("Đã bật lại nhắc nhở")
        else:
            await update.message.reply_text("Không tìm thấy id")

    except Exception as e:
        logging.info("resume error: %s", e)
        await update.message.reply_text("Sai định dạng, vui lòng nhập lại")


async def snooze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return

    chat_id = int(update.effective_chat.id)
    try:
        payload = parse_json_from_command(update.message.text, "/snooze")
        if payload.get("id") is None or payload.get("minutes") is None:
            await update.message.reply_text("Cần {\"id\":...,\"minutes\":...}")
            return

        target_id = int(payload["id"])
        minutes = int(payload["minutes"])
        if minutes <= 0:
            await update.message.reply_text("minutes phải > 0")
            return

        async with data_lock:
            group = groups.get(chat_id)
            if not group:
                await update.message.reply_text("Không tìm thấy nhóm này, vui lòng /start")
                return
            ensure_group_defaults(group)

            tz = get_tz(group["settings"]["tz"])
            now = dt.datetime.now(tz)
            new_time = now + timedelta(minutes=minutes)

            found = False
            for m in group["data"]:
                if int(m.get("id")) == target_id:
                    m["time_receive"] = new_time.strftime(TIME_FMT)
                    m["enabled"] = True
                    found = True
                    break

        if found:
            await save_data()
            await update.message.reply_text(f"Đã snooze {minutes} phút")
        else:
            await update.message.reply_text("Không tìm thấy id")

    except Exception as e:
        logging.info("snooze error: %s", e)
        await update.message.reply_text("Sai định dạng, vui lòng nhập lại")


async def pause_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    chat_id = int(update.effective_chat.id)

    async with data_lock:
        group = groups.get(chat_id)
        if not group:
            await update.message.reply_text("Không tìm thấy nhóm này, vui lòng /start")
            return
        ensure_group_defaults(group)
        group["settings"]["enabled"] = False

    await save_data()
    await update.message.reply_text("Đã tạm dừng toàn bộ nhắc nhở trong group")


async def resume_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    chat_id = int(update.effective_chat.id)

    async with data_lock:
        group = groups.get(chat_id)
        if not group:
            await update.message.reply_text("Không tìm thấy nhóm này, vui lòng /start")
            return
        ensure_group_defaults(group)
        group["settings"]["enabled"] = True

    await save_data()
    await update.message.reply_text("Đã bật lại toàn bộ nhắc nhở trong group")


# -------- New: timezone per group --------
async def set_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return

    chat_id = int(update.effective_chat.id)
    try:
        payload = parse_json_from_command(update.message.text, "/set_timezone")
        tz_name = payload.get("tz")
        if not isinstance(tz_name, str) or not tz_name:
            await update.message.reply_text("Cần {\"tz\":\"Asia/Bangkok\"} (ví dụ)")
            return

        # validate tz
        try:
            _ = pytz.timezone(tz_name)
        except Exception:
            await update.message.reply_text("Timezone không hợp lệ. Ví dụ: Asia/Ho_Chi_Minh, Asia/Bangkok, Asia/Tokyo")
            return

        async with data_lock:
            group = groups.get(chat_id)
            if not group:
                await update.message.reply_text("Không tìm thấy nhóm này, vui lòng /start")
                return
            ensure_group_defaults(group)
            group["settings"]["tz"] = tz_name

        await save_data()
        await update.message.reply_text(f"Đã cập nhật timezone: {tz_name}")

    except Exception as e:
        logging.info("set_timezone error: %s", e)
        await update.message.reply_text("Sai định dạng, vui lòng nhập lại")


# -------- New: export data --------
async def export_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return

    chat_id = int(update.effective_chat.id)
    async with data_lock:
        group = groups.get(chat_id)
        if not group:
            await update.message.reply_text("Không tìm thấy nhóm này, vui lòng /start")
            return
        ensure_group_defaults(group)
        payload = group

    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    bio = BytesIO(raw)
    bio.name = f"data_export_{chat_id}.json"
    await update.message.reply_document(document=InputFile(bio), caption="Backup dữ liệu nhắc nhở của group này.")


# -----------------------------
# Job: send due reminders
# -----------------------------
async def send_due_messages(context: ContextTypes.DEFAULT_TYPE) -> None:
    # snapshot to reduce lock duration
    async with data_lock:
        snapshot = [(cid, dict(g)) for cid, g in groups.items()]

    changed = False

    for chat_id, g in snapshot:
        try:
            ensure_group_defaults(g)
            if not g["settings"]["enabled"]:
                continue

            tz = get_tz(g["settings"]["tz"])
            now = dt.datetime.now(tz)

            # copy list for iteration
            data_list = list(g.get("data", []))

            for m in data_list:
                try:
                    if not m.get("enabled", True):
                        continue

                    due_time = aware_from_timestr(m["time_receive"], tz)
                    if due_time > now:
                        continue

                    # send once
                    text = f"Nhắc nhở: {m.get('message','')}\n"
                    await context.bot.send_message(chat_id=chat_id, text=text)

                    # reschedule (catch-up)
                    duration_days = int(m["duration"])
                    next_time = due_time
                    while next_time <= now:
                        next_time += timedelta(days=duration_days)

                    # write back
                    async with data_lock:
                        gg = groups.get(chat_id)
                        if not gg:
                            continue
                        ensure_group_defaults(gg)
                        for mm in gg["data"]:
                            if int(mm.get("id")) == int(m.get("id")):
                                mm["time_receive"] = timestr_from_aware(next_time, tz)
                                changed = True
                                break

                except Exception as e:
                    logging.info("send loop error: %s", e)

        except Exception as e:
            logging.info("group loop error: %s", e)

    if changed:
        await save_data()


# -----------------------------
# Main
# -----------------------------
async def on_startup(app: Application) -> None:
    await load_data()


def main() -> None:
    app = Application.builder().token(TOKEN).post_init(on_startup).build()

    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("set_message", set_message))
    app.add_handler(CommandHandler("set_message_week", set_message_week))
    app.add_handler(CommandHandler("get_message", get_message))
    app.add_handler(CommandHandler("delete_message", delete_message))

    # new handlers
    app.add_handler(CommandHandler("pause", pause))
    app.add_handler(CommandHandler("resume", resume))
    app.add_handler(CommandHandler("snooze", snooze))
    app.add_handler(CommandHandler("pause_all", pause_all))
    app.add_handler(CommandHandler("resume_all", resume_all))
    app.add_handler(CommandHandler("set_timezone", set_timezone))
    app.add_handler(CommandHandler("export", export_data))

    app.run_polling()


if __name__ == "__main__":
    main()
