# Module role: what the company's number shows the people who write to it — read receipts,
# «typing…» and the wait before a reply — for one device: Settings → Number behaviour, or the
# device's own choices when it has them (owner, 2026-11-19, after Top Land's complaint that every
# received message showed as read and left the company's phone without a notification).
#
# Nothing is marked read unless chosen: a site that never chose, or holds a value the list no longer
# has, marks nothing.

from __future__ import annotations

import random

import frappe
from frappe.utils import cint

MARK_READ_MODES = ("Do not mark as read", "When the chat is opened", "When a command replies", "Every received message")
DEFAULT_MARK_READ_MODE = "Do not mark as read"
OWN_SETTINGS = "Own settings"
#: A person types about five characters a second; the wait never goes beyond this, whatever the reply.
TYPING_CHARACTERS_PER_SECOND = 5
MAX_WAIT_BEFORE_REPLY_SECONDS = 15


def for_device(device: str | None) -> frappe._dict:
	"""`{mark_read_mode, typing_indicator, typing_lasts_by_reply_length, reply_delay_min_seconds,
	reply_delay_max_seconds}` for `device`: its own when it has them, else Settings'."""
	own = (
		frappe.db.get_value(
			"WhatsApp Device",
			device,
			[
				"number_behaviour",
				"mark_read_mode",
				"typing_indicator",
				"typing_lasts_by_reply_length",
				"reply_delay_min_seconds",
				"reply_delay_max_seconds",
			],
			as_dict=True,
		)
		if device
		else None
	)
	if own and own.number_behaviour == OWN_SETTINGS:
		source = own
	else:
		from whatsapp_next.services.settings import stored_or_default

		source = frappe._dict(
			mark_read_mode=stored_or_default("mark_read_mode"),
			typing_indicator=stored_or_default("typing_indicator"),
			typing_lasts_by_reply_length=stored_or_default("typing_lasts_by_reply_length"),
			reply_delay_min_seconds=stored_or_default("reply_delay_min_seconds"),
			reply_delay_max_seconds=stored_or_default("reply_delay_max_seconds"),
		)
	mode = source.mark_read_mode if source.mark_read_mode in MARK_READ_MODES else DEFAULT_MARK_READ_MODE
	low, high = sorted((max(0, cint(source.reply_delay_min_seconds)), max(0, cint(source.reply_delay_max_seconds))))
	return frappe._dict(
		mark_read_mode=mode,
		typing_indicator=bool(cint(source.typing_indicator)),
		typing_lasts_by_reply_length=bool(cint(source.typing_lasts_by_reply_length)),
		reply_delay_min_seconds=low,
		reply_delay_max_seconds=high,
	)


DEVICE_FIELDS = (
	"number_behaviour",
	"mark_read_mode",
	"typing_indicator",
	"typing_lasts_by_reply_length",
	"reply_delay_min_seconds",
	"reply_delay_max_seconds",
)


def get_device_choices(device: str) -> dict:
	"""The device's own number-behaviour fields, for its window; a reading value the list no
	longer has shows as the default."""
	choices = frappe.db.get_value("WhatsApp Device", device, list(DEVICE_FIELDS), as_dict=True) or frappe._dict()
	if choices.get("mark_read_mode") not in MARK_READ_MODES:
		choices["mark_read_mode"] = DEFAULT_MARK_READ_MODE
	return choices


def save_device_choices(device: str, values: dict) -> dict:
	"""Save the device's number behaviour — only `DEVICE_FIELDS`, through the document so its
	options are checked — and return them as stored."""
	doc = frappe.get_doc("WhatsApp Device", device)
	doc.update({k: values[k] for k in DEVICE_FIELDS if k in values})
	doc.save(ignore_permissions=True)
	return get_device_choices(device)


def keep_mark_read_mode_on_the_list(doc) -> None:
	"""A value the list no longer has (the old «Never», or any other) becomes the default, so a
	document saved with it is never refused; called from Settings' and the device's `validate`."""
	if doc.get("mark_read_mode") not in MARK_READ_MODES:
		doc.mark_read_mode = DEFAULT_MARK_READ_MODE


def seconds_to_wait_before_reply(device: str | None, reply_text: str | None) -> float:
	"""How long to wait before sending a reply: a random number of seconds between the chosen two,
	or longer when «typing…» lasts as long as a person would need to type the reply; never more than
	`MAX_WAIT_BEFORE_REPLY_SECONDS`."""
	behaviour = for_device(device)
	wait = random.uniform(behaviour.reply_delay_min_seconds, behaviour.reply_delay_max_seconds)
	if behaviour.typing_indicator and behaviour.typing_lasts_by_reply_length:
		wait = max(wait, len(reply_text or "") / TYPING_CHARACTERS_PER_SECOND)
	return min(wait, MAX_WAIT_BEFORE_REPLY_SECONDS)
