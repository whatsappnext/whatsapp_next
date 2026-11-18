# Module role: creating WhatsApp groups from the product's screens (Contacts, WhatsApp Numbers,
# Contact Groups), and managing one from its row in WhatsApp Numbers: its members and join
# requests, adding and removing members, making or unmaking admins. The device creates the group through the provider and becomes its admin; the
# group then exists here as a WhatsApp Number of type Group, and — when it was created for a
# Contact — as one of that contact's numbers too. Plan feature `allow_groups`.

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from whatsapp_next.exceptions import WANotFoundError, WAStateConflictError, WAValidationError
from whatsapp_next.services import audit, plan_features
from whatsapp_next.services.guards import status_writer
from whatsapp_next.services.phone import classify

MAX_MEMBERS = 256
MAX_NAME = 100
#: Numbers added to a group at a time, a new one's members included: WhatsApp bans numbers that
#: add many people at once (platform review, 2026-11-18). The platform refuses more.
MAX_ADDS_AT_ONCE = 50


def members_from(
	source_doctype: str, names: list[str], max_records: int = MAX_MEMBERS
) -> list[dict[str, Any]]:
	"""The WhatsApp numbers behind a selection, to prefill the dialog: Contacts (their numbers),
	WhatsApp Contact Groups (their members) or WhatsApp Numbers (individual ones). Read through the
	caller's permissions; each row `{phone_e164, display_name}`. A selection of more than
	`max_records` records is refused, never cut: the first 256 used to be read silently, so the
	number check (5,000 numbers) left the rest of a large selection unchecked."""
	names = [n for n in names or [] if n]
	if len(names) > max_records:
		frappe.throw(_("Select at most {0} records at a time").format(max_records), WAValidationError)
	out: dict[str, str | None] = {}
	if source_doctype == "Contact":
		from whatsapp_next.services import permissions

		# only the contacts this user may read: through the product's contact role (all of them),
		# else one by one through Frappe's own permission (`get_all` itself checks nothing)
		if not permissions.can_elevate("read"):
			names = [n for n in names if frappe.has_permission("Contact", "read", n)]
		for row in frappe.get_all(
			"Contact Phone",
			filters={"parenttype": "Contact", "parent": ("in", names or [""])},
			fields=["parent", "wa_phone_e164"],
		):
			kind, key = classify(row.wa_phone_e164)
			if kind == "Individual":
				out.setdefault(key, frappe.db.get_value("Contact", row.parent, "full_name"))
	elif source_doctype == "WhatsApp Contact Group":
		for name in names:
			frappe.has_permission("WhatsApp Contact Group", "read", name, throw=True)
		for row in frappe.get_all(
			"WhatsApp Contact Group Member",
			filters={"parent": ("in", names)},
			fields=["phone_e164", "display_name"],
			ignore_permissions=True,
		):
			kind, key = classify(row.phone_e164)
			if kind == "Individual":
				out.setdefault(key, row.display_name)
	elif source_doctype == "WhatsApp Number":
		for row in frappe.get_list(
			"WhatsApp Number",
			filters={"name": ("in", names), "number_type": "Individual"},
			fields=["name", "display_name"],
		):
			out.setdefault(row.name, row.display_name)
	else:
		frappe.throw(_("Members cannot be taken from {0}").format(source_doctype), WAValidationError)
	return [{"phone_e164": k, "display_name": v} for k, v in out.items()]


def create_whatsapp_group(
	device: str,
	name: str,
	members: list[str],
	description: str | None = None,
	contact: str | None = None,
	user: str | None = None,
) -> dict[str, Any]:
	"""Create the group on `device` with `members` (numbers); returns `{jid, name, members[{phone,
	status, jid}], invite_link, saved_to_contact}`. Audited `WhatsApp Group Created`."""
	from whatsapp_next.providers import registry

	plan_features.require_groups()
	name = (name or "").strip()
	if not name or len(name) > MAX_NAME:
		frappe.throw(
			_("The group name is required, at most {0} characters").format(MAX_NAME), WAValidationError
		)
	keys = []
	for m in members or []:
		kind, key = classify(m)
		if kind == "Individual" and key not in keys:
			keys.append(key)
	if not keys:
		frappe.throw(_("Choose at least one number to add"), WAValidationError)
	if len(keys) > MAX_ADDS_AT_ONCE:
		# a new group's members are added at once too (platform review, 2026-11-18)
		frappe.throw(
			_("Add at most {0} numbers at a time: WhatsApp bans numbers that add many people at once.").format(
				MAX_ADDS_AT_ONCE
			),
			WAValidationError,
		)
	row = frappe.db.get_value(
		"WhatsApp Device", device, ["platform_device", "status", "disabled"], as_dict=True
	)
	if not row:
		frappe.throw(_("WhatsApp Device {0} not found").format(device), WAValidationError)
	if cint(row.disabled):
		frappe.throw(_("{0} is disabled").format(device), WAStateConflictError)
	if row.status != "Connected":
		frappe.throw(_("{0} is not connected").format(device), WAStateConflictError)
	if contact:
		# checked before the group exists: a refusal after it left a group on WhatsApp that the
		# site rolled back and no longer knew of
		from whatsapp_next.services import permissions

		permissions.require("write")
		if not frappe.db.exists("Contact", contact):
			frappe.throw(_("Contact {0} not found").format(contact), WANotFoundError)

	created = registry.get_provider().create_group(
		row.platform_device, name, keys, (description or "").strip() or None
	)
	kind, jid = classify(created.jid)
	if kind != "Group":
		frappe.throw(_("The group was not created"), WAStateConflictError)
	_save_number(jid, created.name or name, device)
	saved = False
	if contact:
		from whatsapp_next.services import permissions

		saved = permissions.add_group_to_contact(contact, jid, user=user)
	audit.record_audit_entry(
		"WhatsApp Group Created",
		reference=("WhatsApp Number", jid),
		target=("Contact", contact) if contact else None,
		count=len(keys),
		details={"device": device, "statuses": _count_members_by_status(created.members)},
		user=user,
	)
	return {
		"jid": jid,
		"name": created.name or name,
		"members": [asdict(m) for m in created.members],
		"invite_link": created.invite_link,
		"saved_to_contact": saved,
	}


