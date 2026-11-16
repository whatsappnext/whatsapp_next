# Creating WhatsApp groups (services/wa_groups): members read from a selection, the provider call,
# the group's Number row, the contact it was made for, and the plan gate.

from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from whatsapp_next.exceptions import WANotFoundError, WANotSupportedError, WAValidationError
from whatsapp_next.providers.schemas import GroupCreated, GroupMember, GroupMembersAdded
from whatsapp_next.services import wa_groups
from whatsapp_next.tests.conftest_frappe import ensure_device


def set_plan_allows_groups(groups: bool) -> None:
	frappe.db.set_single_value("WhatsApp Settings", "plan_features", frappe.as_json({"allow_groups": groups}))
	frappe.clear_document_cache("WhatsApp Settings", "WhatsApp Settings")


class Provider:
	def __init__(self):
		self.calls = []

	def create_group(self, platform_device, name, participants, description=None):
		self.calls.append((platform_device, name, participants, description))
		return GroupCreated(
			jid=f"1203630{frappe.generate_hash(length=8).translate(str.maketrans('abcdef', '123456'))}@g.us",
			name=name,
			members=[
				GroupMember(phone=participants[0].lstrip("+"), status="added"),
				GroupMember(phone=participants[-1].lstrip("+"), status="invite_required"),
			],
			invite_link="https://chat.whatsapp.com/x",
		)


	def add_group_members(self, platform_device, group_jid, participants):
		self.calls.append(("add", platform_device, group_jid, participants))
		statuses = ["added", "already_member", "invite_required"]
		return GroupMembersAdded(
			members=[GroupMember(phone=p.lstrip("+"), status=statuses[i % 3]) for i, p in enumerate(participants)],
			invite_link="https://chat.whatsapp.com/y",
		)


