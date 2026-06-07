"""Telegram adapter — the CLI's claim flow with explicit per-item buttons.

    PYTHONPATH=src .venv/bin/python -m alfred.bot.telegram_bot

Each item is its own message with [Rob] [Sam] [➗Split] buttons — tap the person
who had it. 🔮 marks items remembered from before. /done (or the Done button)
posts a minimal, *pinned* settlement summary. One bot serves every household,
keyed by chat_id. No money logic here — it's purely the interface.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, NetworkError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..access import AccessStore
from ..analytics import Analytics
from ..assemble import receipt_from_parsed
from ..memory import get_memory
from ..models import Member
from ..money import format_money as fm
from ..parsers import tesco
from ..settlement import compute_shares, settle_receipt
from ..email_intake import fetch_new_receipts, imap_enabled
from ..storage import (
    find_household_by_email,
    load_household,
    load_receipt,
    mark_nudge_sent,
    mark_receipt_paid,
    save_household,
    save_receipt,
    settled_order_refs,
)
from ..wrap import nudge as wrap_nudge, render as wrap_render
from ..config import DATA_DIR

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
# httpx logs full request URLs at INFO, including the Telegram bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("alfred")

mem = get_memory()              # MubitMemory if MUBIT_API_KEY is set, else LocalMemory
access = AccessStore()
analytics = Analytics()
SESSIONS: dict[int, dict] = {}   # chat_id -> {"receipt"}
PENDING: dict[int, object] = {}  # chat_id -> Receipt awaiting "split again?" confirmation
HOUSEHOLDS: dict[int, dict] = {}
PAYMENT_LINK = os.environ.get(
    "ALFRED_PAYMENT_LINK", "https://buy.stripe.com/aFa6oHamC1SO7Ds2h21oI00"
)
OPEN_ACCESS = os.environ.get("ALFRED_OPEN_ACCESS") == "1"  # judging: skip the access gate
NUDGE_STAGES = (1, 2, 4)
NUDGE_SCAN_SECONDS = int(os.environ.get("ALFRED_NUDGE_SCAN_SECONDS", "60"))


def household(chat_id: int) -> dict:
    if chat_id not in HOUSEHOLDS:
        data = load_household(str(chat_id))
        HOUSEHOLDS[chat_id] = (
            {"members": [Member(**m) for m in data["members"]],
             "payer_id": data.get("payer_id"), "pay": data.get("pay", {}),
             "emails": data.get("emails", {}), "telegram_ids": data.get("telegram_ids", {})}
            if data else {
                "members": [], "payer_id": None, "pay": {}, "emails": {}, "telegram_ids": {}
            }
        )
    return HOUSEHOLDS[chat_id]


def member_id_for_user(user) -> str:
    return user.first_name.lower().replace(" ", "_")


def payer_telegram_id(chat_id: int, payer_id: str) -> int | None:
    raw = household(chat_id).get("telegram_ids", {}).get(payer_id)
    return int(raw) if raw is not None else None


def can_confirm_payment(receipt_data: dict, telegram_user_id: int) -> bool:
    payer_tg_id = receipt_data.get("payer_tg_id")
    return payer_tg_id is not None and int(payer_tg_id) == telegram_user_id


def persist_household(chat_id: int) -> None:
    hh = household(chat_id)
    save_household(
        str(chat_id), hh["members"], chat_id, hh["payer_id"],
        pay=hh.get("pay", {}), emails=hh.get("emails", {}),
        telegram_ids=hh.get("telegram_ids", {}),
    )


async def require_access(update: Update) -> bool:
    chat_id = update.effective_chat.id
    if OPEN_ACCESS or access.is_activated(chat_id):
        return True
    text = (
        "🎩 This household needs an Alfred access pass.\n\n"
        f"Get three months of access: {PAYMENT_LINK}\n\n"
        "Already have a code? Use /activate CODE"
    )
    if update.callback_query:
        await update.callback_query.answer(
            "This household needs an access pass. Use /activate CODE.", show_alert=True
        )
    else:
        await update.effective_message.reply_text(text)
    return False


def real_items(receipt) -> list:
    return [it for it in receipt.items if not it.is_overhead]


def short_date(raw: str) -> str:
    try:
        return parsedate_to_datetime(raw).strftime("%-d %b")
    except (TypeError, ValueError):
        return ""


def keyboard(idx: int, members, assigned) -> InlineKeyboardMarkup:
    row = [
        InlineKeyboardButton(("✅" if assigned and m.id in assigned else "") + m.name,
                             callback_data=f"c:{idx}:{m.id}")
        for m in members
    ]
    row.append(InlineKeyboardButton("➗ All", callback_data=f"c:{idx}:__split__"))
    return InlineKeyboardMarkup([row])


def settle_markup(payer_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"💳 Paid by {payer_name} (change)", callback_data="payer")],
        [InlineKeyboardButton("🎩 Done & settle", callback_data="done")],
    ])


async def send(bot, chat_id: int, text: str, reply_markup=None, tries: int = 4):
    """send_message with retry — flaky networks drop sends, so retry transient errors."""
    for i in range(tries):
        try:
            return await bot.send_message(chat_id, text, reply_markup=reply_markup)
        except NetworkError:
            if i == tries - 1:
                raise
            await asyncio.sleep(1.0 + i)


def pay_link(provider: str, handle: str, amount_pence: int, note: str = "Alfred") -> str:
    amt = f"{amount_pence / 100:.2f}"
    if provider == "monzo":
        return f"https://monzo.me/{handle}/{amt}?d={note}"
    return f"https://revolut.me/{handle}"  # revolut.me sets the amount in-app, not via URL


def short_name(name: str, n: int = 20) -> str:
    if name.lower().startswith("tesco "):
        name = name[6:]
    return name if len(name) <= n else name[: n - 1] + "…"


def render_list(receipt, members):
    """The single shop message: one tappable button per item + payer + Done."""
    name_of = {m.id: m.name for m in members}
    payer_name = name_of.get(receipt.payer_id, "?")
    items = real_items(receipt)
    claimed = sum(1 for it in items if it.assigned_to)
    text = (f"🎩 {receipt.retailer} · {fm(receipt.stated_total_pence)} · paid by {payer_name}\n"
            f"Tap an item, then pick who shared it. {claimed}/{len(items)} claimed.")
    rows = []
    for idx, it in enumerate(items):
        mark = "✅" if it.assigned_to else "▫️"
        rows.append([InlineKeyboardButton(
            f"{mark} £{it.total_pence / 100:.2f}  {short_name(it.name, 18)}", callback_data=f"i:{idx}")])
    rows.append([InlineKeyboardButton(f"💳 Paid by {payer_name} (change)", callback_data="payer")])
    rows.append([InlineKeyboardButton("✅ Done & settle", callback_data="done")])
    return text, InlineKeyboardMarkup(rows)


def render_item(receipt, members, idx: int):
    """Per-item stepper: tap who shared it (multi-select), then Prev / List / Next."""
    name_of = {m.id: m.name for m in members}
    items = real_items(receipt)
    it = items[idx]
    a = it.assigned_to or set()
    owners = " & ".join(name_of[m.id] for m in members if m.id in a) or "nobody yet"
    text = (f"Item {idx + 1} of {len(items)} · £{it.total_pence / 100:.2f}\n"
            f"{it.name}\n\nWho shared it?  {owners}")
    member_row = [InlineKeyboardButton(("✅ " if m.id in a else "") + m.name,
                                       callback_data=f"t:{idx}:{m.id}") for m in members]
    rows = [
        member_row,
        [InlineKeyboardButton("➗ Everyone", callback_data=f"t:{idx}:all")],
        [InlineKeyboardButton("⬅ Prev", callback_data=f"p:{idx}"),
         InlineKeyboardButton("≡ List", callback_data="b"),
         InlineKeyboardButton("Next ➡", callback_data=f"n:{idx}")],
    ]
    return text, InlineKeyboardMarkup(rows)


async def start_claiming(chat_id: int, receipt, ctx: ContextTypes.DEFAULT_TYPE,
                         payer_tg_id: int | None = None, prefill: bool = True) -> None:
    members = household(chat_id)["members"]
    member_ids = {m.id for m in members}
    if prefill:                                          # skipped on a re-split — start fresh
        for it in real_items(receipt):                  # memory pre-fill
            s = mem.suggest(receipt.household_id, it.name)
            s = (s & member_ids) or None if s else None
            if s:
                it.assign(s)
    SESSIONS[chat_id] = {"receipt": receipt, "payer_tg_id": payer_tg_id}

    text, markup = render_list(receipt, members)         # ONE message — no flood control
    await send(ctx.bot, chat_id, text, reply_markup=markup)


# ---------- commands ----------

HELP_TEXT = (
    "🎩 At your service. Alfred splits the household grocery shop and settles who owes whom.\n\n"
    "👉 *New here? Send /demo* — a full sample shop, no receipt needed.\n\n"
    "*Quick start*\n"
    "1) /add Rob   /add Sam — introduce the household\n"
    "2) each member sends /join — so I can verify who paid\n"
    "3) upload your Tesco receipt as a file (.eml), or forward it to the Alfred inbox\n"
    "4) tap who shared each item → Done → I post who owes whom\n\n"
    "*Commands*\n"
    "/add — add a housemate     /join — register yourself\n"
    "/done — settle the shop    /nudge — chase unpaid shops\n"
    "/weeklywrap · /monthlywrap · /alltimewrap — spend + awards 🔮\n"
    "/setpay — your Monzo/Revolut    /setemail — forward receipts in\n"
    "/help — show this again\n\n"
    "I remember your usual splits, sir, so the next shop near settles itself."
)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    household(update.effective_chat.id)
    if not (OPEN_ACCESS or access.is_activated(update.effective_chat.id)):
        analytics.track(
            "access_page_viewed",
            chat_id=update.effective_chat.id,
            user_id=update.effective_user.id,
        )
        await update.message.reply_text(
            "🎩 Alfred splits Tesco shops and settles exactly who owes whom.\n\n"
            f"Get three months of access: {PAYMENT_LINK}\n\n"
            "Already have a paid or promotional access code? Use /activate CODE"
        )
        return
    try:
        await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
    except BadRequest:
        await update.message.reply_text(HELP_TEXT)


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
    except BadRequest:
        await update.message.reply_text(HELP_TEXT)


async def demo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Walk anyone through the full flow on a bundled sample shop — no receipt needed."""
    if not await require_access(update):
        return
    chat_id = update.effective_chat.id
    hh = household(chat_id)
    if not hh["members"]:
        hh["members"] = [Member("alex", "Alex"), Member("sam", "Sam")]
    payer_id = member_id_for_user(update.effective_user)
    if not any(m.id == payer_id for m in hh["members"]):
        payer_id = hh["members"][0].id
    hh.setdefault("telegram_ids", {})[payer_id] = update.effective_user.id
    persist_household(chat_id)
    try:
        raw = (Path(__file__).resolve().parent.parent / "demo_receipt.eml").read_bytes()
        parsed = tesco.parse(raw)
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(f"🎩 Demo receipt unavailable: {exc}")
        return
    receipt = receipt_from_parsed(parsed, members=hh["members"],
                                  payer_id=payer_id, household_id=str(chat_id))
    await update.message.reply_text(
        "🎩 *Demo mode* — a sample Tesco shop, no receipt needed.\n"
        "Tap who shared each item, then *Done & settle* to see who owes whom.\n"
        "After that, try /nudge (payment reminder) and /weeklywrap (spend + awards 🔮).",
        parse_mode="Markdown",
    )
    await start_claiming(chat_id, receipt, ctx,
                         payer_tg_id=update.effective_user.id, prefill=True)


