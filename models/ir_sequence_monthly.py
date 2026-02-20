import calendar

from odoo import fields, models


class IrSequence(models.Model):
    _inherit = "ir.sequence"

    def _create_date_range_seq(self, date):
        self.ensure_one()
        if self.code != "x_mobile.form.submission":
            return super()._create_date_range_seq(date)

        dt = fields.Date.to_date(date)
        if not dt:
            return super()._create_date_range_seq(date)

        first_day = dt.replace(day=1)
        last_day = dt.replace(day=calendar.monthrange(dt.year, dt.month)[1])

        seq_date_range = self.env["ir.sequence.date_range"].search(
            [
                ("sequence_id", "=", self.id),
                ("date_from", "<=", dt),
                ("date_to", ">=", dt),
            ],
            limit=1,
        )
        if seq_date_range:
            return seq_date_range

        return self.env["ir.sequence.date_range"].sudo().create(
            {
                "date_from": first_day,
                "date_to": last_day,
                "sequence_id": self.id,
                "number_next": 1,
            }
        )
