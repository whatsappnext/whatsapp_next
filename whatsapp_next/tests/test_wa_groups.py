# Creating WhatsApp groups (services/wa_groups): members read from a selection, the provider call,
# the group's Number row, the contact it was made for, and the plan gate.

from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from whatsapp_next.exceptions import WANotFoundError, WANotSupportedError, WAValidationError
from whatsapp_next.providers.schemas import (
	GroupCreated,
	GroupDetails,
	GroupJoinRequest,
	GroupMember,
	GroupMembersAdded,
	GroupParticipantInfo,
)
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


	def get_group_details(self, platform_device, group_jid):
		self.calls.append(("read", platform_device, group_jid))
		own = (frappe.db.get_value("WhatsApp Device", {"platform_device": platform_device}, "phone_e164") or "").lstrip("+")
		return GroupDetails(
			name="Project team",
			members=[
				GroupParticipantInfo(phone="967777667663", jid="967777667663@s.whatsapp.net"),
				GroupParticipantInfo(phone=own, jid=f"{own}@s.whatsapp.net", is_admin=True, is_super_admin=True),
				GroupParticipantInfo(phone="", jid="888000000000777@lid"),
			],
			requests=[GroupJoinRequest(phone="967777999579", jid="967777999579@s.whatsapp.net")],
		)

	def change_group_members(self, platform_device, group_jid, action, members):
		self.calls.append(("change", platform_device, group_jid, action, members))
		return [GroupMember(phone=m.lstrip("+"), status="done") for m in members]


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


	def test_a_groups_members_and_requests_are_read(self):
		"""Owner 2026-11-17: managing a group's members from its row."""
		group = self._create()["jid"]
		with patch("whatsapp_next.providers.registry.get_provider", return_value=self.provider):
			result = wa_groups.group_members(group)
		self.assertEqual(result["device"], self.device)
		self.assertTrue(result["device_is_admin"], "the device is the group's owner here")
		first = result["members"][0]
		self.assertEqual((first["is_super_admin"], first["is_this_device"]), (True, True), "the owner comes first")
		self.assertEqual([m["phone_e164"] for m in result["members"][1:]], ["+967777667663", None])
		self.assertEqual(result["members"][2]["jid"], "888000000000777@lid")
		self.assertEqual([r["phone_e164"] for r in result["requests"]], ["+967777999579"])

	def test_a_member_is_changed_and_the_change_audited(self):
		group = self._create()["jid"]
		with patch("whatsapp_next.providers.registry.get_provider", return_value=self.provider):
			result = wa_groups.change_group_members(group, "make_admin", ["967777667663"])
			with self.assertRaises(WAValidationError):
				wa_groups.change_group_members(group, "ban", ["967777667663"])
			with patch("whatsapp_next.services.settings.chat_in", return_value=False):
				refused = wa_groups.change_group_members(group, "remove", ["+967777667663"])
		self.assertEqual(self.provider.calls[-1][3:], ("make_admin", ["+967777667663"]))
		self.assertEqual(result["members"], [{"phone": "967777667663", "status": "done"}])
		self.assertEqual(refused["members"], [{"phone": "967777667663", "status": "not_allowed"}])
		self.assertTrue(
			frappe.db.exists("WhatsApp Audit Log", {"action": "WhatsApp Group Members Changed", "reference_name": group})
		)

	def test_at_most_fifty_numbers_are_added_at_a_time(self):
		"""Platform review 2026-11-18: adding many people at once gets the sending number banned."""
		group = self._create()["jid"]
		many = [f"+9677{n:08d}" for n in range(51)]
		with patch("whatsapp_next.providers.registry.get_provider", return_value=self.provider):
			with self.assertRaises(WAValidationError):
				wa_groups.add_group_members(group, many)
		self.assertFalse([c for c in self.provider.calls if c[0] == "add"], "nothing was asked")

	def test_approving_join_requests_takes_at_most_fifty(self):
		"""Platform review 2026-11-18: approving requests adds people to the group, so it is capped like
		adding; removing keeps the group's own limit."""
		group = self._create()["jid"]
		many = [f"+9677{n:08d}" for n in range(51)]
		with patch("whatsapp_next.providers.registry.get_provider", return_value=self.provider):
			with self.assertRaises(WAValidationError) as caught:
				wa_groups.change_group_members(group, "approve", many)
			self.assertIn("50", str(caught.exception))
			self.assertFalse([c for c in self.provider.calls if c[0] == "change"], "nothing was asked")
			removed = wa_groups.change_group_members(group, "remove", many)
		self.assertEqual(len(removed["members"]), 51, "removing is not capped at 50")

	def test_a_new_group_takes_at_most_fifty_members(self):
		"""Platform review 2026-11-18: a new group's members are added at once too."""
		many = [f"+9677{n:08d}" for n in range(51)]
		with self.assertRaises(WAValidationError) as caught:
			self._create(members=many)
		self.assertIn("50", str(caught.exception))
		self.assertEqual(self.provider.calls, [], "nothing was asked of WhatsApp")


class TestScreenCallsAreConfigured(IntegrationTestCase):
	"""2026-11-18: «Add members» called `wa_groups.add_members`, which the screens' API map did not
	list, so its button failed. Every two-part key a screen passes to `ui.call` is in that map."""

	def test_every_short_api_key_a_screen_calls_is_configured(self):
		import os
		import re

		root = frappe.get_app_path("whatsapp_next", "public", "js")
		setup = open(os.path.join(root, "whatsapp_next_setup.js"), encoding="utf-8").read()
		areas = dict(re.findall(r'^\t(\w+): \[([^\]]*)\]', setup, flags=re.M))
		configured = {f"{area}.{fn}" for area, fns in areas.items() for fn in re.findall(r'"(\w+)"', fns)}
		missing = set()
		for folder, _dirs, files in os.walk(root):
			if "dist" in folder:
				continue
			for name in files:
				if not name.endswith(".js"):
					continue
				text = open(os.path.join(folder, name), encoding="utf-8").read()
				for key in re.findall(r'\.call\(\s*"(\w+\.\w+)"', text):
					if key not in configured:
						missing.add(f"{key} ({name})")
		self.assertEqual(sorted(missing), [])