def add_group_members(
	group: str, members: list[str], device: str | None = None, user: str | None = None
) -> dict[str, Any]:
	"""Add `members` (numbers) to the WhatsApp group `group` (a WhatsApp Number of type Group) through
	`device`, which must be one of its admins; by default the device the group was last seen on.
	Returns `{jid, name, device, members[{phone, status, jid}], invite_link}`: a row per number, in
	order. A number off Settings → "Send only to" is not asked for (`not_allowed`). Audited
	`WhatsApp Group Members Added` (owner, 2026-11-17)."""
	from whatsapp_next.providers import registry
	from whatsapp_next.services.settings import chat_in

	plan_features.require_groups()
	jid, row = _group_row(group)
	keys = []
	for m in members or []:
		m_kind, key = classify(m)
		if m_kind == "Individual" and key not in keys:
			keys.append(key)
	if not keys:
		frappe.throw(_("Choose at least one number to add"), WAValidationError)
	if len(keys) > MAX_ADDS_AT_ONCE:
		frappe.throw(
			_("Add at most {0} numbers at a time: WhatsApp bans numbers that add many people at once.").format(
				MAX_ADDS_AT_ONCE
			),
			WAValidationError,
		)
	device, device_row = _group_device(row, device)

	allowed = [k for k in keys if chat_in("send_only_to", k)]
	result = (
		registry.get_provider().add_group_members(device_row.platform_device, jid, allowed)
		if allowed
		else None
	)
	by_phone = {m.phone.lstrip("+"): asdict(m) for m in (result.members if result else [])}
	rows = [
		by_phone.get(k.lstrip("+")) or {"phone": k.lstrip("+"), "status": "not_allowed" if k not in allowed else "failed", "jid": None}
		for k in keys
	]
	audit.record_audit_entry(
		"WhatsApp Group Members Added",
		reference=("WhatsApp Number", jid),
		count=len(keys),
		details={"device": device, "statuses": _count_members_by_status([frappe._dict(r) for r in rows])},
		user=user,
	)
	return {
		"jid": jid,
		"name": row.display_name or jid,
		"device": device,
		"members": rows,
		"invite_link": result.invite_link if result else None,
	}


def _group_row(group: str):
	"""`(jid, row)` of a WhatsApp Number of type Group, or `WANotFoundError`."""
	kind, jid = classify(group)
	row = (
		frappe.db.get_value(
			"WhatsApp Number", jid, ["number_type", "display_name", "synced_device", "last_device"], as_dict=True
		)
		if kind == "Group"
		else None
	)
	if not row or row.number_type != "Group":
		frappe.throw(_("{0} is not a WhatsApp group").format(group), WANotFoundError)
	return jid, row


def _group_device(row, device: str | None):
	"""`(device, its row)`: the one asked for, else the device the group was seen on; connected."""
	device = device or row.synced_device or row.last_device
	device_row = (
		frappe.db.get_value(
			"WhatsApp Device", device, ["platform_device", "status", "disabled", "phone_e164"], as_dict=True
		)
		if device
		else None
	)
	if not device_row:
		frappe.throw(_("Choose the device that administers the group"), WAValidationError)
	if cint(device_row.disabled):
		frappe.throw(_("{0} is disabled").format(device), WAStateConflictError)
	if device_row.status != "Connected":
		frappe.throw(_("{0} is not connected").format(device), WAStateConflictError)
	return device, device_row


#: what the group window may do to a member, as the provider names it
MEMBER_ACTIONS = ("remove", "make_admin", "remove_admin", "approve", "reject")