async def activate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    # Access gate is open — no code required. Keep the command friendly so anyone
    # who types it (out of habit) isn't met with an "invalid code" wall.
    await update.message.reply_text(
        "🎩 No code needed, sir — Alfred is at your service. "
        "Try /demo for a sample shop, or /add to set up your household."
    )


async def add_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_access(update):
        return
    hh = household(update.effective_chat.id)
    name = " ".join(ctx.args).strip()
    if not name:
        await update.message.reply_text("Usage:  /add <name>   e.g.  /add Rob")
        return
    mid = name.lower().replace(" ", "_")
    if any(m.id == mid for m in hh["members"]):
        await update.message.reply_text(f"{name} is already in the flat.")
        return
    hh["members"].append(Member(mid, name))
    persist_household(update.effective_chat.id)
    await update.message.reply_text("🎩 Noted, sir. The household: " + ", ".join(m.name for m in hh["members"]))


async def join(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_access(update):
        return
    chat_id = update.effective_chat.id
    hh = household(chat_id)
    mid = member_id_for_user(update.effective_user)
    was_registered = hh.get("telegram_ids", {}).get(mid) == update.effective_user.id
    if not any(m.id == mid for m in hh["members"]):
        hh["members"].append(Member(mid, update.effective_user.first_name))
    hh.setdefault("telegram_ids", {})[mid] = update.effective_user.id
    persist_household(chat_id)
    if not was_registered:
        analytics.track("member_joined", chat_id=chat_id, user_id=update.effective_user.id)
    await update.message.reply_text(
        f"🎩 Noted, Master {update.effective_user.first_name}. "
        "Only you may confirm payments when you are the payer."
    )


async def paidby(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_access(update):
        return
    chat_id = update.effective_chat.id
    hh = household(chat_id)
    target = " ".join(ctx.args).strip().lower().replace(" ", "_")
    m = next((x for x in hh["members"] if x.id == target), None)
    if not m:
        await update.message.reply_text("Unknown member. Try /add first.")
        return
    hh["payer_id"] = m.id
    if chat_id in SESSIONS:
        SESSIONS[chat_id]["receipt"].payer_id = m.id
        SESSIONS[chat_id]["payer_tg_id"] = payer_telegram_id(chat_id, m.id)
    persist_household(chat_id)
    await update.message.reply_text(f"🎩 Very good — Master {m.name} settled the bill.")


async def setpay(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_access(update):
        return
    chat_id = update.effective_chat.id
    hh = household(chat_id)
    if len(ctx.args) < 2 or ctx.args[0].lower() not in ("revolut", "monzo"):
        await update.message.reply_text(
            "Usage:  /setpay revolut <revtag>   or   /setpay monzo <username>")
        return
    provider, handle = ctx.args[0].lower(), ctx.args[1].lstrip("@")
    sid = update.effective_user.first_name.lower().replace(" ", "_")
    m = next((x for x in hh["members"] if x.id == sid), None)
    if not m:
        await update.message.reply_text(f"Add yourself first:  /add {update.effective_user.first_name}")
        return
    hh.setdefault("pay", {})[m.id] = {"provider": provider, "handle": handle}
    persist_household(chat_id)
    note = "amount pre-filled" if provider == "monzo" else "opens your Revolut, you type the amount"
    await update.message.reply_text(f"🎩 Noted, Master {m.name} — {provider}/{handle} ({note}).")


async def setemail(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_access(update):
        return
    chat_id = update.effective_chat.id
    hh = household(chat_id)
    if not ctx.args:
        await update.message.reply_text("Usage:  /setemail you@example.com")
        return
    addr = ctx.args[0].strip().lower()
    sid = update.effective_user.first_name.lower().replace(" ", "_")
    m = next((x for x in hh["members"] if x.id == sid), None)
    if not m:
        await update.message.reply_text(f"Add yourself first:  /add {update.effective_user.first_name}")
        return
    hh.setdefault("emails", {})[m.id] = addr
    persist_household(chat_id)
    inbox = os.environ.get("IMAP_TO") or os.environ.get("IMAP_USER", "the Alfred inbox")
    await update.message.reply_text(
        f"🎩 Noted, Master {m.name}. Forward your Tesco receipt to {inbox} and I'll take care of it.")


async def done_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_access(update):
        return
    if not await settle_and_post(update.effective_chat.id, ctx):
        await update.message.reply_text("No active shop to settle.")


async def _send_wrap(update: Update, period: str) -> None:
    text = wrap_render(str(update.effective_chat.id), period)
    try:
        await update.message.reply_text(text, parse_mode="Markdown")
    except BadRequest:
        await update.message.reply_text(text)   # an item name broke Markdown — plain is fine


async def weeklywrap(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_access(update):
        return
    await _send_wrap(update, "week")


async def monthlywrap(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_access(update):
        return
    await _send_wrap(update, "month")


async def alltimewrap(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_access(update):
        return
    await _send_wrap(update, "all")


async def nudge_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """On-demand 'who still owes' reminder for settled-but-unpaid shops."""
    if not await require_access(update):
        return
    text, buttons = wrap_nudge(str(update.effective_chat.id))
    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"paid:{rid}")] for label, rid in buttons]
    ) if buttons else None
    await update.message.reply_text(text, reply_markup=markup)


def due_automatic_nudges(root: str = DATA_DIR, now: datetime | None = None) -> list[tuple[int, dict, int]]:
    """Return one highest due nudge stage per outstanding receipt."""
    now = now or datetime.now(timezone.utc)
    due = []
    base = Path(root)
    if not base.exists():
        return due
    for path in base.glob("*/*.json"):
        if path.name in ("household.json", "memory.json", "access_codes.json"):
            continue
        try:
            chat_id = int(path.parent.name)
            rec = json.loads(path.read_text())
            settled_at = datetime.fromisoformat(rec["settled_at"])
            if settled_at.tzinfo is None:
                settled_at = settled_at.replace(tzinfo=timezone.utc)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
            continue
        if rec.get("status") != "settled" or not rec.get("settlement"):
            continue
        elapsed_hours = (now - settled_at).total_seconds() / 3600
        stage = max((h for h in NUDGE_STAGES if elapsed_hours >= h), default=0)
        if stage > int(rec.get("nudge_stage_hours") or 0):
            due.append((chat_id, rec, stage))
    return due


def automatic_nudge_text(rec: dict, stage: int) -> str:
    name_of = {m["id"]: m["name"] for m in rec.get("members", [])}
    lines = {
        1: ["🎩 A gentle reminder: this shop has been awaiting payment for an hour."],
        2: ["🎩 A firmer reminder: this shop is still awaiting payment after two hours."],
        4: ["🎩 Payment is now four hours overdue. Please settle the account promptly."],
    }[stage]
    for txn in rec["settlement"]:
        lines.append(
            f"• {name_of.get(txn['from'], txn['from'])} → "
            f"{name_of.get(txn['to'], txn['to'])}: {fm(txn['pence'])}"
        )
    return "\n".join(lines)


async def automatic_nudge_loop(app: Application) -> None:
    while True:
        try:
            for chat_id, rec, stage in due_automatic_nudges():
                label = f"🎩 Confirm {rec.get('retailer', 'Tesco')} {fm(rec['total_paid_pence'])} received"
                markup = InlineKeyboardMarkup([[
                    InlineKeyboardButton(label, callback_data=f"paid:{rec['id']}")
                ]])
                await send(app.bot, chat_id, automatic_nudge_text(rec, stage), reply_markup=markup)
                mark_nudge_sent(str(chat_id), rec["id"], stage)
        except Exception as exc:  # noqa: BLE001
            log.warning("automatic nudge loop failed: %s", exc)
        await asyncio.sleep(NUDGE_SCAN_SECONDS)


# ---------- document (the receipt) ----------

async def on_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_access(update):
        return
    chat_id = update.effective_chat.id
    hh = household(chat_id)
    if not hh["members"]:
        await update.message.reply_text("Add the flat first:  /add Rob")
        return

    f = await update.message.document.get_file()
    data = bytes(await f.download_as_bytearray())
    try:
        parsed = tesco.parse(data)
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(f"Couldn't read that file: {exc}")
        return
    if not parsed.items:
        await update.message.reply_text("No items found — is this a Tesco receipt email?")
        return

    uploader_tg = update.effective_user.id
    uploader = member_id_for_user(update.effective_user)
    if any(m.id == uploader for m in hh["members"]):
        hh.setdefault("telegram_ids", {})[uploader] = uploader_tg
        persist_household(chat_id)
    payer_id = uploader if any(m.id == uploader for m in hh["members"]) else hh["members"][0].id
    payer_tg_id = payer_telegram_id(chat_id, payer_id)
    receipt = receipt_from_parsed(parsed, members=hh["members"],
                                  payer_id=payer_id, household_id=str(chat_id))
    analytics.track(
        "receipt_started",
        chat_id=chat_id,
        user_id=update.effective_user.id,
        properties={"items": len(parsed.items), "total_pence": parsed.total_paid_pence},
    )

    if parsed.order_ref and parsed.order_ref in settled_order_refs(str(chat_id)):
        PENDING[chat_id] = (receipt, payer_tg_id)
        await update.message.reply_text(
            f"⚠️ Order {parsed.order_ref} has already been split before.\n"
            "Are you sure you want to split it again?",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Yes, split again", callback_data="dup:yes"),
                InlineKeyboardButton("No", callback_data="dup:no"),
            ]]),
        )
        return

    await start_claiming(chat_id, receipt, ctx, payer_tg_id)


