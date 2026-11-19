# Module role: what the sender sees while their message is handled — read receipts (blue ticks)
# and "typing…" — per the number's behaviour (`services/number_behaviour`: Settings → Number
# behaviour, or the device's own). Best-effort: a receipt that cannot be sent never stops a command,
# a reply or a chat screen; nothing is sent for simulated or imported messages; a message is marked
# read once only (`read_receipt_sent`).

from __future__ import annotations

from collections import defaultdict

import frappe
from frappe.utils import cint

from whatsapp_next.services import number_behaviour

INBOUND = "WhatsApp Inbound Message"
#: The platform marks at most this many messages of one chat in one request.
MARK_READ_BATCH = 50


def _get_inbound_row_for_receipt(inbound_name: str | None):
	if not inbound_name:
		return None
	row = frappe.db.get_value(
		INBOUND,
		inbound_name,
		[
			"name",
			"device",
			"chat_jid",
			"sender_jid",
			"jid",
			"is_group",
			"provider_message_id",
			"is_simulated",
			"is_history",
			"read_receipt_sent",
		],
		as_dict=True,
	)
	if not row or cint(row.is_simulated) or cint(row.is_history) or not row.provider_message_id:
		return None
	row.chat = row.chat_jid or row.sender_jid or row.jid
	return row if row.chat and "@" in row.chat else None


def _platform_device(device: str | None) -> str | None:
	return frappe.db.get_value("WhatsApp Device", device, "platform_device") if device else None


def _send_receipt(platform_device: str, chat: str, rows: list, sender_jid: str | None) -> bool:
	"""One request for `rows` of one chat (and one sender in a group); flags them on success."""
	from whatsapp_next.providers import registry

	try:
		registry.get_provider().mark_read(
			platform_device, chat, [r.provider_message_id for r in rows], sender_jid
		)
	except Exception:
		frappe.log_error(
			title="WhatsApp read receipt not sent",
			message=f"inbound={', '.join(r.name for r in rows)}",
		)
		return False
	frappe.db.set_value(INBOUND, {"name": ("in", [r.name for r in rows])}, "read_receipt_sent", 1, update_modified=False)
	return True


def mark_read(inbound_name: str) -> bool:
	"""Send the read receipt of one received message, once; returns True when sent."""
	row = _get_inbound_row_for_receipt(inbound_name)
	if not row or cint(row.read_receipt_sent):
		return False
	platform_device = _platform_device(row.device)
	if not platform_device:
		return False
	return _send_receipt(platform_device, row.chat, [row], row.sender_jid if cint(row.is_group) else None)


def mark_conversation_read(key: str, device: str | None = None) -> dict:
	"""The chat with `key` (E.164 or group JID) was opened and its messages shown: when the number
	marks «When the chat is opened», its received messages not yet marked are marked — the newest
	`MARK_READ_BATCH` per request, one request per device and sender. Never raises."""
	filters: dict = {"read_receipt_sent": 0, "is_simulated": 0, "is_history": 0, "provider_message_id": ("is", "set")}
	if str(key or "").endswith("@g.us"):
		filters.update(chat_jid=key, is_group=1)
	else:
		filters.update(phone_e164=key, is_group=0)
	if device:
		filters["device"] = device if isinstance(device, str) else ("in", list(device))
	rows = frappe.get_all(
		INBOUND,
		filters=filters,
		fields=["name", "device", "chat_jid", "sender_jid", "jid", "is_group", "provider_message_id"],
		order_by="creation desc",
		limit=MARK_READ_BATCH,
	)
	groups: dict[tuple, list] = defaultdict(list)
	for row in rows:
		if number_behaviour.for_device(row.device).mark_read_mode != "When the chat is opened":
			continue
		chat = row.chat_jid or row.sender_jid or row.jid
		if not chat or "@" not in chat:
			continue
		groups[(row.device, chat, row.sender_jid if cint(row.is_group) else None)].append(row)
	marked = 0
	for (row_device, chat, sender_jid), chat_rows in groups.items():
		platform_device = _platform_device(row_device)
		if platform_device and _send_receipt(platform_device, chat, chat_rows, sender_jid):
			marked += len(chat_rows)
	return {"marked": marked}


def send_typing_indicator(inbound_name: str, state: str = "composing") -> bool:
	"""Show "typing…" in the chat of a received message, when the number shows it."""
	from whatsapp_next.providers import registry

	row = _get_inbound_row_for_receipt(inbound_name)
	if not row or not number_behaviour.for_device(row.device).typing_indicator:
		return False
	platform_device = _platform_device(row.device)
	if not platform_device:
		return False
	try:
		registry.get_provider().send_presence(platform_device, row.chat, state)
		return True
	except Exception:
		return False  # a missing "typing…" is not worth an error log


def _mark_read_mode(inbound_name: str) -> str:
	device = frappe.db.get_value(INBOUND, inbound_name, "device") if inbound_name else None
	return number_behaviour.for_device(device).mark_read_mode


def on_received(inbound_name: str) -> None:
	"""After a message is stored: queue its receipt when the number marks every received message."""
	if _mark_read_mode(inbound_name) == "Every received message":
		frappe.enqueue(
			"whatsapp_next.services.receipts.mark_read",
			inbound_name=inbound_name,
			queue="short",
			enqueue_after_commit=True,
			job_id=f"wa-read-{inbound_name}",
			deduplicate=True,
		)


def on_command_replied(inbound_name: str) -> None:
	"""After a command answered: its message is marked read when the number marks then."""
	if _mark_read_mode(inbound_name) == "When a command replies":
		mark_read(inbound_name)
