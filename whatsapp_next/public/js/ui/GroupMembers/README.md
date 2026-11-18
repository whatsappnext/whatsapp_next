# GroupMembers

A WhatsApp group's members, managed from the product: a `frappe.ui.Dialog` with the device the
group is read and changed through (connected devices; the group's own device first), the members
with who owns and who administers the group, and the requests to join. Through a device that is an
admin of the group, a Manager can add members (it opens `GroupCreate` in its «add to this group»
mode), remove a member, make or unmake an admin, and approve or reject a request. A device that is
no admin shows the members with a note and no changes. A member WhatsApp shows by a hidden id only,
whose number the site does not know, is listed as «Number hidden by WhatsApp» without changes: the
engine acts on numbers. Removing and rejecting ask first; every change says what WhatsApp did.

## Usage

```js
new whatsapp.ui.GroupMembers({
	group: "120363…@g.us",
	name: "Sales team",
	device: "WA-DEV-033",        // optional: else the group's own, else the default device
	can_change: true,            // a Manager; reading needs an Agent
	on_change: () => listview.refresh(),
});
```

Options: `group`, `name?`, `device?`, `can_change?`, `on_change?()`.
API keys: `wa_groups.get_members` → `{jid, name, device, device_is_admin, members[], requests[]}`
(each `{phone_e164, jid, display_name, contact, is_admin, is_super_admin, is_this_device}`),
`wa_groups.change_members` (`action`: `remove`, `make_admin`, `remove_admin`, `approve`, `reject`)
→ `{members[{phone, status: done|failed|unconfirmed|not_allowed}]}`, `devices.list_devices`.

## Live use

WhatsApp Numbers: «Members» on a group's row and in its conversation panel.

## Design gate
- A Frappe dialog (`frappe.ui.Dialog`): focus trap, Escape and the device field's label come from
  Frappe.
- Every action button in a row carries an `aria-label` naming its member («Remove: Ali»); the
  visible word alone («Remove») said nothing of whom to a screen reader (fixed in this review).
- The owner and the admins are written badges (StatusBadge with its label), never colour alone.
- Removing a member and rejecting a request ask first; every change ends in a toast saying what
  WhatsApp did (done, refused, or not in «Send only to»).
- Loading and error are states of their own; a device that is no admin of the group says so and
  shows no buttons, rather than buttons that fail.
- On a phone a row wraps: the buttons go under the name and number, at full width.
