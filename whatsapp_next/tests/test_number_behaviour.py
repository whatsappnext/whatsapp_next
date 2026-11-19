# What the company's number shows the people who write to it (owner, 2026-11-19, after Top Land's
# complaint): read receipts only when chosen — never by default — and only once per message;
# «typing…» and a wait before a reply, so it does not answer like a machine; and a device may keep
# its own choices. The provider is replaced in every test: nothing leaves.

from __future__ import annotations

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from whatsapp_next.services import number_behaviour, receipts
from whatsapp_next.tests.conftest_frappe import ensure_device

# the owner's own numbers and group (tests too send only to them), none with messages on the dev site
PERSON = "+967779972929"
PERSON_JID = "967779972929@s.whatsapp.net"
OTHER = "+967782309999"
GROUP = "120363026488895333@g.us"
SETTINGS = "WhatsApp Settings"
FIELDS = ("mark_read_mode", "typing_indicator", "typing_lasts_by_reply_length", "reply_delay_min_seconds", "reply_delay_max_seconds")


class TestNumberBehaviour(IntegrationTestCase):
	def setUp(self):
		self.device = frappe.db.get_value("WhatsApp Device", {}, "name") or ensure_device()
		saved = {f: frappe.db.get_value("Singles", {"doctype": SETTINGS, "field": f}, "value", order_by=None) for f in FIELDS}
		self.addCleanup(self._restore_settings, saved)
		before = frappe.db.get_value("WhatsApp Device", self.device, "number_behaviour")
		self.addCleanup(frappe.db.set_value, "WhatsApp Device", self.device, "number_behaviour", before)
		self.provider = MagicMock()
		patcher = patch("whatsapp_next.providers.registry.get_provider", return_value=self.provider)
		patcher.start()
		self.addCleanup(patcher.stop)

	def _restore_settings(self, saved):
		for field, value in saved.items():
			frappe.db.delete("Singles", {"doctype": SETTINGS, "field": field})
			if value is not None:
				frappe.db.set_single_value(SETTINGS, field, value)
		frappe.clear_document_cache(SETTINGS, SETTINGS)

	def _settings(self, **values):
		for field, value in values.items():
			frappe.db.set_single_value(SETTINGS, field, value)
		frappe.clear_document_cache(SETTINGS, SETTINGS)

	def _inbound(self, phone=PERSON, chat=PERSON_JID, sender=PERSON_JID, group=0, **values):
		doc = frappe.get_doc(
			{
				"doctype": "WhatsApp Inbound Message",
				"device": self.device,
				"message_type": "Text",
				"body": "hi",
				"provider_message_id": f"NB-{frappe.generate_hash(length=10)}",
				"command_status": "None",
				"phone_e164": phone,
				"chat_jid": chat,
				"sender_jid": sender,
				"is_group": group,
				**values,
			}
		)
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True)
		self.addCleanup(frappe.db.delete, "WhatsApp Inbound Message", {"name": doc.name})
		return doc

	# ---- the default: nothing is marked ---------------------------------------------------------

	def test_a_site_that_never_chose_marks_nothing(self):
		frappe.db.delete("Singles", {"doctype": SETTINGS, "field": "mark_read_mode"})
		frappe.clear_document_cache(SETTINGS, SETTINGS)
		self.assertEqual(number_behaviour.for_device(self.device).mark_read_mode, "Do not mark as read")
		doc = self._inbound()
		with patch("frappe.enqueue") as enqueue:
			receipts.on_received(doc.name)
			receipts.on_command_replied(doc.name)
		enqueue.assert_not_called()
		self.provider.mark_read.assert_not_called()

	def test_a_value_the_list_no_longer_has_marks_nothing(self):
		frappe.db.set_single_value(SETTINGS, "mark_read_mode", "Some old value")
		frappe.clear_document_cache(SETTINGS, SETTINGS)
		self.assertEqual(number_behaviour.for_device(self.device).mark_read_mode, "Do not mark as read")
		# the old «Never» too, and saving the settings with it is not refused
		frappe.db.set_single_value(SETTINGS, "mark_read_mode", "Never")
		frappe.clear_document_cache(SETTINGS, SETTINGS)
		doc = frappe.get_doc(SETTINGS)
		doc.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_single_value(SETTINGS, "mark_read_mode"), "Do not mark as read")

	def test_receiving_marks_only_when_every_message_is_chosen(self):
		doc = self._inbound()
		for mode in ("Do not mark as read", "When the chat is opened", "When a command replies"):
			self._settings(mark_read_mode=mode)
			with patch("frappe.enqueue") as enqueue:
				receipts.on_received(doc.name)
			enqueue.assert_not_called()
		self._settings(mark_read_mode="Every received message")
		with patch("frappe.enqueue") as enqueue:
			receipts.on_received(doc.name)
		enqueue.assert_called_once()

	# ---- when the chat is opened -----------------------------------------------------------------

	def test_opening_the_chat_marks_its_unread_messages_once(self):
		self._settings(mark_read_mode="When the chat is opened")
		first, second = self._inbound(), self._inbound()
		already = self._inbound(read_receipt_sent=1)
		elsewhere = self._inbound(phone=OTHER, chat="967782309999@s.whatsapp.net", sender="967782309999@s.whatsapp.net")
		answer = receipts.mark_conversation_read(PERSON)
		self.assertEqual(answer["marked"], 2)
		self.provider.mark_read.assert_called_once()
		_device, chat, ids, sender = self.provider.mark_read.call_args.args
		self.assertEqual((chat, sorted(ids), sender), (PERSON_JID, sorted([first.provider_message_id, second.provider_message_id]), None))
		self.assertNotIn(already.provider_message_id, ids)
		self.assertNotIn(elsewhere.provider_message_id, ids)
		self.assertEqual(frappe.db.get_value("WhatsApp Inbound Message", first.name, "read_receipt_sent"), 1)
		self.provider.mark_read.reset_mock()
		self.assertEqual(receipts.mark_conversation_read(PERSON)["marked"], 0, "opened again: nothing is sent")
		self.provider.mark_read.assert_not_called()

	def test_opening_a_group_marks_each_senders_messages_with_their_jid(self):
		self._settings(mark_read_mode="When the chat is opened")
		one = self._inbound(phone=None, chat=GROUP, sender="39883435471066@lid", group=1)
		two = self._inbound(phone=None, chat=GROUP, sender="88352560808107@lid", group=1)
		receipts.mark_conversation_read(GROUP)
		calls = sorted((c.args[1], tuple(c.args[2]), c.args[3]) for c in self.provider.mark_read.call_args_list)
		self.assertEqual(
			calls,
			sorted([(GROUP, (one.provider_message_id,), "39883435471066@lid"), (GROUP, (two.provider_message_id,), "88352560808107@lid")]),
		)

	def test_opening_the_chat_sends_nothing_unless_chosen(self):
		self._inbound()
		for mode in ("Do not mark as read", "When a command replies", "Every received message"):
			self._settings(mark_read_mode=mode)
			self.assertEqual(receipts.mark_conversation_read(PERSON)["marked"], 0)
		self.provider.mark_read.assert_not_called()

	def test_a_receipt_that_fails_never_stops_the_chat(self):
		self._settings(mark_read_mode="When the chat is opened")
		doc = self._inbound()
		self.provider.mark_read.side_effect = Exception("device offline")
		before = frappe.db.count("Error Log", {"method": "WhatsApp read receipt not sent"})
		self.assertEqual(receipts.mark_conversation_read(PERSON)["marked"], 0)
		self.assertEqual(frappe.db.count("Error Log", {"method": "WhatsApp read receipt not sent"}), before + 1)
		self.assertEqual(frappe.db.get_value("WhatsApp Inbound Message", doc.name, "read_receipt_sent"), 0, "tried again next time")

	def test_the_chat_screen_asks_through_its_own_endpoint(self):
		from whatsapp_next.api.v1 import messages

		self._settings(mark_read_mode="When the chat is opened")
		self._inbound()
		self.assertEqual(messages.mark_conversation_read(key=PERSON)["marked"], 1)

	# ---- a device with its own choices ----------------------------------------------------------

	def test_a_device_keeps_its_own_choices(self):
		self._settings(mark_read_mode="Do not mark as read")
		frappe.db.set_value(
			"WhatsApp Device",
			self.device,
			{"number_behaviour": "Own settings", "mark_read_mode": "When a command replies"},
		)
		doc = self._inbound()
		receipts.on_command_replied(doc.name)
		self.provider.mark_read.assert_called_once()
		receipts.on_command_replied(doc.name)
		self.provider.mark_read.assert_called_once()  # marked once only

	# ---- the wait before a reply -------------------------------------------------------------------

	def test_the_wait_before_a_reply(self):
		self._settings(typing_indicator=1, typing_lasts_by_reply_length=0, reply_delay_min_seconds=2, reply_delay_max_seconds=5)
		for _ in range(20):
			self.assertTrue(2 <= number_behaviour.seconds_to_wait_before_reply(self.device, "ok") <= 5)
		self._settings(typing_lasts_by_reply_length=1)
		long_reply = "x" * 400
		self.assertGreater(number_behaviour.seconds_to_wait_before_reply(self.device, long_reply), 5, "typed like a person")
		self.assertLessEqual(number_behaviour.seconds_to_wait_before_reply(self.device, "x" * 10000), number_behaviour.MAX_WAIT_BEFORE_REPLY_SECONDS)
		self._settings(reply_delay_min_seconds=0, reply_delay_max_seconds=0, typing_indicator=0)
		self.assertEqual(number_behaviour.seconds_to_wait_before_reply(self.device, long_reply), 0)

	def test_a_command_reply_waits_the_chosen_seconds(self):
		from frappe.utils import add_to_date, now_datetime

		from whatsapp_next.services import command_router

		self._settings(typing_indicator=0, reply_delay_min_seconds=3, reply_delay_max_seconds=3)
		frappe.flags.wa_wait_before_reply_in_test = True
		self.addCleanup(setattr, frappe.flags, "wa_wait_before_reply_in_test", False)
		inbound = frappe._dict(name=None, device=self.device, received_at=now_datetime())
		with patch.object(command_router.time, "sleep") as sleep:
			command_router._wait_before_reply(inbound.get, [{"body": "ok"}])
		self.assertAlmostEqual(sleep.call_args.args[0], 3, delta=1)
		# the command already took longer than the wait: the reply goes at once
		inbound.received_at = add_to_date(now_datetime(), seconds=-10)
		with patch.object(command_router.time, "sleep") as sleep:
			command_router._wait_before_reply(inbound.get, [{"body": "ok"}])
		sleep.assert_not_called()

	def test_a_device_saves_its_own_choices_through_its_endpoint(self):
		from whatsapp_next.api.v1 import devices

		frappe.db.set_value("WhatsApp Device", self.device, "mark_read_mode", "Never")
		self.assertEqual(devices.get_number_behaviour(device=self.device)["mark_read_mode"], "Do not mark as read", "an old value shows as the default")
		before = devices.get_number_behaviour(device=self.device)
		self.addCleanup(frappe.db.set_value, "WhatsApp Device", self.device, dict(before))
		saved = devices.set_number_behaviour(
			device=self.device, number_behaviour="Own settings", mark_read_mode="When the chat is opened",
			reply_delay_min_seconds=1, reply_delay_max_seconds=3,
		)
		self.assertEqual((saved["number_behaviour"], saved["mark_read_mode"]), ("Own settings", "When the chat is opened"))
		self.assertEqual(number_behaviour.for_device(self.device).mark_read_mode, "When the chat is opened")
		with self.assertRaises(frappe.ValidationError):
			devices.set_number_behaviour(device=self.device, number_behaviour="Own settings", mark_read_mode="Always")
