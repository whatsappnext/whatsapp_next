# Security review 2026-11-16 (high, disk): retention deleted the files attached to WhatsApp Log
# rows only. A received message's file (up to 100 MB, attached to WhatsApp Inbound Message) stayed
# on disk for ever after its row was deleted.
from __future__ import annotations

import os

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime

from whatsapp_next.services import retention

INBOUND = "WhatsApp Inbound Message"


class TestRetentionFiles(IntegrationTestCase):
	def _received_with_file(self, days_ago):
		row = frappe.new_doc(INBOUND)
		row.update({"provider_message_id": f"retention-file-{frappe.generate_hash(length=8)}", "received_at": add_days(now_datetime(), -days_ago)})
		row.name = frappe.generate_hash(length=12)
		row.db_insert()
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"received-{frappe.generate_hash(length=8)}.txt",
				"content": f"picture {frappe.generate_hash(length=12)}",
				"is_private": 1,
				"attached_to_doctype": INBOUND,
				"attached_to_name": row.name,
			}
		).insert(ignore_permissions=True)
		return row.name, file_doc

	def test_a_received_messages_file_goes_with_its_row(self):
		old_row, old_file = self._received_with_file(days_ago=400)
		new_row, new_file = self._received_with_file(days_ago=2)
		self.assertTrue(os.path.exists(old_file.get_full_path()))
		retention.purge_table(INBOUND, 365, "received_at", filters={"name": ("in", [old_row, new_row])}, commit=False)
		self.assertFalse(frappe.db.exists(INBOUND, old_row))
		self.assertFalse(frappe.db.exists("File", old_file.name), "the File row stayed")
		self.assertFalse(os.path.exists(old_file.get_full_path()), "the file stayed on disk")
		self.assertTrue(frappe.db.exists("File", new_file.name), "a recent message keeps its file")