def group_members(group: str, device: str | None = None) -> dict[str, Any]:
	"""A WhatsApp group's members and join requests, read through `device` (by default the one the
	group was seen on): `{jid, name, device, device_is_admin, members[...], requests[...]}`, each
	person `{phone_e164, jid, display_name, contact, is_admin, is_super_admin}`. A member WhatsApp
	shows by a hidden id only takes the number this site learnt for that id, when it has one
	(owner, 2026-11-17)."""
	from whatsapp_next.providers import registry

	plan_features.require_groups()
	jid, row = _group_row(group)
	device, device_row = _group_device(row, device)
	details = registry.get_provider().get_group_details(device_row.platform_device, jid)
	own = (device_row.phone_e164 or "").lstrip("+")

	def person(p) -> dict[str, Any]:
		phone = f"+{p.phone}" if p.phone else _number_of_hidden_id(p.jid)
		known = frappe.db.get_value("WhatsApp Number", phone or p.jid, ["display_name", "contact"], as_dict=True) if (phone or p.jid) else None
		return {
			"phone_e164": phone,
			"jid": p.jid,
			"display_name": (known or {}).get("display_name"),
			"contact": (known or {}).get("contact"),
			"is_admin": bool(getattr(p, "is_admin", False)),
			"is_super_admin": bool(getattr(p, "is_super_admin", False)),
			"is_this_device": bool(phone and own and phone.lstrip("+") == own),
			"requested_at": getattr(p, "requested_at", None),
		}

	members = [person(p) for p in details.members]
	return {
		"jid": jid,
		"name": details.name or row.display_name or jid,
		"device": device,
		"device_is_admin": any(m["is_admin"] and m["is_this_device"] for m in members),
		"members": sorted(members, key=lambda m: (not m["is_super_admin"], not m["is_admin"], m["display_name"] or "~")),
		"requests": [person(r) for r in details.requests],
	}


def _number_of_hidden_id(jid: str | None) -> str | None:
	"""The real number this site learnt for a hidden id (`…@lid`), see `numbers_materializer`."""
	if not (jid or "").endswith("@lid"):
		return None
	from whatsapp_next.services.numbers_materializer import known_number_of_hidden_id

	user = jid.split("@")[0].split(":")[0]
	phone = frappe.db.get_value("WhatsApp Number", f"{user}@lid", "phone") or known_number_of_hidden_id(jid)
	return phone if (phone or "").startswith("+") else None


def change_group_members(
	group: str, action: str, members: list[str], device: str | None = None, user: str | None = None
) -> dict[str, Any]:
	"""`action` on `members` (numbers) of a WhatsApp group through `device`, which must administer
	it: `remove`, `make_admin`, `remove_admin`, or `approve` / `reject` join requests. Returns
	`{jid, action, device, members[{phone, status}]}` with `done`, `failed` or `not_allowed` (off
	Settings → "Send only to", never asked for). Audited `WhatsApp Group Members Changed`."""
	from whatsapp_next.providers import registry
	from whatsapp_next.services.settings import chat_in

	plan_features.require_groups()
	if action not in MEMBER_ACTIONS:
		frappe.throw(_("Unknown change: {0}").format(action), WAValidationError)
	jid, row = _group_row(group)
	keys = []
	for m in members or []:
		m_kind, key = classify(m)
		if m_kind == "Individual" and key not in keys:
			keys.append(key)
	if not keys:
		frappe.throw(_("Choose at least one member"), WAValidationError)
	device, device_row = _group_device(row, device)
	allowed = [k for k in keys if chat_in("send_only_to", k)]
	done = (
		registry.get_provider().change_group_members(device_row.platform_device, jid, action, allowed)
		if allowed
		else []
	)
	by_phone = {m.phone.lstrip("+"): m.status for m in done}
	rows = [
		{
			"phone": k.lstrip("+"),
			"status": by_phone.get(k.lstrip("+")) or ("not_allowed" if k not in allowed else "failed"),
		}
		for k in keys
	]
	audit.record_audit_entry(
		"WhatsApp Group Members Changed",
		reference=("WhatsApp Number", jid),
		count=len(keys),
		details={"device": device, "action": action, "statuses": _count_members_by_status([frappe._dict(r) for r in rows])},
		user=user,
	)
	return {"jid": jid, "action": action, "device": device, "members": rows}


def _save_number(jid: str, name: str, device: str) -> None:
	"""The group's WhatsApp Number row, as the device sync would write it."""
	now = now_datetime()
	if frappe.db.exists("WhatsApp Number", jid):
		frappe.db.set_value(
			"WhatsApp Number",
			jid,
			{"display_name": name, "saved_name": name, "synced_device": device, "synced_at": now},
		)
		return
	with status_writer():
		frappe.get_doc(
			{
				"doctype": "WhatsApp Number",
				"phone_e164": jid,
				"jid": jid,
				"number_type": "Group",
				"display_name": name,
				"saved_name": name,
				"synced_device": device,
				"synced_at": now,
				"link_status": "Not Linked",
			}
		).insert(ignore_permissions=True)


def _count_members_by_status(members) -> dict[str, int]:
	out: dict[str, int] = {}
	for member in members:
		out[member.status] = out.get(member.status, 0) + 1
	return out
