// whatsapp.ui.GroupMembers — a WhatsApp group's members, managed from the product (owner,
// 2026-11-17): the members with who is an admin, the requests to join, and — through a device that
// administers the group — adding members, removing them, making or unmaking admins, approving or
// rejecting requests. A member WhatsApp shows by a hidden id only, with no number known, is listed
// without changes: the engine acts on numbers. Data through `wa_groups.get_members`,
// `wa_groups.change_members` and `devices.list_devices`; adding opens `whatsapp.ui.GroupCreate`.

import ui from "../_core/index.js";

const RESULT = {
	done: () => __("Done"),
	not_allowed: () => __("Not in «Send only to»"),
	failed: () => __("WhatsApp refused it"),
};

whatsapp.ui.GroupMembers = class GroupMembers {
	/**
	 * @param {Object} opts
	 * @param {string} opts.group — the group's JID (`…@g.us`)
	 * @param {string} [opts.name] — its name, for the title until the members load
	 * @param {string} [opts.device] — the device to read and change it through
	 * @param {boolean} [opts.can_change] — offer the changes (a Manager); reading needs an Agent
	 * @param {Function} [opts.on_change] — `() => void` after a change
	 */
	constructor(opts = {}) {
		this.opts = Object.assign({}, opts);
		this.device = opts.device || null;
		this.data = null;
		this.build_dialog();
		this.dialog.show();
		this.load_devices().then(() => this.load());
	}

	build_dialog() {
		this.dialog = new frappe.ui.Dialog({
			title: __("Members of {0}", [this.opts.name || this.opts.group]),
			size: "large",
			fields: [
				{
					fieldtype: "Select",
					fieldname: "device",
					label: __("Device"),
					options: [],
					description: __("The group is read and changed through this number; only an admin of the group can change it."),
					change: () => {
						const value = this.dialog.get_value("device");
						if (value && value !== this.device) {
							this.device = value;
							this.load();
						}
					},
				},
				{ fieldtype: "HTML", fieldname: "body" },
			],
		});
		this.dialog.$wrapper.addClass("whatsapp-kit whatsapp-kit-sheet whatsapp-kit-group-create whatsapp-kit-group-members");
		this.$body = this.dialog.get_field("body").$wrapper;
		this.$body.on("click", "[data-act]", (e) => this.on_action($(e.currentTarget)));
	}

	load_devices() {
		return ui.call("devices.list_devices", {}, { silent: true }).then((r) => {
			const rows = ((r && r.rows) || r || []).filter((d) => d.status === "Connected" && !cint(d.disabled));
			const field = this.dialog.get_field("device");
			field.df.options = rows.map((d) => ({ value: d.name, label: d.device_name || d.name }));
			field.refresh();
			const preferred = rows.find((d) => d.name === this.device) || rows.find((d) => cint(d.is_default)) || rows[0];
			this.device = preferred ? preferred.name : this.device;
			if (this.device) this.dialog.set_value("device", this.device);
		});
	}

	load() {
		this.$body.html(`<p class="whatsapp-kit-group-members__state">${ui.escape(__("Reading the group from WhatsApp…"))}</p>`);
		return ui
			.call("wa_groups.get_members", { group: this.opts.group, device: this.device || null }, { silent: true })
			.then((r) => {
				this.data = r;
				this.device = r.device;
				this.dialog.set_title(__("Members of {0}", [r.name]));
				this.draw();
			})
			.catch((err) => {
				this.$body.html(`<p class="whatsapp-kit-group-members__state whatsapp-kit-group-members__state--error">${ui.escape(
					(err && (err.message || err.toString())) || __("The group could not be read.")
				)}</p>`);
			});
	}

	can_change() {
		return !!(this.opts.can_change && this.data && this.data.device_is_admin);
	}

	draw() {
		const escape_html = ui.escape;
		const d = this.data;
		const change = this.can_change();
		const admins = d.members.filter((m) => m.is_admin).length;
		const note = !this.opts.can_change
			? ""
			: d.device_is_admin
				? ""
				: `<p class="whatsapp-kit-group-members__note">${escape_html(__("This device is not an admin of the group: it shows the members, but cannot change them. Choose a device that is."))}</p>`;
		const head = `<div class="whatsapp-kit-group-create__bar">
			<span class="whatsapp-kit-group-create__count">${escape_html(whatsapp.ui.plural(d.members.length, { one: __("{0} member"), other: __("{0} members") }))} · ${escape_html(whatsapp.ui.plural(admins, { one: __("{0} admin"), other: __("{0} admins") }))}</span>
			${change ? `<button type="button" class="btn btn-xs btn-default" data-act="add">${ui.icon("es-line-add-people", "xs")} ${escape_html(__("Add members"))}</button>` : ""}
		</div>`;
		const row = (m, actions) => `<li data-phone="${escape_html(m.phone_e164 || "")}" data-name="${escape_html(m.display_name || m.phone_e164 || "")}">
			<span class="whatsapp-kit-group-create__name">${escape_html(m.display_name || (m.phone_e164 ? "" : __("Number hidden by WhatsApp")))}</span>
			${m.phone_e164 ? `<bdi class="whatsapp-kit-tabular" dir="ltr">${escape_html(whatsapp.ui.phone ? whatsapp.ui.phone(m.phone_e164) : m.phone_e164)}</bdi>` : ""}
			${m.is_super_admin ? whatsapp.ui.StatusBadge.html({ label: __("Owner"), colour: "blue" }) : m.is_admin ? whatsapp.ui.StatusBadge.html({ label: __("Admin"), colour: "green" }) : ""}
			<span class="whatsapp-kit-group-members__acts">${actions}</span>
		</li>`;
		const button = (act, label, danger) =>
			`<button type="button" class="btn btn-xs btn-default${danger ? " whatsapp-kit-group-members__danger" : ""}" data-act="${act}">${escape_html(label)}</button>`;
		const member_actions = (m) => {
			if (!change || !m.phone_e164 || m.is_super_admin || m.is_this_device) return "";
			return (m.is_admin ? button("remove_admin", __("Remove admin")) : button("make_admin", __("Make admin"))) + button("remove", __("Remove"), true);
		};
		const members = `<ul class="whatsapp-kit-group-create__list">${d.members.map((m) => row(m, member_actions(m))).join("")}</ul>`;
		const requests = d.requests.length
			? `<h5 class="whatsapp-kit-group-members__title">${escape_html(__("Requests to join"))}</h5>
				<ul class="whatsapp-kit-group-create__list">${d.requests
					.map((m) => row(m, change && m.phone_e164 ? button("approve", __("Approve")) + button("reject", __("Reject"), true) : ""))
					.join("")}</ul>`
			: "";
		this.$body.html(`${note}<div class="whatsapp-kit-group-create__members">${head}${members}${requests}</div>`);
	}

	on_action($btn) {
		const act = $btn.data("act");
		if (act === "add") {
			new whatsapp.ui.GroupCreate({
				group: { jid: this.data.jid, name: this.data.name, device: this.device },
				on_created: () => this.after_change(),
			});
			return;
		}
		const $row = $btn.closest("li");
		const phone = $row.data("phone");
		const name = $row.data("name");
		const run = () =>
			ui
				.call("wa_groups.change_members", { group: this.data.jid, action: act, members: [phone], device: this.device }, { freeze: true })
				.then((r) => {
					const status = ((r && r.members) || [])[0];
					const said = (RESULT[status && status.status] || RESULT.failed)();
					if (status && status.status === "done") whatsapp.ui.Toast.success(said);
					else whatsapp.ui.Toast.error(said);
					this.after_change();
				})
				.catch((err) => whatsapp.ui.Toast.error(err));
		if (act === "remove" || act === "reject") {
			frappe.confirm(
				act === "remove" ? __("Remove {0} from the group?", [ui.escape(name)]) : __("Reject {0}'s request to join?", [ui.escape(name)]),
				run
			);
			return;
		}
		run();
	}

	after_change() {
		this.load();
		if (this.opts.on_change) this.opts.on_change();
	}
};
