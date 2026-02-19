/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";
import { onMounted, onWillUnmount } from "@odoo/owl";

/**
 * Make Enter key apply the confirm wizard, because `data-hotkey="enter"` is not
 * reliably supported in Odoo's hotkey system.
 *
 * Scope is limited to our wizard model to avoid impacting other forms.
 */
patch(FormController.prototype, {
    setup() {
        super.setup();

        // `resModel` is provided by standard view props.
        if (this.props?.resModel !== "x_mobile.form.submission.confirm.wizard") {
            return;
        }

        const handler = (ev) => {
            if (ev.defaultPrevented) {
                return;
            }
            if (ev.isComposing) {
                return;
            }
            if (ev.key !== "Enter") {
                return;
            }
            // Avoid interfering with explicit buttons.
            if (ev.target && (ev.target.tagName === "BUTTON" || ev.target.tagName === "A")) {
                return;
            }

            // Confirm wizard doesn't need Enter for anything else: treat Enter as "Apply".
            const btn = this.el && this.el.querySelector('button[name="action_apply"]');
            if (btn) {
                ev.preventDefault();
                ev.stopPropagation();
                btn.click();
            }
        };

        onMounted(() => {
            try {
                this.el && this.el.addEventListener("keydown", handler, true);
            } catch {
                // ignore
            }
        });
        onWillUnmount(() => {
            try {
                this.el && this.el.removeEventListener("keydown", handler, true);
            } catch {
                // ignore
            }
        });
    },
});

