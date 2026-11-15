# A message the device's own WhatsApp sent — from the phone, from another device linked to the
# same number, or a button pressed in the number's chat with itself — is not a message from a
# customer: it is never stored as inbound and never runs a command (live test 2026-11-12: a tap in
# support Z's chat with itself ran «كشف» as a command from the device's own number and answered
# «رقمك غير مرتبط» to it, and another bot's replies were stored as inbound messages). It is stored
# on the outbound side, as the history import stores the device's own messages.

from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from whatsapp_next.tests.conftest_frappe import ensure_device, fake_provider
from whatsapp_next.webhooks.handlers import handle_event

OWN_NUMBER = "+967782309999"
CUSTOMER = "+967777667663"
OWN_LID = "88352560808107@lid"


def _body(**values) -> dict:
	body = {
		"event": "message.received",
		"device": "WAD-TEST-OWN01",
		"phone_number": OWN_NUMBER.lstrip("+"),
		"message_type": "Text",
		"text": "جاري تنفيذ طلبك",
		"has_text": True,
		"is_edit": False,
		"received_at": "2026-11-15 20:00:00",
	}
	body.update(values)
	message_id = body.setdefault("message_id", f"3EB0OWN{frappe.generate_hash(length=10).upper()}")
	body.setdefault("provider_message_id", message_id)
	body.setdefault("event_id", message_id)
	return body


class TestInboundOwnMessages(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.device = ensure_device(
			device_name="Own Messages", platform_device="WAD-TEST-OWN01", phone=OWN_NUMBER
		)

	def _handle(self, body):
		with fake_provider(), patch.object(frappe, "enqueue") as enqueue:
			provider = frappe.get_attr("whatsapp_next.providers.registry.get_provider")()
			event = provider.parse_webhook({}, body)
			status, links = handle_event(event, webhook_event_name=None, device=self.device)
		return status, links, enqueue

	def _inbound(self, message_id):
		return frappe.db.get_value("WhatsApp Inbound Message", {"provider_message_id": message_id}, "name")

	def _outbound(self, message_id):
		return frappe.db.get_value(
			"WhatsApp Log",
			{"provider_message_id": message_id},
			["name", "phone_e164", "status", "source_type", "body", "message_type"],
			as_dict=True,
		)

	def test_a_message_from_another_linked_device_is_outbound_not_a_command(self):
		body = _body(
			chat=f"{CUSTOMER.lstrip('+')}@s.whatsapp.net",
			chat_jid=f"{CUSTOMER.lstrip('+')}@s.whatsapp.net",
			sender_jid=f"{OWN_NUMBER.lstrip('+')}:44@s.whatsapp.net",
			**{"from": f"{OWN_NUMBER.lstrip('+')}:44@s.whatsapp.net"},
			is_from_me=True,
		)
		status, links, enqueue = self._handle(body)
		self.assertIsNone(self._inbound(body["message_id"]), "the device's own message was stored as inbound")
		row = self._outbound(body["message_id"])
		self.assertIsNotNone(row, "the device's own message is kept on the outbound side")
		self.assertEqual((status, links.get("outbound_message")), ("Processed", row.name))
		self.assertEqual(
			(row.phone_e164, row.status, row.source_type, row.body, row.message_type),
			(CUSTOMER, "Sent", "History", "جاري تنفيذ طلبك", "Text"),
		)
		routed = [
			c
			for c in enqueue.call_args_list
			if "command" in str(c.args[:1]) + str(c.kwargs.get("method", ""))
		]
		self.assertEqual(routed, [], "a command ran on the device's own message")

	def test_a_tap_in_the_chat_with_itself_is_ignored(self):
		body = _body(
			chat=OWN_LID,
			chat_jid=OWN_LID,
			sender_jid=OWN_LID,
			**{"from": OWN_LID},
			is_from_me=True,
			message_type="Interactive Reply",
			text="كشف حساب العميل",
			selected_id="كشف",
		)
		status, _links, _enqueue = self._handle(body)
		self.assertEqual(status, "Ignored")
		self.assertIsNone(self._inbound(body["message_id"]))
		self.assertIsNone(self._outbound(body["message_id"]))

	def test_a_privacy_id_chat_takes_the_number_the_customer_wrote_from(self):
		lid = f"{frappe.generate_hash(length=12)}@lid"
		earlier = _body(
			chat=lid,
			chat_jid=lid,
			sender_jid=lid,
			**{"from": lid},
			is_from_me=False,
			sender_number=CUSTOMER.lstrip("+"),
			text="رصيد",
		)
		self._handle(earlier)
		self.assertIsNotNone(self._inbound(earlier["message_id"]))
		own = _body(
			chat=lid,
			chat_jid=lid,
			sender_jid=f"{OWN_LID.split('@')[0]}:44@lid",
			**{"from": f"{OWN_LID.split('@')[0]}:44@lid"},
			is_from_me=True,
		)
		self._handle(own)
		self.assertIsNone(self._inbound(own["message_id"]))
		self.assertEqual(self._outbound(own["message_id"]).phone_e164, CUSTOMER)

	def test_the_devices_own_number_as_sender_is_own_even_without_the_flag(self):
		body = _body(
			chat=f"{CUSTOMER.lstrip('+')}@s.whatsapp.net",
			chat_jid=f"{CUSTOMER.lstrip('+')}@s.whatsapp.net",
			sender_jid=f"{OWN_NUMBER.lstrip('+')}@s.whatsapp.net",
			**{"from": f"{OWN_NUMBER.lstrip('+')}@s.whatsapp.net"},
			sender_mobile_no=OWN_NUMBER.lstrip("+"),
		)
		body.pop("is_from_me", None)
		self._handle(body)
		self.assertIsNone(self._inbound(body["message_id"]))

	def test_a_customers_message_is_still_inbound(self):
		body = _body(
			chat=f"{CUSTOMER.lstrip('+')}@s.whatsapp.net",
			chat_jid=f"{CUSTOMER.lstrip('+')}@s.whatsapp.net",
			sender_jid=f"{CUSTOMER.lstrip('+')}@s.whatsapp.net",
			**{"from": f"{CUSTOMER.lstrip('+')}@s.whatsapp.net"},
			sender_mobile_no=CUSTOMER.lstrip("+"),
			is_from_me=False,
			text="رصيد",
		)
		status, links, _enqueue = self._handle(body)
		self.assertEqual(status, "Processed")
		self.assertEqual(links.get("inbound_message"), self._inbound(body["message_id"]))

	def test_the_devices_own_reaction_is_not_an_inbound_reaction(self):
		body = _body(
			event="message.reaction",
			chat=f"{CUSTOMER.lstrip('+')}@s.whatsapp.net",
			chat_jid=f"{CUSTOMER.lstrip('+')}@s.whatsapp.net",
			sender_jid=f"{OWN_NUMBER.lstrip('+')}:44@s.whatsapp.net",
			**{"from": f"{OWN_NUMBER.lstrip('+')}:44@s.whatsapp.net"},
			is_from_me=True,
			message_type="reaction",
			reaction="👍",
			reacted_message_id="3EB0SOMEONE",
		)
		status, _links, _enqueue = self._handle(body)
		self.assertEqual(status, "Ignored")
		self.assertIsNone(self._inbound(body["message_id"]))
