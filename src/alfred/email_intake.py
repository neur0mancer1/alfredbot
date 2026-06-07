"""Email intake — forward your receipt to a mailbox, Alfred picks it up via IMAP.

Far less faff than downloading a .eml and uploading it: a member registers their
address (/setemail), forwards the Tesco receipt to the Alfred inbox, and we route
it to their household by sender. This module only does the *fetch*; parsing and
routing live in the bot. Inert unless IMAP_USER / IMAP_PASS are set.
"""

from __future__ import annotations

import email
import email.utils
import imaplib
import os
from email import policy


def _config() -> tuple[str, str, str] | None:
    host = os.environ.get("IMAP_HOST", "imap.gmail.com")
    user = os.environ.get("IMAP_USER")
    pw = os.environ.get("IMAP_PASS")
    return (host, user, pw) if user and pw else None


def imap_enabled() -> bool:
    return _config() is not None


def fetch_new_receipts() -> list[tuple[str, bytes]]:
    """Return [(from_address, raw_message_bytes)] for UNSEEN mail; marks them seen.

    Synchronous (imaplib) — call it from a thread so it doesn't block the bot loop.
    """
    cfg = _config()
    if not cfg:
        return []
    host, user, pw = cfg
    out: list[tuple[str, bytes]] = []
    with imaplib.IMAP4_SSL(host) as M:
        M.login(user, pw)
        M.select("INBOX")
        # If IMAP_TO is set (e.g. you+alfred@gmail.com), only read mail sent there,
        # so a shared/personal inbox's normal mail is never touched.
        to = os.environ.get("IMAP_TO")
        criteria = f'(UNSEEN TO "{to}")' if to else "UNSEEN"
        typ, data = M.search(None, criteria)
        if typ != "OK":
            return []
        for num in data[0].split():
            typ, msgdata = M.fetch(num, "(RFC822)")
            if typ != "OK" or not msgdata or not msgdata[0]:
                continue
            raw = msgdata[0][1]
            msg = email.message_from_bytes(raw, policy=policy.default)
            frm = email.utils.parseaddr(msg.get("from", ""))[1].lower()
            out.append((frm, raw))
            M.store(num, "+FLAGS", "\\Seen")
    return out
