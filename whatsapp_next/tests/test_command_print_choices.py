# Owner, 2026-11-17: a command chooses the print format and the letter head of what it sends as a
# file. A function declares a printed document (`@reply key | print | doctype=… | title`) and its
# run names the document; a report reply takes the chosen letter head.
from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from whatsapp_next.services import attachments, function_runner, report_file
from whatsapp_next.services import function_header as fh
from whatsapp_next.services.function_runner import FunctionRun

HEADER = """# ==================== WA-FUNCTION ====================
# @function     _test_print_reply
# @title        Print test
# @version      1.0.0
# @party_types  all
# @reply  invoice    | print  | doctype=ToDo | The document
# @reply  statement  | report | report=ToDo  | The report
# ==================== END WA-FUNCTION ====================
"""


class TestCommandPrintChoices(IntegrationTestCase):
	def test_a_print_reply_is_declared_with_its_document_type(self):
		spec = fh.parse_function_header(HEADER)
		replies = {r["key"]: r for r in spec["replies"]}
		self.assertEqual((replies["invoice"]["type"], replies["invoice"]["doctype"]), ("print", "ToDo"))
		with self.assertRaises(fh.HeaderError):
			fh.parse_function_header(HEADER.replace("doctype=ToDo", "x=1"))
		ready = fh.validate_messages(spec, [{"reply": "invoice", "name": "TD-1", "caption": "here"}])
		self.assertEqual(ready[0]["type"], "print")
		self.assertEqual((ready[0]["doctype"], ready[0]["name"]), ("ToDo", "TD-1"))
		with self.assertRaises(ValueError):
			fh.validate_messages(spec, [{"reply": "invoice"}])

	def test_the_chosen_print_format_and_letter_head_print_the_document(self):
		todo = frappe.get_doc({"doctype": "ToDo", "description": "print-choice test"}).insert(ignore_permissions=True)
		run = FunctionRun(messages=[{"reply": "invoice", "type": "print", "doctype": "ToDo", "name": todo.name, "file_name": "doc", "caption": "your document"}])
		calls = []
		from frappe.utils.pdf import get_pdf

		pdf = get_pdf("<p>print choice</p>")

		def fake_render(doctype, name, **kwargs):
			calls.append((doctype, name, kwargs.get("print_format"), kwargs.get("letter_head")))
			return attachments.RenderedPdf(content=pdf, file_name="doc.pdf")

		other_format = frappe.db.get_value("Print Format", {"doc_type": ("!=", "ToDo"), "disabled": 0}, "name")
		with patch.object(attachments, "render_print_pdf", side_effect=fake_render):
			out = function_runner.replies_of(run, print_settings={"invoice": {"print_format": None, "letter_head": "LH-X"}})
			# a print format of another document type is not used
			function_runner.replies_of(run, print_settings={"invoice": {"print_format": other_format, "letter_head": None}})
		self.assertEqual(calls[0], ("ToDo", todo.name, None, "LH-X"))
		self.assertEqual(calls[1][2], None)
		self.assertEqual((out[0]["message_type"], out[0]["body"], out[0]["mime_type"]), ("Document", "your document", "application/pdf"))

	def test_a_document_the_run_may_not_read_is_not_printed(self):
		todo = frappe.get_doc({"doctype": "ToDo", "description": "print-choice private"}).insert(ignore_permissions=True)
		run = FunctionRun(messages=[{"reply": "invoice", "type": "print", "doctype": "ToDo", "name": todo.name, "caption": ""}])
		with patch.object(frappe, "has_permission", return_value=False), patch.object(attachments, "render_print_pdf") as render:
			out = function_runner.replies_of(run)
		render.assert_not_called()
		self.assertEqual(out[0]["message_type"], "Text", "the sender gets the generic error, not the file")

	def test_a_report_reply_carries_the_chosen_letter_head(self):
		lh = frappe.get_doc({"doctype": "Letter Head", "letter_head_name": f"PrintChoice LH {frappe.generate_hash(length=4)}", "source": "HTML", "content": "<div>LETTERHEAD-MARK</div>"}).insert(ignore_permissions=True)
		pages = []
		with patch.object(report_file, "run_report", return_value=([{"label": "A", "fieldname": "a", "fieldtype": "Data", "options": None}], [{"a": "x"}])), patch(
			"frappe.utils.pdf.get_pdf", side_effect=lambda html, options=None: pages.append(html) or b"%PDF"
		):
			report_file.render_report_file({"report_name": "ToDo", "filters": {}, "format": "PDF", "file_name": "r"}, letter_head=lh.name)
		self.assertIn("LETTERHEAD-MARK", pages[0])
