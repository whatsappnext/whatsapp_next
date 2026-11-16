# Security review 2026-11-16 (low): only quick send checked that its author may open the file it
# carries. A status post's media and a campaign message's attachment read any File by its URL, so
# a Manager could post someone's private file as the device's WhatsApp status.
from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from whatsapp_next.tests.conftest_frappe import ensure_test_user


class TestFileReadsOnSave(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.manager = ensure_test_user("WhatsApp Manager")
		cls.private = frappe.get_doc(
			{"doctype": "File", "file_name": "someone-elses.txt", "content": "private words", "is_private": 1}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_a_status_post_cannot_carry_a_file_its_author_cannot_open(self):
		frappe.set_user(self.manager)
		post = frappe.get_doc({"doctype": "WhatsApp Status Post", "kind": "Image", "media": self.private.file_url})
		with self.assertRaises(frappe.PermissionError):
			post.validate()

	def test_a_campaign_message_cannot_carry_a_file_its_author_cannot_open(self):
		frappe.set_user(self.manager)
		campaign = frappe.get_doc(
			{
				"doctype": "WhatsApp Campaign",
				"campaign_name": "FileReads Test",
				"status": "Draft",
				"messages": [{"message_type": "Document", "attachment": self.private.file_url, "delay_seconds": 0}],
			}
		)
		with self.assertRaises(frappe.PermissionError):
			campaign.check_message_files()
