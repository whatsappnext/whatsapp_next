# Security review 2026-11-16 (medium): a command's allowed and blocked groups and the global
# blacklist are WhatsApp Contact Groups, which an Agent and a Contact User may write. An Agent
# could add an outside number to a sensitive command's allowed group, or take one off a blocked
# group, changing the access a Manager set. A group used for access is changed by a Manager only.
from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from whatsapp_next.tests.conftest_frappe import ensure_test_user

GROUP = "AccessGroups Test"


class TestAccessGroups(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.agent = ensure_test_user("WhatsApp Agent")

	def setUp(self):
		frappe.db.delete("WhatsApp Contact Group", {"group_name": ("like", f"{GROUP}%")})

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("WhatsApp Contact Group", {"group_name": ("like", f"{GROUP}%")})

	def _group(self, suffix, kind="Other"):
		return frappe.get_doc(
			{"doctype": "WhatsApp Contact Group", "group_name": f"{GROUP} {suffix}", "kind": kind, "source": "Manual"}
		).insert(ignore_permissions=True)

	def test_an_agent_cannot_change_a_blacklist(self):
		group = self._group("blacklist", kind="Blacklist")
		frappe.set_user(self.agent)
		group.reload()
		group.append("members", {"phone": "+967784499996"})
		with self.assertRaises(frappe.PermissionError):
			group.save()

	def test_an_agent_cannot_change_a_commands_allowed_group(self):
		group = self._group("allowed")
		original = frappe.get_all
		with_command = {"name": "CMD-X"}

		def used_by_a_command(doctype, *args, **kwargs):
			if doctype == "WhatsApp Command":
				return [with_command]
			return original(doctype, *args, **kwargs)

		from unittest.mock import patch

		frappe.set_user(self.agent)
		group.reload()
		group.append("members", {"phone": "+967784499996"})
		with patch.object(frappe, "get_all", side_effect=used_by_a_command), self.assertRaises(frappe.PermissionError):
			group.save()

	def test_an_agent_still_edits_a_marketing_group(self):
		group = self._group("marketing", kind="Marketing")
		frappe.set_user(self.agent)
		group.reload()
		group.append("members", {"phone": "+967784499996"})
		group.save()
		self.assertEqual(len(group.members), 1)
