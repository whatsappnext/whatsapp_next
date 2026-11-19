# Module role: a function's `report` reply printed as a file (WHATSAPP_NEXT_INTEGRATION §9). Runs the
# report the header declared with the filters the function chose, and lays its columns and rows out
# as a PDF table or an Excel sheet. The router sends the file as a Document, like any other.

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import cstr, escape_html
from frappe.utils.formatters import format_value

FORMATS = ("PDF", "Excel")
MIME = {
	"PDF": "application/pdf",
	"Excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
EXT = {"PDF": "pdf", "Excel": "xlsx"}


def _columns(raw: list[Any]) -> list[dict[str, Any]]:
	"""Report columns as dicts (`fieldname`, `label`, `fieldtype`, `options`), hidden ones dropped."""
	out = []
	for i, column in enumerate(raw or []):
		if isinstance(column, str):
			# legacy "Label:Fieldtype/Options:Width"
			label, _sep, rest = column.partition(":")
			fieldtype = rest.split(":")[0].split("/")[0] or "Data"
			column = {"label": label, "fieldtype": fieldtype, "fieldname": frappe.scrub(label) or f"col_{i}"}
		if not isinstance(column, dict) or column.get("hidden"):
			continue
		out.append(
			{
				"fieldname": column.get("fieldname") or f"col_{i}",
				"label": cstr(column.get("label") or column.get("fieldname") or ""),
				"fieldtype": column.get("fieldtype") or "Data",
				"options": column.get("options"),
				"index": i,
			}
		)
	return out


def _get_row_cell_value(row: Any, col: dict[str, Any]) -> Any:
	if isinstance(row, dict):
		return row.get(col["fieldname"])
	if isinstance(row, list | tuple) and col["index"] < len(row):
		return row[col["index"]]
	return None


def run_report(report_name: str, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], list[Any]]:
	"""`(columns, rows)` of a query or script report, as the current user."""
	from frappe.desk.query_report import run

	result = run(report_name, filters=filters or {}) or {}
	rows = [r for r in result.get("result") or [] if isinstance(r, dict | list | tuple)]
	return _columns(result.get("columns")), rows


def _render_report_pdf(
	title: str,
	columns: list[dict[str, Any]],
	rows: list[Any],
	letter_head: str | None = None,
	company: str | None = None,
) -> bytes:
	from whatsapp_next.services.html_to_pdf import render_html_to_pdf

	from whatsapp_next.services.report_render import _letter_head_html, keep_latin_text_upright, letter_head_document

	# the command's letter head on top (owner, 2026-11-17); None: the site's default one
	letter_head = letter_head or frappe.db.get_value("Letter Head", {"is_default": 1, "disabled": 0}, "name")

	rtl = frappe.local.lang in ("ar", "fa", "he", "ur")
	head = "".join(f"<th>{escape_html(_(c['label']))}</th>" for c in columns)
	body = []
	for row in rows:
		cells = []
		for c in columns:
			value = _get_row_cell_value(row, c)
			shown = (
				format_value(value, {"fieldtype": c["fieldtype"], "options": c["options"]})
				if value not in (None, "")
				else ""
			)
			cells.append(f"<td>{escape_html(cstr(shown))}</td>")
		body.append("<tr>" + "".join(cells) + "</tr>")
	html = f"""<!doctype html><html dir="{"rtl" if rtl else "ltr"}"><head><meta charset="utf-8">
<style>
body{{font-family:sans-serif;font-size:10px}}
h1{{font-size:14px;margin:0 0 8px}}
table{{width:100%;border-collapse:collapse}}
th,td{{border:1px solid #ccc;padding:3px 5px;text-align:{"right" if rtl else "left"}}}
th{{background:#f2f2f2}}
</style></head><body>{_letter_head_html(letter_head, letter_head_document(company, title))}<h1>{escape_html(_(title))}</h1>
<table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></body></html>"""
	return render_html_to_pdf(html, None, {"orientation": "Landscape" if len(columns) > 6 else "Portrait"})


