# Security review 2026-11-16 (high): `reprocess_failed` read the oldest 100 Failed events and only
# then skipped, in Python, those that had used their tries. Once 100 exhausted events existed, no
# newer failed event was ever tried again: one bad burst stopped inbound retries for good.
from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from whatsapp_next.webhooks import handlers

PREFIX = "retry-backlog-test-"


class TestWebhookRetryBacklog(IntegrationTestCase):
	def setUp(self):
		frappe.db.delete("WhatsApp Webhook Event", {"event_id": ("like", f"{PREFIX}%")})

	def tearDown(self):
		frappe.db.delete("WhatsApp Webhook Event", {"event_id": ("like", f"{PREFIX}%")})

	def _failed(self, index, tries, minutes_ago):
		doc = frappe.new_doc("WhatsApp Webhook Event")
		doc.update(
			{
				"event_id": f"{PREFIX}{index}",
				"event_name": "message.received",
				"status": "Failed",
				"error": f"[try {tries}] RuntimeError: boom",
				"received_at": add_to_date(now_datetime(), minutes=-minutes_ago),
			}
		)
		doc.name = f"{PREFIX}{index}"
		doc.db_insert()
		return doc.name

	def test_a_new_failure_is_retried_behind_a_hundred_exhausted_ones(self):
		for i in range(handlers.MAX_TRIES * 40):  # 120 events that used all their tries, older
			self._failed(f"old-{i}", handlers.MAX_TRIES, minutes_ago=600 - i)
		fresh = self._failed("fresh", 1, minutes_ago=30)
		real = frappe.get_all

		def only_this_test(doctype, *args, filters=None, **kwargs):
			if doctype == "WhatsApp Webhook Event":
				mine = ["event_id", "like", f"{PREFIX}%"]
				if isinstance(filters, list):
					filters = [*filters, mine]
				else:
					filters = {**(filters or {}), "event_id": ("like", f"{PREFIX}%")}
			return real(doctype, *args, filters=filters, **kwargs)

		ran = []
		with (
			patch.object(frappe, "get_all", side_effect=only_this_test),
			patch.object(handlers, "process_event", side_effect=ran.append),
		):
			count = handlers.reprocess_failed()
		self.assertEqual(ran, [fresh])
		self.assertEqual(count, 1)
