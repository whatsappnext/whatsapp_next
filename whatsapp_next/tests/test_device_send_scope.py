# Security review 2026-11-16 (medium): a User Permission on WhatsApp Device limited what an Agent
# could read, but not what it could send: quick send, the simulator and the dispatcher took any
# device, and the log row is inserted with ignore_permissions. An Agent limited to device A sent
# from device B, another branch's WhatsApp number.
from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from whatsapp_next.services import dispatch
from whatsapp_next.tests.conftest_frappe import ensure_device, ensure_test_user


class TestDeviceSendScope(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.agent = ensure_test_user("WhatsApp Agent")
		cls.mine = ensure_device("Scope Device A", "WAD-TEST-SCA", phone="+967777667663")
		cls.theirs = ensure_device("Scope Device B", "WAD-TEST-SCB", phone="+967775040850")

	def setUp(self):
		frappe.db.delete("User Permission", {"user": self.agent, "allow": "WhatsApp Device"})
		frappe.get_doc(
			{"doctype": "User Permission", "user": self.agent, "allow": "WhatsApp Device", "for_value": self.mine}
		).insert(ignore_permissions=True)
		frappe.clear_cache(user=self.agent)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("User Permission", {"user": self.agent, "allow": "WhatsApp Device"})
		frappe.clear_cache(user=self.agent)

	def test_a_user_limited_to_a_device_sends_from_it_only(self):
		frappe.set_user(self.agent)
		self.assertEqual(dispatch._resolve_device(self.mine), self.mine)
		with self.assertRaises(frappe.PermissionError):
			dispatch._resolve_device(self.theirs)
		# no device named: the user's own, not the site's default
		self.assertEqual(dispatch._resolve_device(None), self.mine)
