# Security review 2026-11-16 (medium): a WhatsApp Manager could read any field of any document.
# A campaign recipient row names any source document (picker rows and /api/resource take it as
# given) and the body renders it with no permission check; a WhatsApp Notification could be made
# on any DocType (Salary Slip, Employee) with a fixed number of the author's own as recipient.
# A document is rendered only for someone who may read it.
from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from whatsapp_next.services import campaign_runner as runner
from whatsapp_next.tests.conftest_frappe import ensure_test_user

SECRET_DOCTYPE = "User"  # only a System Manager reads another user's record
SECRET_NAME = "Administrator"


class TestManagerDocumentReads(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.manager = ensure_test_user("WhatsApp Manager")

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_a_campaign_body_renders_no_document_the_manager_cannot_read(self):
		self.assertFalse(frappe.has_permission(SECRET_DOCTYPE, "read", SECRET_NAME, user=self.manager))
		recipient = frappe._dict(
			phone_e164="+967784499996", display_name="R", contact=None, source_doctype=SECRET_DOCTYPE, source_name=SECRET_NAME
		)
		campaign = frappe._dict(name="CAMP-X", campaign_name="x", owner=self.manager, started_by=self.manager)
		frappe.set_user(self.manager)
		body = runner._render_for("mail: {{ doc.email }}|", recipient, campaign)
		self.assertNotIn("admin@", body or "")
		# whoever may read it gets it
		frappe.set_user("Administrator")
		readable = frappe._dict(campaign, owner="Administrator", started_by="Administrator")
		self.assertIn("@", runner._render_for("{{ doc.email }}", recipient, readable) or "")

	def test_a_notification_needs_read_on_its_document_type(self):
		frappe.set_user(self.manager)
		doc = frappe.get_doc(
			{
				"doctype": "WhatsApp Notification",
				"notification_name": "ManagerReads Test",
				"enabled": 0,
				# a DocType the Manager cannot read at all (HR's)
				"document_type": "Salary Slip" if frappe.db.exists("DocType", "Salary Slip") else "Employee",
				"event": "New",
				"message": "{{ doc.email }}",
				"recipients": [{"recipient_type": "Fixed Number", "phone": "+967784499996"}],
			}
		)
		with patch.object(frappe, "enqueue"), self.assertRaises(frappe.PermissionError):
			doc.insert()