def _render_report_xlsx(title: str, columns: list[dict[str, Any]], rows: list[Any]) -> bytes:
	from frappe.utils.xlsxutils import make_xlsx

	data = [[_(c["label"]) for c in columns]]
	data += [[_get_row_cell_value(row, c) for c in columns] for row in rows]
	return make_xlsx(data, (_(title) or "Report")[:31]).getvalue()


def report_print_formats(report_name: str) -> list[str]:
	"""The report's own print formats a server can render: Frappe's Print Formats for a Report
	(`print_format_for = Report`) written in Jinja. The ones written in JS are drawn by the browser
	(the report view's Print) and cannot be printed here (owner, 2026-11-17)."""
	return frappe.get_all(
		"Print Format",
		filters={"print_format_for": "Report", "report": report_name, "print_format_type": "Jinja", "disabled": 0},
		pluck="name",
		order_by="name asc",
	)


def render_report_with_print_format(
	print_format: str, title: str, columns: list[dict[str, Any]], rows: list[Any], filters: dict[str, Any], letter_head: str | None
) -> bytes:
	"""The report printed with one of its Jinja print formats. The template gets `data` (a list of
	rows, each a dict by fieldname), `columns`, `filters`, `report_name`, `title`, `company`, and
	`letter_head` (the chosen letter head, rendered); its CSS (`css`) goes into the page. The page is
	drawn by the format's own PDF engine (`services/html_to_pdf`), as Desk prints it."""
	from whatsapp_next.services.html_to_pdf import render_html_to_pdf
	from whatsapp_next.services.report_render import _letter_head_html, keep_latin_text_upright, letter_head_document

	pf = frappe.db.get_value("Print Format", print_format, ["html", "css", "report", "print_format_type"], as_dict=True)
	if not pf or pf.report != title or pf.print_format_type != "Jinja":
		frappe.throw(_("{0} is not a Jinja print format of report {1}").format(print_format, title))
	company = (filters or {}).get("company")
	letter_head = letter_head or frappe.db.get_value("Letter Head", {"is_default": 1, "disabled": 0}, "name")
	data = [{c["fieldname"]: _get_row_cell_value(row, c) for c in columns} for row in rows]
	body = frappe.render_template(
		pf.html or "",
		{
			"data": data,
			"columns": columns,
			"filters": frappe._dict(filters or {}),
			"report_name": title,
			"title": _(title),
			"company": company,
			"letter_head": _letter_head_html(letter_head, letter_head_document(company, title)),
		},
	)
	rtl = frappe.local.lang in ("ar", "fa", "he", "ur")
	html = f"""<!doctype html><html dir="{"rtl" if rtl else "ltr"}"><head><meta charset="utf-8"><style>{pf.css or ""}</style></head><body>{body}</body></html>"""
	return render_html_to_pdf(keep_latin_text_upright(html) if rtl else html, print_format, {"orientation": "Portrait"})


def render_report_file(
	msg: dict[str, Any], letter_head: str | None = None, print_format: str | None = None
) -> tuple[str, bytes, str]:
	"""`(file name, bytes, mime)` for a validated `report` message
	(`report_name`, `filters`, `format`, `file_name`). E: `frappe.ValidationError`."""
	file_format = msg.get("format") or "PDF"
	if file_format not in FORMATS:
		frappe.throw(_("Unknown report format {0}").format(file_format))
	report_name = msg["report_name"]
	columns, rows = run_report(report_name, msg.get("filters") or {})
	content = (
		render_report_with_print_format(print_format, report_name, columns, rows, msg.get("filters") or {}, letter_head)
		if file_format == "PDF" and print_format
		else _render_report_pdf(
			report_name, columns, rows, letter_head=letter_head, company=(msg.get("filters") or {}).get("company")
		)
		if file_format == "PDF"
		else _render_report_xlsx(report_name, columns, rows)
	)
	name = cstr(msg.get("file_name") or report_name).strip() or report_name
	if not name.lower().endswith("." + EXT[file_format]):
		name = f"{name}.{EXT[file_format]}"
	return name, content, MIME[file_format]
