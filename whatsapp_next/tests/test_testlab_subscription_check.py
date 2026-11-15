# The Test Lab's «Subscription and balance» check reads the subscription's end date. Frappe 15 reads
# 0001-01-01 (an empty date in the database) as no date at all: the check took it for a date and
# subtracted from it, and the whole health page failed with a TypeError (version-15 screens,
# 2026-11-15). A date it cannot read is no end date.

from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from whatsapp_next.services import testlab


class TestTestLabSubscriptionCheck(IntegrationTestCase):
	def _settings(self, end):
		return frappe._dict(
			subscription_status="Active",
			messages_remaining=10,
			message_limit=100,
			plan_name="Basic",
			plan_code="basic",
			subscription_end=end,
			subscription_synced_at=frappe.utils.now_datetime(),
		)

	def test_an_end_date_frappe_cannot_read_is_no_end_date(self):
		# Frappe 15's getdate answers None for 0001-01-01; on 16 it is a date of year 1
		with patch.object(testlab, "getdate", return_value=None):
			check = testlab._subscription_check(self._settings("0001-01-01"))
		self.assertNotIn("0001", check["detail"])

	def test_a_real_end_date_is_shown(self):
		end = frappe.utils.add_days(frappe.utils.nowdate(), 30)
		check = testlab._subscription_check(self._settings(end))
		self.assertIn(frappe.format(end, {"fieldtype": "Date"}), check["detail"])
