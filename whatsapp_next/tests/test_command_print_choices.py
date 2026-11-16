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


class TestCommandPrintSettingsSaved(IntegrationTestCase):
	"""The choices kept on the command, checked on save, and used by the run."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from whatsapp_next.tests import function_fixtures as ff
		from whatsapp_next.tests.conftest_frappe import ensure_settings

		ensure_settings(enable_commands=1, command_service_user=ff.ensure_service_user())
		ff.ensure_function("_test_print")
		cls.print_format = "PrintChoice ToDo"
		if not frappe.db.exists("Print Format", cls.print_format):
			frappe.get_doc(
				{"doctype": "Print Format", "name": cls.print_format, "doc_type": "ToDo", "module": "Desk", "custom_format": 1, "print_format_type": "Jinja", "html": "<p>{{ doc.name }}</p>", "standard": "No"}
			).insert(ignore_permissions=True)
		cls.letter_head = frappe.db.get_value("Letter Head", {"letter_head_name": ("like", "PrintChoice LH%")}, "name") or frappe.get_doc(
			{"doctype": "Letter Head", "letter_head_name": f"PrintChoice LH {frappe.generate_hash(length=4)}", "source": "HTML", "content": "<div>LH</div>"}
		).insert(ignore_permissions=True).name

	def setUp(self):
		self._drop_test_command()

	def tearDown(self):
		frappe.set_user("Administrator")
		self._drop_test_command()

	@staticmethod
	def _drop_test_command():
		for name in frappe.get_all("WhatsApp Command", filters={"code": "prtestdoc"}, pluck="name"):
			frappe.db.delete("WhatsApp Command Print Setting", {"parent": name})
		frappe.db.delete("WhatsApp Command", {"code": "prtestdoc"})

	def _save(self, print_settings):
		from whatsapp_next.api.v1 import commands as api
		from whatsapp_next.tests.conftest_frappe import as_user

		with as_user("WhatsApp Manager"):
			return api.save_editor(
				payload={"code": "prtestdoc", "function": "_test_print", "allowed_party_types": ["Customer"], "requires_linked_contact": 0, "print_settings": print_settings}
			)["name"]

	def test_the_choices_are_kept_and_used(self):
		from whatsapp_next.services import command_router as cr

		name = self._save([{"reply": "doc", "print_format": self.print_format, "letter_head": self.letter_head}])
		rows = frappe.get_all("WhatsApp Command Print Setting", filters={"parent": name}, fields=["reply", "print_format", "letter_head"])
		self.assertEqual([(r.reply, r.print_format, r.letter_head) for r in rows], [("doc", self.print_format, self.letter_head)])
		from whatsapp_next.services import commands

		self.assertEqual(commands.get_command_editor_data(name)["command"]["print_settings"][0]["print_format"], self.print_format)
		todo = frappe.get_doc({"doctype": "ToDo", "description": "print-choice saved"}).insert(ignore_permissions=True)
		run = FunctionRun(messages=[{"reply": "doc", "type": "print", "doctype": "ToDo", "name": todo.name, "caption": ""}])
		calls = []
		from frappe.utils.pdf import get_pdf

		pdf = get_pdf("<p>x</p>")

		def fake_render(doctype, docname, **kwargs):
			calls.append((kwargs.get("print_format"), kwargs.get("letter_head")))
			return attachments.RenderedPdf(content=pdf, file_name="doc.pdf")

		with patch.object(attachments, "render_print_pdf", side_effect=fake_render):
			cr.render_outputs(frappe.get_doc("WhatsApp Command", name), run)
		self.assertEqual(calls, [(self.print_format, self.letter_head)])

	def _report_format(self, name, kind="Jinja", html="<p>ROWS={{ data|length }} {{ report_name }}</p>{{ letter_head }}"):
		if not frappe.db.exists("Print Format", name):
			frappe.get_doc(
				{"doctype": "Print Format", "name": name, "print_format_for": "Report", "report": "General Ledger", "module": "Accounts", "custom_format": 1, "print_format_type": kind, "html": html, "standard": "No"}
			).insert(ignore_permissions=True)
		return name

	def test_a_report_prints_with_its_own_jinja_print_format(self):
		"""Owner, 2026-11-17 («التقارير معها قوالب طباعة»): a report reply takes one of the report's
		own print formats; the server renders the Jinja ones, with the letter head."""
		jinja = self._report_format("PrintChoice GL")
		js = self._report_format("PrintChoice GL JS", kind="JS")
		self.assertIn(jinja, report_file.report_print_formats("General Ledger"))
		self.assertNotIn(js, report_file.report_print_formats("General Ledger"), "a JS format is drawn by the browser")
		name = self._save([{"reply": "sheet", "print_format": jinja, "letter_head": self.letter_head}])
		self.assertTrue(name)
		with self.assertRaises(frappe.ValidationError):
			self._save([{"reply": "sheet", "print_format": js}])
		pages = []
		with patch.object(report_file, "run_report", return_value=([{"label": "A", "fieldname": "a", "fieldtype": "Data", "options": None, "index": 0}], [{"a": "x"}, {"a": "y"}])), patch(
			"frappe.utils.pdf.get_pdf", side_effect=lambda html, options=None: pages.append(html) or b"%PDF"
		):
			report_file.render_report_file({"report_name": "General Ledger", "filters": {}, "format": "PDF", "file_name": "r"}, letter_head=self.letter_head, print_format=jinja)
		self.assertIn("ROWS=2 General Ledger", pages[0])
		self.assertIn("<div>LH</div>", pages[0])

	def test_a_print_format_of_another_document_type_is_refused(self):
		other = frappe.db.get_value("Print Format", {"doc_type": ("!=", "ToDo"), "disabled": 0}, "name")
		with self.assertRaises(frappe.ValidationError):
			self._save([{"reply": "doc", "print_format": other}])
		with self.assertRaises(frappe.ValidationError):
			self._save([{"reply": "result_text", "letter_head": self.letter_head}])  # a text has no file
		with self.assertRaises(frappe.ValidationError):
			self._save([{"reply": "sheet", "print_format": other}])  # not one of the report's
