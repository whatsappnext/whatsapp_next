# Copyright (c) 2026, SANAD Digital and contributors
# For license information, please see license.txt

"""A command function (a Server Script whose API method starts with `wa_fn_`) runs only when the
function engine starts it (`services.function_runner.run_function`).

Security review 2026-11-16 (critical): each installed function guards itself by refusing a run
whose `form_dict.cmd` names it, but only `/api/method/<name>` sets `cmd`. `/api/v2/method/<name>`
and the MCP tool call (`/api/v2/mcp`) ran the function as any signed-in user, a portal user too,
with inputs they chose: party, document, and the text it renders with Jinja. The check here sits
where every route ends, `ServerScript.execute_method`, so the functions already installed on a
site are covered without installing them again. The engine's mark is a request flag
(`frappe.flags`): it lives in this request's memory only and nothing a caller sends can set it.
"""

import frappe
from frappe import _
from frappe.core.doctype.server_script.server_script import ServerScript

from whatsapp_next.services.function_header import API_PREFIX

#: set by the function engine around the one script it runs
ENGINE_RUN_FLAG = "wa_function_engine_run"


class WhatsAppServerScript(ServerScript):
	def execute_method(self):
		if (self.api_method or "").startswith(API_PREFIX) and frappe.flags.get(ENGINE_RUN_FLAG) != self.name:
			raise frappe.PermissionError(_("A WhatsApp function runs only from a WhatsApp command."))
		return super().execute_method()
