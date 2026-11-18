# Copyright (c) 2026, SANAD Digital and contributors
# For license information, please see license.txt

"""The country a user is in, read from their network: every phone field opens on its dial code
(owner, 2026-11-18). Nothing here downloads the database or reads a real one."""

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from whatsapp_next.api.v1 import phone as phone_api
from whatsapp_next.services import phone, visitor_country


class _Database:
	"""A stand-in country database: one public address in Yemen."""

	def get(self, address):
		return {"country": {"iso_code": "YE"}} if address == "134.35.0.1" else None


class TestVisitorCountry(IntegrationTestCase):
	def _country(self, headers=None, address="134.35.0.1", database=None, no_database=False):
		with (
			patch.object(frappe.local, "request", SimpleNamespace(headers=headers or {}), create=True),
			patch.object(frappe.local, "request_ip", address, create=True),
			patch.object(
				visitor_country,
				"_open_country_database",
				return_value=None if no_database else (database or _Database()),
			),
		):
			return visitor_country.visitor_country()

	def test_the_cloudflare_header_names_the_country_first(self):
		self.assertEqual(self._country({"CF-IPCountry": "sa"}), "SA")

	def test_the_address_is_looked_up_in_the_local_database(self):
		self.assertEqual(self._country(), "YE")

	def test_no_country_when_nothing_can_tell(self):
		self.assertIsNone(self._country({"CF-IPCountry": "XX"}, address="8.8.8.8"), "unknown to the database")
		self.assertIsNone(self._country(address="10.0.0.7"), "a private address is never looked up")
		self.assertIsNone(self._country(no_database=True), "no database on this site yet")
		self.assertIsNone(self._country(address="not an address"))

	def test_the_phone_fields_open_on_the_users_country(self):
		with patch.object(visitor_country, "visitor_country", return_value="SA"):
			self.assertEqual(phone_api.get_countries()["default"], "SA")
		with patch.object(visitor_country, "visitor_country", return_value=None):
			self.assertEqual(phone_api.get_countries()["default"], phone.default_region())

	def test_the_database_is_fetched_only_when_missing_or_old(self):
		with (
			patch.object(visitor_country, "_country_database_age_days", return_value=3),
			patch.object(visitor_country.requests, "get") as fetched,
		):
			self.assertFalse(visitor_country.refresh_country_database())
		fetched.assert_not_called()

	def test_the_page_starts_on_the_users_country(self):
		"""Owner 2026-11-19: the field showed the site's country first and switched a moment later,
		when the server's answer came: the page's own start data carries the network's country."""
		from whatsapp_next.boot import boot_session

		bootinfo = frappe._dict()
		with patch.object(visitor_country, "visitor_country", return_value="SA"):
			boot_session(bootinfo)
		self.assertEqual(bootinfo.whatsapp_next.region, "SA")
		bootinfo = frappe._dict()
		with patch.object(visitor_country, "visitor_country", return_value=None):
			boot_session(bootinfo)
		self.assertEqual(bootinfo.whatsapp_next.region, phone.default_region())