# ---------- callbacks ----------

async def on_dup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_access(update):
        return
    q = update.callback_query
    chat_id = q.message.chat.id
    pending = PENDING.pop(chat_id, None)
    if q.data == "dup:yes" and pending:
        receipt, payer_tg_id = pending
        await q.edit_message_text("🎩 As you wish, sir — splitting it again, fresh.")
        await start_claiming(chat_id, receipt, ctx, payer_tg_id, prefill=False)
    else:
        await q.edit_message_text("🎩 Very good — left as settled.")
    await q.answer()


async def on_item(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Open an item's 'who shared it' menu — edits the single shop message."""
    if not await require_access(update):
        return
    q = update.callback_query
    sess = SESSIONS.get(q.message.chat.id)
    if not sess:
        await q.answer("No active shop — send a receipt first.")
        return
    members = household(q.message.chat.id)["members"]
    idx = int(q.data.split(":")[1])
    if idx >= len(real_items(sess["receipt"])):
        await q.answer()
        return
    await q.answer()
    text, markup = render_item(sess["receipt"], members, idx)
    try:
        await q.edit_message_text(text, reply_markup=markup)
    except BadRequest:
        pass


async def on_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle a member (or everyone) on an item, then re-render the item menu."""
    if not await require_access(update):
        return
    q = update.callback_query
    sess = SESSIONS.get(q.message.chat.id)
    if not sess:
        await q.answer("No active shop.")
        return
    members = household(q.message.chat.id)["members"]
    _, idx_s, who = q.data.split(":", 2)
    idx = int(idx_s)
    items = real_items(sess["receipt"])
    if idx >= len(items):
        await q.answer()
        return
    item = items[idx]
    current = set(item.assigned_to) if item.assigned_to else set()
    if who == "all":
        item.assign(None if len(current) == len(members) else {m.id for m in members})
    else:
        current.symmetric_difference_update({who})
        item.assign(current or None)
    await q.answer()
    text, markup = render_item(sess["receipt"], members, idx)
    try:
        await q.edit_message_text(text, reply_markup=markup)
    except BadRequest:
        pass


async def on_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Return to the item list."""
    if not await require_access(update):
        return
    q = update.callback_query
    sess = SESSIONS.get(q.message.chat.id)
    if not sess:
        await q.answer("No active shop.")
        return
    members = household(q.message.chat.id)["members"]
    await q.answer()
    text, markup = render_list(sess["receipt"], members)
    try:
        await q.edit_message_text(text, reply_markup=markup)
    except BadRequest:
        pass


async def on_nav(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Step Prev/Next through items; past either end drops back to the list."""
    if not await require_access(update):
        return
    q = update.callback_query
    sess = SESSIONS.get(q.message.chat.id)
    if not sess:
        await q.answer("No active shop.")
        return
    members = household(q.message.chat.id)["members"]
    direction, idx_s = q.data.split(":")
    idx = int(idx_s) + (1 if direction == "n" else -1)
    items = real_items(sess["receipt"])
    await q.answer()
    if 0 <= idx < len(items):
        text, markup = render_item(sess["receipt"], members, idx)
    else:
        text, markup = render_list(sess["receipt"], members)
    try:
        await q.edit_message_text(text, reply_markup=markup)
    except BadRequest:
        pass


async def on_done_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_access(update):
        return
    q = update.callback_query
    ok = await settle_and_post(q.message.chat.id, ctx)
    await q.answer("Settled" if ok else "No active shop.")
    if ok:
        try:
            await q.edit_message_text("🎩 Settled — the payer may confirm once payment arrives.")
        except BadRequest:
            pass


async def on_payer(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Cycle who paid the bill, in-flow (no /paidby command needed)."""
    if not await require_access(update):
        return
    q = update.callback_query
    chat_id = q.message.chat.id
    sess = SESSIONS.get(chat_id)
    members = household(chat_id)["members"]
    if not sess or not members:
        await q.answer("No active shop.")
        return
    receipt = sess["receipt"]
    ids = [m.id for m in members]
    cur = receipt.payer_id
    receipt.payer_id = ids[(ids.index(cur) + 1) % len(ids)] if cur in ids else ids[0]
    household(chat_id)["payer_id"] = receipt.payer_id
    sess["payer_tg_id"] = payer_telegram_id(chat_id, receipt.payer_id)
    persist_household(chat_id)
    payer_name = next(m.name for m in members if m.id == receipt.payer_id)
    await q.answer(f"Paid by {payer_name}")
    text, markup = render_list(receipt, members)
    try:
        await q.edit_message_text(text, reply_markup=markup)
    except BadRequest:
        pass


async def on_paid(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Payer confirms the money landed -> the shop is officially settled & paid."""
    if not await require_access(update):
        return
    q = update.callback_query
    chat_id = q.message.chat.id
    rid = q.data.split(":", 1)[1]
    rec = load_receipt(str(chat_id), rid)
    if not rec:
        await q.answer("Can't find that shop.", show_alert=True)
        return
    if not can_confirm_payment(rec, q.from_user.id):
        msg = (
            "Only the payer can confirm they've been paid back."
            if rec.get("payer_tg_id") is not None
            else "The payer must use /join before they can confirm payment."
        )
        await q.answer(msg, show_alert=True)
        return
    if rec.get("status") == "paid":
        await q.answer("Already confirmed ✅")
        return

    rec = mark_receipt_paid(str(chat_id), rid)
    analytics.track("payment_confirmed", chat_id=chat_id, user_id=q.from_user.id)
    payer_name = next((m["name"] for m in rec.get("members", []) if m["id"] == rec.get("payer_id")),
                      "the payer")
    new_text = q.message.text.replace("⏳ awaiting payment", f"✅ paid — thank you, Master {payer_name}")
    try:
        await q.edit_message_text(new_text, reply_markup=None)
    except BadRequest:
        pass
    try:
        await ctx.bot.pin_chat_message(chat_id, q.message.message_id, disable_notification=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not pin paid summary in chat %s: %s", chat_id, exc)
        await q.answer(
            "Payment confirmed, but I need admin permission to pin messages.",
            show_alert=True,
        )
        return
    await q.answer("Payment confirmed and summary pinned 🎩")


# ---------- settlement ----------

async def settle_and_post(chat_id: int, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    sess = SESSIONS.get(chat_id)
    if not sess:
        return False
    receipt = sess["receipt"]
    members = household(chat_id)["members"]
    shares = compute_shares(receipt)
    txns = settle_receipt(shares, receipt.payer_id)

    payer_tg_id = sess.get("payer_tg_id")
    for it in receipt.items:
        if not it.is_overhead and it.assigned_to:
            mem.remember(receipt.household_id, it.name, set(it.assigned_to))
    save_receipt(receipt, txns, status="settled", payer_tg_id=payer_tg_id)
    analytics.track(
        "settlement_completed",
        chat_id=chat_id,
        properties={
            "members": len(members),
            "items": len(real_items(receipt)),
            "total_pence": receipt.stated_total_pence,
            "fully_assigned": shares.fully_assigned,
        },
    )

    name_of = {m.id: m.name for m in members}
    payer_name = name_of.get(receipt.payer_id, "the payer")
    lines = [f"🎩 The accounts, sir — {receipt.retailer} {short_date(receipt.created_at)} · "
             f"{fm(receipt.stated_total_pence)} · paid by {payer_name}"]
    lines += [f"{name_of[t.from_id]} → {name_of[t.to_id]}: {fm(t.pence)}" for t in txns] or ["all square 🎉"]
    if not shares.fully_assigned:
        lines.append(f"({len(shares.unassigned_items)} item(s) left unclaimed)")
    lines.append("\nStatus: ⏳ awaiting payment")

    rows = []
    pay = household(chat_id).get("pay", {})
    for t in txns:
        info = pay.get(t.to_id)
        if info:
            tip = "" if info["provider"] == "monzo" else " (enter amount)"
            rows.append([InlineKeyboardButton(
                f"Pay {name_of[t.to_id]} {fm(t.pence)}{tip}",
                url=pay_link(info["provider"], info["handle"], t.pence, note=f"Alfred {receipt.retailer}"))])
    if txns:
        rows.append([InlineKeyboardButton(
            f"🎩 {payer_name}: confirm received", callback_data=f"paid:{receipt.id}")])
    markup = InlineKeyboardMarkup(rows) if rows else None
    await send(ctx.bot, chat_id, "\n".join(lines), reply_markup=markup)
    SESSIONS.pop(chat_id, None)
    return True


# ---------- wiring ----------

async def handle_inbound_email(app: Application, frm: str, raw: bytes) -> None:
    """A forwarded receipt arrived -> route to the sender's household and start claiming."""
    hit = find_household_by_email(frm)
    if not hit:
        log.info("inbound email from unknown sender %s — ignored", frm)
        return
    household_id, member_id = hit
    chat_id = int(household_id)
    if not (OPEN_ACCESS or access.is_activated(chat_id)):
        await app.bot.send_message(
            chat_id, "🎩 This household needs an access pass. Use /activate CODE."
        )
        return
    hh = household(chat_id)
    try:
        parsed = tesco.parse(raw)
    except Exception as exc:  # noqa: BLE001
        log.warning("inbound parse failed: %s", exc)
        return
    if not parsed.items:
        await app.bot.send_message(
            chat_id, "🎩 I couldn't read that forwarded receipt, sir — do forward the Tesco order email itself.")
        return
    if parsed.order_ref and parsed.order_ref in settled_order_refs(household_id):
        await app.bot.send_message(
            chat_id, f"🎩 Order {parsed.order_ref} was already split, sir — ignoring the duplicate.")
        return
    receipt = receipt_from_parsed(parsed, members=hh["members"],
                                  payer_id=member_id, household_id=household_id)
    await start_claiming(
        chat_id, receipt, SimpleNamespace(bot=app.bot),
        payer_tg_id=payer_telegram_id(chat_id, member_id),
    )


async def email_poll_loop(app: Application) -> None:
    normal_delay = int(os.environ.get("IMAP_POLL_SECONDS", "5"))
    delay = normal_delay
    while True:
        try:
            for frm, raw in await asyncio.to_thread(fetch_new_receipts):
                try:
                    await handle_inbound_email(app, frm, raw)
                except Exception as exc:  # noqa: BLE001
                    log.warning("inbound handling failed: %s", exc)
            delay = normal_delay
        except Exception as exc:  # noqa: BLE001
            log.warning("email poll error: %s", exc)
            delay = min(max(delay * 2, 30), 300)
        await asyncio.sleep(delay)


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.warning("handler error: %s", ctx.error)


async def post_init(app: Application) -> None:
    me = await app.bot.get_me()
    log.info("Alfred is live as @%s (memory: %s)", me.username, type(mem).__name__)
    await app.bot.set_my_commands([
        BotCommand("demo", "Try a sample shop — no receipt needed"),
        BotCommand("add", "Add a housemate"),
        BotCommand("join", "Register yourself (verify payer)"),
        BotCommand("done", "Settle the current shop"),
        BotCommand("nudge", "Chase settled-but-unpaid shops"),
        BotCommand("weeklywrap", "This week: spend + awards"),
        BotCommand("monthlywrap", "This month: spend + awards"),
        BotCommand("alltimewrap", "All-time: spend + awards"),
        BotCommand("setpay", "Set your Monzo/Revolut handle"),
        BotCommand("setemail", "Forward receipts to Alfred by email"),
        BotCommand("help", "How Alfred works"),
    ])
    if imap_enabled():
        asyncio.create_task(email_poll_loop(app), name="email-poll-loop")
        log.info("email intake on (%s)", os.environ.get("IMAP_USER"))
    asyncio.create_task(automatic_nudge_loop(app), name="automatic-nudge-loop")
    log.info("automatic payment nudges on (1h, 2h, 4h)")


def main() -> None:
    app = (
        Application.builder()
        .token(os.environ["TELEGRAM_BOT_TOKEN"])
        .post_init(post_init)
        .concurrent_updates(True)        # taps don't block each other on a slow link
        .connect_timeout(20).read_timeout(20).write_timeout(20).pool_timeout(20)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("demo", demo_cmd))
    app.add_handler(CommandHandler("activate", activate))
    app.add_handler(CommandHandler("add", add_member))
    app.add_handler(CommandHandler("join", join))
    app.add_handler(CommandHandler("paidby", paidby))
    app.add_handler(CommandHandler("setpay", setpay))
    app.add_handler(CommandHandler("setemail", setemail))
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CommandHandler("weeklywrap", weeklywrap))
    app.add_handler(CommandHandler("monthlywrap", monthlywrap))
    app.add_handler(CommandHandler("wrap", monthlywrap))   # alias
    app.add_handler(CommandHandler("alltimewrap", alltimewrap))
    app.add_handler(CommandHandler("nudge", nudge_cmd))
    app.add_handler(CallbackQueryHandler(on_dup, pattern=r"^dup:"))
    app.add_handler(CallbackQueryHandler(on_paid, pattern=r"^paid:"))
    app.add_handler(CallbackQueryHandler(on_done_cb, pattern=r"^done$"))
    app.add_handler(CallbackQueryHandler(on_payer, pattern=r"^payer$"))
    app.add_handler(CallbackQueryHandler(on_item, pattern=r"^i:"))
    app.add_handler(CallbackQueryHandler(on_toggle, pattern=r"^t:"))
    app.add_handler(CallbackQueryHandler(on_back, pattern=r"^b$"))
    app.add_handler(CallbackQueryHandler(on_nav, pattern=r"^[np]:"))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_error_handler(on_error)
    app.run_polling()  # PTB retries getUpdates internally; an external supervisor handles restarts


if __name__ == "__main__":
    main()
