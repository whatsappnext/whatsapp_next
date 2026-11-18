# Module role: the country the user is in, read from their network, so every phone field opens on
# that country's dial code (owner, 2026-11-18). The platform has the same module.
#
# Two sources, and the visitor's address never leaves this server:
#   1. Cloudflare's `CF-IPCountry` header, when the site is served through Cloudflare
# 	  (the platform's whatsappnext.app is).
#   2. DB-IP's free country database ("IP to Country Lite", CC BY 4.0), kept in the site's private
# 	  files and read here. A daily job fetches it when it is missing or older than a month; the
# 	  download sends nothing about anyone.
# Neither knows: no country, and the field keeps the site's own default.

from __future__ import annotations

import gzip
import ipaddress
import os
import shutil
import time
from datetime import date, timedelta

import frappe
import requests

#: Cloudflare names the visitor's country here ("XX" unknown, "T1" Tor).
COUNTRY_HEADER = "CF-IPCountry"
UNKNOWN_HEADER_VALUES = {"XX", "T1"}
#: DB-IP publishes a new free database each month, named by its month.
COUNTRY_DATABASE_URL = "https://download.db-ip.com/free/dbip-country-lite-{month}.mmdb.gz"
COUNTRY_DATABASE_MAX_AGE_DAYS = 35
COUNTRY_DATABASE_DOWNLOAD_SECONDS = 120

# one open database per worker process, reopened when the file changes
_open_databases: dict[str, tuple[float, object]] = {}


def visitor_country() -> str | None:
	"""The ISO-3166 alpha-2 code of the country the current request comes from, or None."""
	request = getattr(frappe.local, "request", None)
	headers = getattr(request, "headers", None) or {}
	named = str(headers.get(COUNTRY_HEADER) or "").strip().upper()
	if len(named) == 2 and named.isalpha() and named not in UNKNOWN_HEADER_VALUES:
		return named
	return country_of_address(getattr(frappe.local, "request_ip", None))


def country_of_address(address: str | None) -> str | None:
	"""The country a public address belongs to, from the local database; None for a private or
	unknown address, or when this site has no database yet."""
	try:
		parsed = ipaddress.ip_address(str(address or "").strip())
	except ValueError:
		return None
	if not parsed.is_global:
		return None
	database = _open_country_database()
	if database is None:
		return None
	try:
		row = database.get(str(parsed))
	except Exception:
		return None
	code = str(((row or {}).get("country") or {}).get("iso_code") or "").upper()
	return code if len(code) == 2 else None


def country_database_path() -> str:
	"""Where this site keeps the country database."""
	return frappe.get_site_path("private", "geo", "dbip-country-lite.mmdb")


def _open_country_database():
	path = country_database_path()
	try:
		changed_at = os.path.getmtime(path)
	except OSError:
		return None
	opened = _open_databases.get(path)
	if opened and opened[0] == changed_at:
		return opened[1]
	try:
		import maxminddb

		database = maxminddb.open_database(path)
	except Exception:
		return None
	_open_databases[path] = (changed_at, database)
	return database


def _country_database_age_days() -> float | None:
	try:
		return (time.time() - os.path.getmtime(country_database_path())) / 86400
	except OSError:
		return None


def refresh_country_database() -> bool:
	"""Daily job: fetch this month's database (or last month's, early in a month) when the site
	has none or its copy is older than a month. True when a new copy was saved."""
	age = _country_database_age_days()
	if age is not None and age < COUNTRY_DATABASE_MAX_AGE_DAYS:
		return False
	first_of_month = date.today().replace(day=1)
	months = [first_of_month, first_of_month - timedelta(days=1)]
	for month in months:
		if _download_country_database(COUNTRY_DATABASE_URL.format(month=month.strftime("%Y-%m"))):
			return True
	frappe.log_error(
		title="Country database not downloaded",
		message="DB-IP answered no database for this month or the last.",
	)
	return False


def _download_country_database(url: str) -> bool:
	path = country_database_path()
	partial = path + ".download"
	try:
		response = requests.get(url, timeout=COUNTRY_DATABASE_DOWNLOAD_SECONDS, stream=True)
		if response.status_code != 200:
			return False
		os.makedirs(os.path.dirname(path), exist_ok=True)
		with gzip.GzipFile(fileobj=response.raw) as packed, open(partial, "wb") as target:
			shutil.copyfileobj(packed, target)
		import maxminddb

		maxminddb.open_database(partial).close()  # a broken file never replaces a good one
		os.replace(partial, path)
		return True
	except Exception:
		if os.path.exists(partial):
			os.remove(partial)
		return False
