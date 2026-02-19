/** @odoo-module **/

import { listView } from "@web/views/list/list_view";
import { ListController } from "@web/views/list/list_controller";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { useService } from "@web/core/utils/hooks";
import { onMounted, useRef, useState } from "@odoo/owl";

class MFormSubmissionListController extends ListController {
    setup() {
        super.setup();
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.codeInputRef = useRef("mformCodeInput");
        this.state = useState({
            code: "",
        });

        onMounted(() => {
            // Best-effort focus for scanner workflows.
            // Note: browsers may ignore autofocus depending on user gesture policy.
            try {
                this.codeInputRef.el && this.codeInputRef.el.focus();
            } catch {
                // ignore
            }
        });
    }

    _beep({ type }) {
        // WebAudio beep: no external assets, works in most modern browsers after a user gesture.
        // type: "success" | "error"
        try {
            const Ctx = window.AudioContext || window.webkitAudioContext;
            if (!Ctx) {
                return;
            }
            const ctx = new Ctx();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = "sine";
            const now = ctx.currentTime;

            if (type === "success") {
                osc.frequency.setValueAtTime(880, now);
                osc.frequency.setValueAtTime(1320, now + 0.08);
                gain.gain.setValueAtTime(0.0001, now);
                gain.gain.exponentialRampToValueAtTime(0.12, now + 0.01);
                gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.16);
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.start(now);
                osc.stop(now + 0.18);
            } else {
                osc.frequency.setValueAtTime(220, now);
                gain.gain.setValueAtTime(0.0001, now);
                gain.gain.exponentialRampToValueAtTime(0.16, now + 0.01);
                gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.22);
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.start(now);
                osc.stop(now + 0.24);
            }

            // Ensure the context is closed soon after.
            window.setTimeout(() => {
                try {
                    ctx.close();
                } catch {
                    // ignore
                }
            }, 400);
        } catch {
            // ignore audio errors
        }
    }

    _getWizardContext() {
        // If the list is opened from a specific form, Odoo usually passes default_form_id.
        // Keep it generic otherwise (no form restriction).
        const ctx = { ...(this.props.context || {}) };
        return ctx;
    }

    async _applyWizard(action) {
        const code = (this.state.code || "").trim();
        if (!code) {
            this.notification.add("Please input a code.", { type: "warning" });
            return;
        }
        const ctx = this._getWizardContext();
        try {
            const [wizardId] = await this.orm.create(
                "x_mobile.form.submission.confirm.wizard",
                [
                    {
                        code,
                        action,
                    },
                ],
                { context: ctx }
            );
            const result = await this.orm.call(
                "x_mobile.form.submission.confirm.wizard",
                "action_apply_json",
                [[wizardId]],
                { context: ctx }
            );
            if (result && result.ok) {
                // Success: side notification only (no popup).
                this.notification.add(result.message || _t("Done."), {
                    title: result.title || _t("Done"),
                    type: result.type || "success",
                    sticky: false,
                });
                this._beep({ type: "success" });
                this.state.code = "";
                await this.actionService.doAction({ type: "ir.actions.client", tag: "reload" });
            } else {
                // Failure: popup dialog + error beep.
                const title = (result && result.title) || _t("Confirm Failed");
                const message = (result && result.message) || _t("Please check the code and try again.");
                this.dialog.add(AlertDialog, {
                    title,
                    body: message,
                });
                this._beep({ type: "error" });
            }
        } catch (e) {
            this._beep({ type: "error" });
            const msg =
                (e && e.message) ||
                (e && e.data && e.data.message) ||
                "Confirm failed.";
            this.dialog.add(AlertDialog, {
                title: _t("Confirm Failed"),
                body: msg,
            });
        } finally {
            try {
                this.codeInputRef.el && this.codeInputRef.el.focus();
            } catch {
                // ignore
            }
        }
    }

    async onMformConfirmByCode() {
        await this._applyWizard("confirm");
    }

    async onMformUnconfirmByCode() {
        await this._applyWizard("unconfirm");
    }

    onMformCodeKeydown(ev) {
        // Owl in Odoo 19 doesn't support `t-on-keydown.enter`, so handle Enter manually.
        if (!ev || ev.isComposing) {
            return;
        }
        if (ev.key === "Enter") {
            ev.preventDefault();
            ev.stopPropagation();
            this.onMformConfirmByCode();
        }
    }

    onMformCodeInput(ev) {
        if (!ev || !ev.target) {
            return;
        }
        this.state.code = ev.target.value || "";
    }
}

export const mformSubmissionListView = {
    ...listView,
    Controller: MFormSubmissionListController,
    // Don't render custom buttons in the left control-panel button area.
    // We'll inject them next to the SearchBar via our custom controller template.
    buttonTemplate: "web.ListView.Buttons",
};

// Use a custom template so we can position the confirm box on the right side (near SearchBar).
MFormSubmissionListController.template = "mobile_form_builder.MFormSubmissionListView";

registry.category("views").add("mform_submission_list", mformSubmissionListView);
