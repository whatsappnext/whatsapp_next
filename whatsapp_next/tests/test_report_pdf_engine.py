# A report reply printed with a print format is drawn by that format's PDF engine (Chrome or
# wkhtmltopdf), as Desk prints it and as document attachments already are (topland, 2026-11-19:
# «كشف» came out with DejaVu fonts, no flex and the wrong page size, because it was always drawn by
# wkhtmltopdf). Chrome failing falls back to wkhtmltopdf with an Error Log entry. Report replies
# and alerts without a print format are unchanged.

from __future__ import annotations

import io
import os
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from whatsapp_next.services import html_to_pdf, report_file, report_render

REPORT = "General Ledger"
COLUMNS = [{"label": "A", "fieldname": "a", "fieldtype": "Data", "options": None, "index": 0}]
ROWS = [{"a": "x"}, {"a": "y"}]


class TestReportPdfEngine(IntegrationTestCase):
	def _format(self, name, engine, **values):
		if frappe.db.exists("Print Format", name):
			frappe.delete_doc("Print Format", name, force=True, ignore_permissions=True)
		doc = frappe.get_doc(
			{
				"doctype": "Print Format",
				"name": name,
				"print_format_for": "Report",
				"report": REPORT,
				"module": "Accounts",
				"custom_format": 1,
				"print_format_type": "Jinja",
				"html": "<p>ROWS={{ data|length }}</p>",
				"css": "@page { size: A5; }",
				"standard": "No",
				**(
					{"pdf_generator": engine}
					if frappe.get_meta("Print Format").has_field("pdf_generator")
					else {}
				),
				**values,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "Print Format", doc.name, force=True, ignore_permissions=True)
		return doc.name

	def _render(self, print_format):
		return report_file.render_report_with_print_format(print_format, REPORT, COLUMNS, ROWS, {}, None)

	def test_a_chrome_format_is_drawn_by_chrome(self):
		if not frappe.get_meta("Print Format").has_field("pdf_generator"):
			self.skipTest("Frappe 15 has no PDF engine choice")
		name = self._format("PdfEngine Chrome", "chrome")
		with (
			patch("frappe.utils.pdf.get_chrome_pdf", return_value=b"%PDF-chrome") as chrome,
			patch("frappe.utils.pdf.get_pdf") as wkhtmltopdf,
		):
			self.assertEqual(self._render(name), b"%PDF-chrome")
		self.assertEqual(chrome.call_args.args[0], name)
		self.assertIn("ROWS=2", chrome.call_args.args[1])
		self.assertEqual(chrome.call_args.kwargs.get("pdf_generator"), "chrome")
		wkhtmltopdf.assert_not_called()

	def test_chrome_failing_falls_back_to_wkhtmltopdf_and_is_logged(self):
		if not frappe.get_meta("Print Format").has_field("pdf_generator"):
			self.skipTest("Frappe 15 has no PDF engine choice")
		name = self._format("PdfEngine Chrome Fails", "chrome", margin_top=20, margin_left=8)
		before = frappe.db.count("Error Log", {"method": ("like", "WhatsApp PDF%")})
		with (
			patch("frappe.utils.pdf.get_chrome_pdf", side_effect=RuntimeError("no browser")),
			patch("frappe.utils.pdf.get_pdf", return_value=b"%PDF-wk") as wkhtmltopdf,
		):
			self.assertEqual(self._render(name), b"%PDF-wk")
		options = wkhtmltopdf.call_args.args[1]
		self.assertEqual(
			(options.get("page-size"), options.get("margin-top"), options.get("margin-left")),
			("A5", "20mm", "8mm"),
		)
		self.assertEqual(frappe.db.count("Error Log", {"method": ("like", "WhatsApp PDF%")}), before + 1)

	def test_a_wkhtmltopdf_format_takes_its_page_size_and_margins(self):
		name = self._format("PdfEngine Wk", "wkhtmltopdf", margin_bottom=12)
		with (
			patch("frappe.utils.pdf.get_chrome_pdf") as chrome,
			patch("frappe.utils.pdf.get_pdf", return_value=b"%PDF") as wkhtmltopdf,
		):
			self._render(name)
		chrome.assert_not_called()
		options = wkhtmltopdf.call_args.args[1]
		self.assertEqual((options.get("page-size"), options.get("margin-bottom")), ("A5", "12mm"))

	def test_replies_and_alerts_without_a_print_format_are_unchanged(self):
		with (
			patch("frappe.utils.pdf.get_chrome_pdf") as chrome,
			patch("frappe.utils.pdf.get_pdf", return_value=b"%PDF") as wkhtmltopdf,
		):
			with patch.object(report_file, "run_report", return_value=(COLUMNS, ROWS)):
				report_file.render_report_file(
					{"report_name": REPORT, "filters": {}, "format": "PDF", "file_name": "r"}
				)
			report_render.report_pdf("<table><tr><th>A</th></tr></table>")
		chrome.assert_not_called()
		self.assertEqual(wkhtmltopdf.call_count, 2)
		self.assertEqual(html_to_pdf.engine_for(None), "wkhtmltopdf")

	def test_chrome_really_draws_the_page(self):
		"""The bench's own Chrome, when it has one: the page comes out of Chrome (Skia), not Qt."""
		if not frappe.get_meta("Print Format").has_field("pdf_generator"):
			self.skipTest("Frappe 15 has no PDF engine choice")
		if not os.path.isdir(os.path.join(frappe.utils.get_bench_path(), "chromium")):
			self.skipTest("no Chrome on this bench")
		from pypdf import PdfReader

		name = self._format("PdfEngine Chrome Real", "chrome")
		pdf = self._render(name)
		producer = str(PdfReader(io.BytesIO(pdf)).metadata.get("/Producer") or "")
		self.assertIn("Skia", producer, producer)
