# Module role: WhatsApp Contact Group controller — member rows normalized to E.164, duplicate guard,
# `member_count` / `members_changed_at` maintenance, and the global-blacklist disable guard (fields.md §12).

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from whatsapp_next.exceptions import WAStateConflictError, WAValidationError
from whatsapp_next.services.phone import set_phone_pair

MEMBER_COMPARE_FIELDS = ("phone_e164", "display_name", "contact", "source_type", "note")


class WhatsAppContactGroup(Document):
	"""A named list of phone numbers used by campaigns, commands and the global blacklist."""

	def validate(self) -> None:
		"""Normalize members, reject duplicates, maintain counters, guard the global blacklist."""
		self.check_access_group_editor()
		self.normalize_members()
		self.check_duplicate_members()
		self.update_member_stats()
		self.check_blacklist_disable()

	def check_access_group_editor(self) -> None:
		"""A group that decides access — a blacklist, the global blacklist, a command's allowed or
		blocked group — is changed by a Manager only. An Agent or a Contact User may write groups,
		and could add an outside number to a sensitive command's allowed group (security review
		2026-11-16). Checked whatever the path, the picker's own included (it saves with
		`ignore_permissions` after its write check)."""
		if self.is_new() or set(frappe.get_roles()) & ACCESS_GROUP_EDITORS:
			return
		reason = access_group_reason(self)
		if reason and self.flags.wa_blacklist_toggle and not frappe.get_all(
			"WhatsApp Command",
			or_filters={"allowed_group": self.name, "blocked_group": self.name},
			pluck="name",
			limit=1,
		):
			# blocking or unblocking one number (`permissions.toggle_blacklist`, audited) is the
			# blacklist change these roles may make; a command's access group stays a Manager's
			return
		if reason:
			frappe.throw(
				_("{0} {1}: only a WhatsApp Manager may change it").format(self.name, reason),
				frappe.PermissionError,
			)

	def normalize_members(self) -> None:
		"""Fill `phone_e164` on every row (required) and stamp `added_by` / `added_on` on new rows."""
		for row in self.get("members") or []:
			set_phone_pair(row, "phone", "phone_e164", required=True)
			if not row.added_by:
				row.added_by = frappe.session.user
			if not row.added_on:
				row.added_on = now_datetime()

	def check_duplicate_members(self) -> None:
		"""Reject the same `phone_e164` appearing twice in the table."""
		seen: set[str] = set()
		for row in self.get("members") or []:
			if row.phone_e164 in seen:
				frappe.throw(
					_("Row {0}: number {1} is already a member of this group").format(
						row.idx, row.phone_e164
					),
					WAValidationError,
				)
			seen.add(row.phone_e164)

	def update_member_stats(self) -> None:
		"""Set `member_count`; bump `members_changed_at` when the member set changed."""
		self.member_count = len(self.get("members") or [])
		if self.is_new():
			if self.member_count:
				self.members_changed_at = now_datetime()
			return
		before = self.get_doc_before_save()
		if before is None or _member_signature(before) != _member_signature(self):
			self.members_changed_at = now_datetime()

	def check_blacklist_disable(self) -> None:
		"""A `Blacklist` group cannot be disabled while it is `WhatsApp Settings.global_blacklist_group`."""
		if not (self.disabled and self.kind == "Blacklist") or self.is_new():
			return
		if not frappe.db.exists("DocType", "WhatsApp Settings"):
			return
		# a read error must stop the save, not wave the global blacklist through
		global_group = frappe.db.get_single_value("WhatsApp Settings", "global_blacklist_group")
		if global_group and global_group == self.name:
			frappe.throw(
				_("{0} is the global blacklist and cannot be disabled").format(self.name),
				WAStateConflictError,
			)


#: who may change a group that decides access
ACCESS_GROUP_EDITORS = {"Administrator", "System Manager", "WhatsApp Manager"}


def access_group_reason(group) -> str | None:
	"""Why the group decides access (stored values: a change in this save is judged by what the
	group already is), or None."""
	stored_kind = frappe.db.get_value("WhatsApp Contact Group", group.name, "kind")
	if "Blacklist" in (stored_kind, group.kind):
		return _("is a blacklist")
	if frappe.db.exists("DocType", "WhatsApp Settings") and frappe.db.get_single_value(
		"WhatsApp Settings", "global_blacklist_group"
	) == group.name:
		return _("is the global blacklist")
	if frappe.get_all(
		"WhatsApp Command",
		or_filters={"allowed_group": group.name, "blocked_group": group.name},
		pluck="name",
		limit=1,
	):
		return _("decides who may run a command")
	return None


def _member_signature(doc: Document) -> list[tuple]:
	return sorted(tuple(row.get(f) for f in MEMBER_COMPARE_FIELDS) for row in doc.get("members") or [])
