# Module role: one way to turn an HTML page into PDF bytes for a WhatsApp file — with the PDF engine
# its print format chose, the way Desk prints it and the way document attachments already are
# (`services/attachments.render_print_pdf`).
#
# Why: a report reply printed with its print format was always drawn by wkhtmltopdf, whatever the
# format chose. topland's «كشف» (Customer Ledger, «كشف حساب عميل v4», chrome) came out with DejaVu
# instead of IBM Plex, no flex and the wrong page size (2026-11-19). Chrome reads the format's page
# size and margins and its `@page` rule itself; wkhtmltopdf is handed them as options. Chrome failing
# leaves an Error Log entry and the file is drawn by wkhtmltopdf, so one always arrives. A page with no
# print format (the plain report table, report alerts) is drawn by wkhtmltopdf as before.

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import flt


def engine_for(print_format: str | None) -> str:
	"""`chrome` or `wkhtmltopdf`: the print format's own choice, else Print Settings' (Frappe 16);
	wkhtmltopdf for a page without a print format."""
	if not print_format:
		return "wkhtmltopdf"
	from whatsapp_next.services.attachments import pdf_engine

	return pdf_engine(print_format)


def print_format_wkhtmltopdf_options(print_format: str | None) -> dict[str, str]:
	"""The print format's page size (`attachments.page_options`) and its own margins, as
	wkhtmltopdf options; `{}` without a print format."""
	if not print_format or not frappe.db.exists("Print Format", print_format):
		return {}
	from whatsapp_next.services.attachments import page_options

	options = dict(page_options(print_format))
	margins = (
		frappe.db.get_value(
			"Print Format",
			print_format,
			["margin_top", "margin_bottom", "margin_left", "margin_right"],
			as_dict=True,
		)
		or {}
	)
	for side in ("top", "bottom", "left", "right"):
		value = flt(margins.get(f"margin_{side}"))
		if value:
			options[f"margin-{side}"] = f"{value:g}mm"
	return options


def render_html_to_pdf(
	html: str, print_format: str | None = None, options: dict[str, Any] | None = None
) -> bytes:
	"""PDF bytes of `html`, drawn by `print_format`'s engine: Chrome when it chose Chrome, falling
	back to wkhtmltopdf (logged) when Chrome fails; wkhtmltopdf otherwise, with the format's page
	size and margins over `options`."""
	from frappe.utils import pdf as frappe_pdf

	if engine_for(print_format) == "chrome":
		try:
			drawn = frappe_pdf.get_chrome_pdf(print_format, html, {}, None, pdf_generator="chrome")
			if drawn:
				return drawn
			raise RuntimeError("Chrome returned no PDF")
		except Exception:
			frappe.log_error(
				title="WhatsApp PDF: Chrome failed, wkhtmltopdf used",
				message=f"print format: {print_format}\n\n{frappe.get_traceback()}",
			)
	return frappe_pdf.get_pdf(html, {**(options or {}), **print_format_wkhtmltopdf_options(print_format)})
