from odoo import _, api, fields, models
from odoo.exceptions import UserError


class MobileFormSubmissionConfirmWizard(models.TransientModel):
    _name = "x_mobile.form.submission.confirm.wizard"
    _description = "Mobile Form Submission Confirmation Wizard"

    form_id = fields.Many2one("x_mobile.form", readonly=True)
    code = fields.Char(string="Code", required=True, help="Scan or type the confirmation code.")
    action = fields.Selection(
        [("confirm", "Confirm"), ("unconfirm", "Unconfirm")],
        default="confirm",
        required=True,
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        form_id = self.env.context.get("default_form_id") or self.env.context.get("form_id")
        if form_id and "form_id" in fields_list:
            res["form_id"] = int(form_id)
        return res

    def _find_submission(self, code):
        self.ensure_one()
        code = (code or "").strip()
        if not code:
            raise UserError(_("Please input a code."))

        def _search():
            dom = [
                ("active", "=", True),
                "|",
                ("confirm_key1_value", "=", code),
                ("confirm_key2_value", "=", code),
            ]
            if self.form_id:
                dom = [("form_id", "=", self.form_id.id)] + dom
            return self.env["x_mobile.form.submission"].search(dom, limit=2)

        subs = _search()
        # NOTE:
        # Avoid doing full-table backfill in this hot path.
        # On large datasets, recomputing confirm keys here turns a single miss
        # into seconds of blocking time and causes heavy contention under load.
        # Backfill is already handled when form confirm fields are changed.

        if not subs:
            raise UserError(_("No submission found for this code."))
        if len(subs) > 1:
            raise UserError(_("Conflict: multiple submissions match this code."))
        return subs[0]

    def action_apply(self):
        self.ensure_one()
        result = self.action_apply_json()
        title = result["title"]
        msg = result["message"]
        notif_type = result["type"]

        # Stay on the submissions list: show a notification and reload the view.
        params = {
            "title": title,
            "message": msg,
            "type": notif_type,
            "sticky": False,
        }
        if result.get("ok"):
            params["next"] = {"type": "ir.actions.client", "tag": "reload"}

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": params,
        }

    def action_apply_json(self):
        """JSON-friendly apply result for list page UX control."""
        self.ensure_one()
        try:
            sub = self._find_submission(self.code)
            if self.action == "confirm":
                sub.action_confirm()
                return {
                    "ok": True,
                    "title": _("Confirmed"),
                    "message": _("Submission confirmed."),
                    "type": "success",
                }
            sub.action_unconfirm()
            return {
                "ok": True,
                "title": _("Unconfirmed"),
                "message": _("Submission unconfirmed."),
                "type": "warning",
            }
        except UserError as e:
            return {
                "ok": False,
                "title": _("Confirm Failed"),
                "message": str(e),
                "type": "danger",
            }