class TestWaGroups(IntegrationTestCase):
	def setUp(self):
		self.device = (
			frappe.db.get_value("WhatsApp Device", {"status": "Connected", "disabled": 0}, "name")
			or ensure_device()
		)
		set_plan_allows_groups(True)
		self.provider = Provider()

	def _create(self, **kw):
		with patch("whatsapp_next.providers.registry.get_provider", return_value=self.provider):
			return wa_groups.create_whatsapp_group(
				self.device,
				kw.pop("name", "Project team"),
				kw.pop("members", ["+967784499996", "967777667663"]),
				**kw,
			)

	def test_creates_the_group_and_its_number(self):
		result = self._create(description="Weekly")
		self.assertEqual(self.provider.calls[0][2], ["+967784499996", "+967777667663"])
		self.assertEqual([m["status"] for m in result["members"]], ["added", "invite_required"])
		row = frappe.db.get_value(
			"WhatsApp Number", result["jid"], ["number_type", "display_name", "synced_device"], as_dict=True
		)
		self.assertEqual(
			(row.number_type, row.display_name, row.synced_device), ("Group", "Project team", self.device)
		)
		self.assertTrue(
			frappe.db.exists(
				"WhatsApp Audit Log", {"action": "WhatsApp Group Created", "reference_name": result["jid"]}
			)
		)

	def test_saved_among_the_contacts_numbers(self):
		contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": f"Group {frappe.generate_hash(length=5)}",
				"phone_nos": [{"phone": "+967777999579", "is_primary_mobile_no": 1}],
			}
		).insert(ignore_permissions=True)
		members = wa_groups.members_from("Contact", [contact.name])
		self.assertEqual([m["phone_e164"] for m in members], ["+967777999579"])
		result = self._create(members=[m["phone_e164"] for m in members], contact=contact.name)
		self.assertTrue(result["saved_to_contact"])
		phones = frappe.get_all("Contact Phone", filters={"parent": contact.name}, pluck="wa_phone_e164")
		self.assertIn(result["jid"], phones)
		self.assertEqual(frappe.db.get_value("WhatsApp Number", result["jid"], "contact"), contact.name)

	def test_needs_the_plan_and_members(self):
		with self.assertRaises(WAValidationError):
			self._create(members=["not a number"])
		set_plan_allows_groups(False)
		with self.assertRaises(WANotSupportedError):
			self._create()

	def test_nothing_is_made_on_whatsapp_that_the_site_then_refuses(self):
		"""A disabled device, or a contact the user may not write or that does not exist, is refused
		before the group is made: refused after it, the group stayed on WhatsApp while the site
		rolled its own row back."""
		from whatsapp_next.exceptions import WANotFoundError, WAStateConflictError
		from whatsapp_next.tests.conftest_frappe import as_user

		frappe.db.set_value("WhatsApp Device", self.device, "disabled", 1)
		self.addCleanup(frappe.db.set_value, "WhatsApp Device", self.device, "disabled", 0)
		with self.assertRaises(WAStateConflictError):
			self._create()
		frappe.db.set_value("WhatsApp Device", self.device, "disabled", 0)
		with self.assertRaises(WANotFoundError):
			self._create(contact="No Such Contact 4f2a")
		with as_user("WhatsApp Viewer"), self.assertRaises(frappe.PermissionError):
			self._create(contact="No Such Contact 4f2a")
		self.assertEqual(self.provider.calls, [], "nothing was asked of WhatsApp")

	def test_a_selection_too_large_is_refused_not_cut(self):
		"""More records than the action takes are refused with a message; the first 256 were read
		silently, so a larger number check left the rest unchecked."""
		names = [f"N-{i}" for i in range(wa_groups.MAX_MEMBERS + 1)]
		with self.assertRaises(WAValidationError):
			wa_groups.members_from("WhatsApp Number", names)
		self.assertEqual(wa_groups.members_from("WhatsApp Number", names, max_records=5000), [])


	def test_members_are_added_to_an_existing_group(self):
		"""Owner 2026-11-17: a group's row in WhatsApp Numbers adds members to the WhatsApp group."""
		group = self._create()["jid"]
		with patch("whatsapp_next.providers.registry.get_provider", return_value=self.provider):
			result = wa_groups.add_group_members(group, ["967784499996", "+967777667663", "+967784499996", "+967777999579"])
		platform_device = frappe.db.get_value("WhatsApp Device", self.device, "platform_device")
		self.assertEqual(self.provider.calls[-1], ("add", platform_device, group, ["+967784499996", "+967777667663", "+967777999579"]))
		self.assertEqual(
			[(m["phone"], m["status"]) for m in result["members"]],
			[("967784499996", "added"), ("967777667663", "already_member"), ("967777999579", "invite_required")],
		)
		self.assertEqual((result["device"], result["invite_link"]), (self.device, "https://chat.whatsapp.com/y"))
		self.assertTrue(
			frappe.db.exists("WhatsApp Audit Log", {"action": "WhatsApp Group Members Added", "reference_name": group})
		)

	def test_a_number_off_send_only_to_is_not_asked(self):
		group = self._create()["jid"]
		with (
			patch("whatsapp_next.providers.registry.get_provider", return_value=self.provider),
			patch("whatsapp_next.services.settings.chat_in", side_effect=lambda _f, key: key == "+967784499996"),
		):
			result = wa_groups.add_group_members(group, ["+967784499996", "+967775675566"])
		self.assertEqual(self.provider.calls[-1][3], ["+967784499996"])
		self.assertEqual([m["status"] for m in result["members"]], ["added", "not_allowed"])

	def test_adding_needs_a_group_a_number_and_the_plan(self):
		group = self._create()["jid"]
		with patch("whatsapp_next.providers.registry.get_provider", return_value=self.provider):
			with self.assertRaises(WANotFoundError):
				wa_groups.add_group_members("+967784499996", ["+967777667663"])  # not a group
			with self.assertRaises(WAValidationError):
				wa_groups.add_group_members(group, ["not a number"])
			set_plan_allows_groups(False)
			with self.assertRaises(WANotSupportedError):
				wa_groups.add_group_members(group, ["+967777667663"])
