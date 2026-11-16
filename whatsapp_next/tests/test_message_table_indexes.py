# Security review 2026-11-16 (high): the queries that run after every group message
# (`read_layer.stats_by_key`: WhatsApp Log.jid, Inbound chat_jid / sender_jid) and every campaign
# refresh (WhatsApp Log.campaign) filtered on columns with no index: each read the whole table.
from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

INDEXED = {
	"WhatsApp Log": ("jid", "campaign"),
	"WhatsApp Inbound Message": ("chat_jid", "sender_jid"),
}


class TestMessageTableIndexes(IntegrationTestCase):
	def test_the_hot_filters_are_indexed(self):
		"""Read from the shipped DocType files: a site gets the indexes at its next migrate."""
		from frappe.modules import get_doc_path

		for doctype, fields in INDEXED.items():
			scrubbed = frappe.scrub(doctype)
			definition = frappe.get_file_json(f"{get_doc_path('WhatsApp Next', 'DocType', doctype)}/{scrubbed}.json")
			indexed = {f["fieldname"] for f in definition["fields"] if f.get("search_index")}
			for fieldname in fields:
				with self.subTest(doctype=doctype, field=fieldname):
					self.assertIn(fieldname, indexed, f"{doctype}.{fieldname} has no index")
