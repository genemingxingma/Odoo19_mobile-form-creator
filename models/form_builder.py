import io
import json
import re
import uuid
from html import escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


def _mform_company_tz(env):
    """Return company timezone string (fallback to user tz, then UTC)."""
    company = env.company
    tz = ""
    try:
        tz = (getattr(company, "tz", "") or "").strip()
    except Exception:
        tz = ""
    if not tz:
        try:
            tz = (getattr(company.partner_id, "tz", "") or "").strip()
        except Exception:
            tz = ""
    if not tz:
        tz = (env.user.tz or "").strip()
    return tz or "UTC"


def _mform_format_dt_company_tz(env, dt):
    """Format an Odoo UTC datetime in the company's timezone."""
    if not dt:
        return ""
    tz = _mform_company_tz(env)
    # Use Odoo's timezone conversion helper via context tz.
    try:
        local_dt = fields.Datetime.context_timestamp(env.user.with_context(tz=tz), dt)
    except Exception:
        local_dt = dt
    try:
        return local_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(local_dt)


class MobileForm(models.Model):
    _name = "x_mobile.form"
    _description = "Mobile Collection Form"

    name = fields.Char(required=True)
    related_partner_id = fields.Many2one(
        "res.partner",
        string="Related Party",
        help="Related company or individual from contacts.",
        ondelete="set null",
    )
    active = fields.Boolean(default=True)
    is_enabled = fields.Boolean(string="Enabled for Public", default=True)
    allow_repeat_client_submit = fields.Boolean(
        string="Allow Repeated Submit From Same Client",
        default=True,
        help="If disabled, the same browser client can submit this form only once.",
    )
    description = fields.Text()
    success_message = fields.Text(
        string="Submission Success Message",
        default="Thank you for your submission.",
        help="Message shown after user submits the form.",
    )
    closed_message = fields.Text(
        string="Public Form Closed Message",
        default="This form is currently closed.",
        help="Message shown to public users when this form is not enabled for public access.",
    )
    confirm_component_id_1 = fields.Many2one(
        "x_mobile.form.component",
        string="Confirmation Field 1",
        domain="[('form_id', '=', id), ('component_type', 'not in', ('display','image','section','signature','file_upload','checkbox'))]",
        help="Used for staff confirmation by scanning/entering a code. The value will be copied onto the submission record.",
    )
    confirm_component_id_2 = fields.Many2one(
        "x_mobile.form.component",
        string="Confirmation Field 2",
        domain="[('form_id', '=', id), ('component_type', 'not in', ('display','image','section','signature','file_upload','checkbox'))]",
        help="Optional second confirmation field. Either field can match during confirmation.",
    )
    access_token = fields.Char(readonly=True, copy=False, index=True)
    share_url = fields.Char(compute="_compute_share_url", readonly=True, compute_sudo=True)
    qr_code_fallback_html = fields.Html(compute="_compute_share_url", sanitize=False, readonly=True, compute_sudo=True)
    component_ids = fields.One2many("x_mobile.form.component", "form_id", string="Components")
    submission_ids = fields.One2many("x_mobile.form.submission", "form_id", string="Submissions")
    submission_count = fields.Integer(compute="_compute_submission_count")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.setdefault("access_token", uuid.uuid4().hex)
        return super().create(vals_list)

    def _normalize_confirm_value(self, value):
        """Normalize arbitrary answer payload values into a comparable string."""
        if value is None:
            return ""
        if isinstance(value, dict):
            # Prefer common scalar keys if present.
            for k in ("text", "value", "code", "raw", "data"):
                v = value.get(k)
                if isinstance(v, (str, int, float)) and str(v).strip():
                    return str(v).strip()
            return ""
        if isinstance(value, (list, tuple, set)):
            parts = [str(v).strip() for v in value if str(v).strip()]
            return ",".join(parts)
        return str(value).strip()

    def _compute_confirm_values_from_answers(self, answers):
        self.ensure_one()
        answers = answers or {}
        c1 = self.confirm_component_id_1
        c2 = self.confirm_component_id_2
        v1 = self._normalize_confirm_value(answers.get(c1.key)) if c1 and c1.key else ""
        v2 = self._normalize_confirm_value(answers.get(c2.key)) if c2 and c2.key else ""
        return v1, v2

    def _recompute_confirm_keys_for_submissions(self):
        """Backfill confirm_key*_value for existing submissions based on answer_json."""
        import json

        for form in self:
            if not form.submission_ids:
                continue
            for sub in form.submission_ids:
                try:
                    answers = json.loads(sub.answer_json or "{}")
                    if not isinstance(answers, dict):
                        answers = {}
                except Exception:
                    answers = {}
                v1, v2 = form._compute_confirm_values_from_answers(answers)
                sub.sudo().write({"confirm_key1_value": v1, "confirm_key2_value": v2})

    def write(self, vals):
        recompute = any(k in vals for k in ("confirm_component_id_1", "confirm_component_id_2"))
        res = super().write(vals)
        if recompute:
            self._recompute_confirm_keys_for_submissions()
        return res

    def _compute_submission_count(self):
        grouped = self.env["x_mobile.form.submission"].read_group(
            [("form_id", "in", self.ids)], ["form_id"], ["form_id"]
        )
        counts = {item["form_id"][0]: item["form_id_count"] for item in grouped}
        for record in self:
            record.submission_count = counts.get(record.id, 0)

    def _compute_share_url(self):
        base_url = (self.env["ir.config_parameter"].sudo().get_param("web.base.url", "") or "").rstrip("/")
        for record in self:
            if not record.access_token:
                token = uuid.uuid4().hex
                try:
                    record.sudo().write({"access_token": token})
                except Exception:
                    record.access_token = token
            share_path = f"/mform/{record.access_token}"
            share = f"{base_url}{share_path}" if base_url else share_path
            record.share_url = share
            local_qr_url = f"/mform/qr/{record.access_token}.png"
            record.qr_code_fallback_html = (
                f'<div style="padding-top:8px;">'
                f'<img src="{local_qr_url}" alt="QR Code" '
                f'style="max-width:220px;max-height:220px;border:1px solid #dfe3ea;border-radius:8px;"/>'
                f'<div style="margin-top:6px;"><a href="{local_qr_url}" target="_blank">Open QR</a></div>'
                f"</div>"
            )
    def action_open_public_form(self):
        self.ensure_one()
        if not self.share_url:
            raise UserError(_("Unable to build share URL."))
        return {
            "type": "ir.actions.act_url",
            "url": self.share_url,
            "target": "self",
        }

    def action_view_submissions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Submissions"),
            "res_model": "x_mobile.form.submission",
            "view_mode": "list,form",
            "domain": [("form_id", "=", self.id)],
            "context": {"default_form_id": self.id},
        }

    def unlink(self):
        for record in self:
            if record.submission_count or record.submission_ids:
                raise UserError(
                    _("Cannot delete form '%s' because it already has submitted records.") % (record.name,)
                )
        return super().unlink()


class MobileFormComponent(models.Model):
    _name = "x_mobile.form.component"
    _description = "Mobile Form Component"
    _order = "sequence, id"

    COMPONENT_TYPES = [
        ("input", "Input"),
        ("email", "Email"),
        ("formatted_number", "Formatted Number"),
        ("number_wheel", "Number Wheel"),
        ("textarea", "Textarea"),
        ("multiline_text", "Multiline Text"),
        ("age_auto", "Age (Auto)"),
        ("file_upload", "File Upload"),
        ("section", "Section"),
        ("display", "Text Display"),
        ("image", "Image"),
        ("radio", "Radio"),
        ("select", "Select"),
        ("checkbox", "Checkbox"),
        ("date", "Date"),
        ("signature", "Signature"),
        ("barcode_scan", "Camera Barcode Scan"),
    ]

    form_id = fields.Many2one("x_mobile.form", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Label", required=True)
    key = fields.Char(required=True, help="Unique key in form submission payload")
    component_type = fields.Selection(COMPONENT_TYPES, required=True, default="input")
    required = fields.Boolean(default=False)
    include_in_export = fields.Boolean(
        string="Include in Excel Export",
        default=True,
        help="If enabled, this field will be exported as a column in Excel.",
    )
    placeholder = fields.Char()
    help_text = fields.Char()
    only_digits = fields.Boolean(string="Only Digits")
    min_length = fields.Integer(string="Min Length")
    max_length = fields.Integer(string="Max Length")
    case_mode = fields.Selection(
        [("none", "No Limit"), ("upper", "Uppercase"), ("lower", "Lowercase")],
        string="Case Limit",
        default="none",
        required=True,
    )
    validation_mode = fields.Selection(
        [
            ("none", "No Extra Validation"),
            ("alpha", "Letters Only"),
            ("alnum", "Letters + Numbers"),
            ("phone", "Mobile Phone"),
            ("email", "Email"),
            ("custom_regex", "Custom Regex"),
        ],
        string="Advanced Validation",
        default="none",
        required=True,
    )
    custom_regex = fields.Char(
        string="Custom Regex",
        help=r"Python regex used with full match, e.g. ^[A-Z]{2}\d{6}$",
    )
    options_text = fields.Text(help="For radio/select/checkbox: one option per line")
    default_options_text = fields.Char(
        string="Default Selection(s)",
        help="For radio/select: one default option. For checkbox: multiple values split by comma or line.",
    )
    use_conditional_options = fields.Boolean(
        string="Enable Cascading Options",
        default=False,
        help="Show next-level options based on the selected option.",
    )
    option_ids = fields.One2many("x_mobile.form.component.option", "component_id", string="Option Lines")
    display_text = fields.Text(help="Display content for text display block")
    image = fields.Binary(attachment=True)
    image_filename = fields.Char()
    image_url = fields.Char(help="Optional external image URL")
    file_accept = fields.Char(
        string="Allowed File Types",
        help="Examples: .pdf,.jpg,image/*,application/pdf",
    )
    file_max_mb = fields.Float(
        string="Max File Size (MB)",
        default=10.0,
    )
    date_format = fields.Selection(
        [
            ("mmddyyyy", "MMDDYYYY"),
            ("ddmmyyyy", "DDMMYYYY"),
            ("yyyymmdd", "YYYYMMDD"),
        ],
        string="Date Format",
        default="yyyymmdd",
        required=True,
        help="How the date value is stored/exported (input stays the standard HTML date control).",
    )
    linked_date_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="Linked Date Component",
        domain="[('form_id', '=', form_id), ('component_type', '=', 'date')]",
        help="Age will be automatically calculated from this date component.",
    )
    age_min = fields.Integer(
        string="Minimum Age",
        help="If set, ages below this value will trigger the configured action.",
    )
    age_min_action = fields.Selection(
        [("none", "No Check"), ("warn", "Warn Only"), ("block", "Block Submission")],
        string="Under Minimum Action",
        default="none",
        required=True,
    )
    age_min_message = fields.Char(
        string="Under Minimum Message",
        default="Age is below the minimum requirement.",
    )
    age_max = fields.Integer(
        string="Maximum Age",
        help="If set, ages above this value will trigger the configured action.",
    )
    age_max_action = fields.Selection(
        [("none", "No Check"), ("warn", "Warn Only"), ("block", "Block Submission")],
        string="Over Maximum Action",
        default="none",
        required=True,
    )
    age_max_message = fields.Char(
        string="Over Maximum Message",
        default="Age is above the maximum requirement.",
    )
    number_format_pattern = fields.Char(
        string="Number Format Pattern",
        help="Use 0 as digit placeholder, e.g. 000-00-000-0",
    )
    number_wheel_min = fields.Integer(
        string="Wheel Min",
        default=0,
    )
    number_wheel_max = fields.Integer(
        string="Wheel Max",
        default=100,
    )
    number_wheel_step = fields.Integer(
        string="Wheel Step",
        default=1,
    )
    number_wheel_default = fields.Integer(
        string="Wheel Default",
        default=0,
        help="Default selected value for Number Wheel.",
    )
    visibility_enabled = fields.Boolean(
        string="Enable Visibility Rule",
        default=False,
        help="Show or hide this component based on another component selection.",
    )
    visibility_source_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="Visibility Source",
        domain="[('form_id', '=', form_id), ('component_type', 'in', ['radio', 'checkbox', 'select'])]",
        ondelete="set null",
    )
    visibility_mode = fields.Selection(
        [("show_if_match", "Show If Match"), ("hide_if_match", "Hide If Match")],
        string="Visibility Mode",
        default="show_if_match",
        required=True,
    )
    visibility_match_values = fields.Char(
        string="Match Values",
        help="Comma or line separated values. Rule matches when source contains any of these values.",
    )
    visibility_match_option_ids = fields.Many2many(
        "x_mobile.form.component.option",
        "x_mobile_component_visibility_option_rel",
        "component_id",
        "option_id",
        string="Match Options",
        domain="[('component_id', '=', visibility_source_component_id)]",
        help="Select source options that trigger this visibility rule.",
    )

    _key_form_uniq = models.Constraint(
        "unique(form_id, key)",
        "Component key must be unique per form.",
    )

    @api.onchange("name")
    def _onchange_name_set_key(self):
        for record in self:
            if record.name and not record.key:
                normalized = re.sub(r"[^a-z0-9]+", "_", record.name.lower()).strip("_")
                record.key = normalized or f"field_{record.sequence or 1}"

    @api.constrains("key")
    def _check_key(self):
        pattern = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")
        for record in self:
            if not pattern.match(record.key or ""):
                raise ValidationError(_("Component key must start with a letter and contain only letters, numbers, or _."))

    @api.constrains("min_length", "max_length")
    def _check_length_range(self):
        for record in self:
            if record.min_length and record.min_length < 0:
                raise ValidationError(_("Min Length cannot be negative."))
            if record.max_length and record.max_length < 0:
                raise ValidationError(_("Max Length cannot be negative."))
            if record.min_length and record.max_length and record.min_length > record.max_length:
                raise ValidationError(_("Min Length cannot be greater than Max Length."))

    @api.constrains("file_max_mb")
    def _check_file_max_mb(self):
        for record in self:
            if record.file_max_mb is not None and record.file_max_mb < 0:
                raise ValidationError(_("Max File Size (MB) cannot be negative."))

    @api.constrains("number_format_pattern")
    def _check_number_format_pattern(self):
        for record in self:
            if record.component_type != "formatted_number":
                continue
            pattern = (record.number_format_pattern or "").strip()
            if not pattern:
                continue
            if "0" not in pattern:
                raise ValidationError(_("Number format pattern must include at least one 0 placeholder."))
            if not re.fullmatch(r"[0\-\s\./()]+", pattern):
                raise ValidationError(
                    _("Number format pattern only supports 0 and separators: - space . / ( )")
                )

    @api.constrains("component_type", "number_wheel_min", "number_wheel_max", "number_wheel_step", "number_wheel_default")
    def _check_number_wheel_range(self):
        for record in self:
            if record.component_type != "number_wheel":
                continue
            if record.number_wheel_step <= 0:
                raise ValidationError(_("Wheel Step must be greater than 0."))
            if record.number_wheel_min > record.number_wheel_max:
                raise ValidationError(_("Wheel Min cannot be greater than Wheel Max."))
            count = ((record.number_wheel_max - record.number_wheel_min) // record.number_wheel_step) + 1
            if count > 3000:
                raise ValidationError(_("Number Wheel has too many values. Please reduce range or increase step."))
            if record.number_wheel_default < record.number_wheel_min or record.number_wheel_default > record.number_wheel_max:
                raise ValidationError(_("Wheel Default must be between Wheel Min and Wheel Max."))
            if (record.number_wheel_default - record.number_wheel_min) % (record.number_wheel_step or 1) != 0:
                raise ValidationError(_("Wheel Default must follow Wheel Step."))

    @api.constrains(
        "visibility_enabled",
        "visibility_source_component_id",
        "visibility_match_values",
        "visibility_match_option_ids",
        "form_id",
    )
    def _check_visibility_rule(self):
        for record in self:
            if not record.visibility_enabled:
                continue
            if not record.visibility_source_component_id:
                raise ValidationError(_("Visibility Source is required when visibility rule is enabled."))
            if record.visibility_source_component_id.form_id != record.form_id:
                raise ValidationError(_("Visibility Source must be in the same form."))
            if record.visibility_source_component_id == record:
                raise ValidationError(_("Component cannot use itself as Visibility Source."))
            if not record.visibility_match_option_ids and not (record.visibility_match_values or "").strip():
                raise ValidationError(_("Match Options is required when visibility rule is enabled."))
            invalid_opts = record.visibility_match_option_ids.filtered(
                lambda o: o.component_id != record.visibility_source_component_id
            )
            if invalid_opts:
                raise ValidationError(_("All Match Options must belong to the selected Visibility Source."))

    @api.onchange("visibility_source_component_id")
    def _onchange_visibility_source_component_id(self):
        for record in self:
            if not record.visibility_source_component_id:
                record.visibility_match_option_ids = [(5, 0, 0)]
                continue
            valid = record.visibility_match_option_ids.filtered(
                lambda o: o.component_id == record.visibility_source_component_id
            )
            record.visibility_match_option_ids = valid

    @api.constrains("age_min", "age_max")
    def _check_age_limits(self):
        for record in self:
            if record.component_type != "age_auto":
                continue
            if record.age_min is not None and record.age_min < 0:
                raise ValidationError(_("Minimum Age cannot be negative."))
            if record.age_max is not None and record.age_max < 0:
                raise ValidationError(_("Maximum Age cannot be negative."))
            if (
                record.age_min is not None
                and record.age_max is not None
                and record.age_min > record.age_max
            ):
                raise ValidationError(_("Minimum Age cannot be greater than Maximum Age."))

    @api.onchange("component_type")
    def _onchange_component_type_defaults(self):
        for record in self:
            if record.component_type in ("radio", "select", "checkbox"):
                if not record.option_ids and not (record.options_text or "").strip():
                    record.option_ids = [
                        (0, 0, {"sequence": 10, "name": _("Option 1")}),
                        (0, 0, {"sequence": 20, "name": _("Option 2")}),
                    ]
                if not record.option_ids and (record.options_text or "").strip():
                    lines = []
                    for idx, opt in enumerate(record._parse_options_text(), start=1):
                        lines.append((0, 0, {"sequence": idx * 10, "name": opt}))
                    if lines:
                        record.option_ids = lines
            if record.component_type in ("display", "image", "section"):
                record.include_in_export = False
                record.required = False
            if record.component_type == "formatted_number" and not (record.number_format_pattern or "").strip():
                record.number_format_pattern = "000-00-000-0"
            if record.component_type == "number_wheel":
                if record.number_wheel_step <= 0:
                    record.number_wheel_step = 1
                if record.number_wheel_min > record.number_wheel_max:
                    record.number_wheel_min = 0
                    record.number_wheel_max = 100
                if record.number_wheel_default < record.number_wheel_min or record.number_wheel_default > record.number_wheel_max:
                    record.number_wheel_default = record.number_wheel_min
            if record.component_type == "age_auto":
                record.required = False
                if record.age_min is None:
                    record.age_min = 0

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.with_context(mobile_form_skip_log=True)._ensure_option_lines_from_text()
        return records

    def write(self, vals):
        result = super().write(vals)
        fields_touched = set(vals.keys())
        if fields_touched.intersection({"options_text", "component_type"}):
            self.with_context(mobile_form_skip_log=True)._ensure_option_lines_from_text()
        return result

    def unlink(self):
        return super().unlink()

    @api.onchange("validation_mode")
    def _onchange_validation_mode_defaults(self):
        for record in self:
            if record.validation_mode == "custom_regex" and not (record.custom_regex or "").strip():
                record.custom_regex = r"^[A-Z]{2}\d{6}$"
            elif record.validation_mode != "custom_regex":
                record.custom_regex = False

    @api.constrains("validation_mode", "custom_regex")
    def _check_custom_regex(self):
        for record in self:
            if record.validation_mode == "custom_regex":
                regex_text = (record.custom_regex or "").strip()
                if not regex_text:
                    continue
                try:
                    re.compile(regex_text)
                except re.error as exc:
                    raise ValidationError(
                        _("Invalid custom regex on '%s': %s") % (record.name, str(exc))
                    ) from exc

    @api.constrains("component_type", "option_ids")
    def _check_default_for_single_choice(self):
        for record in self:
            if record.component_type in ("radio", "select"):
                if len(record.option_ids.filtered("is_default")) > 1:
                    raise ValidationError(_("Only one default option is allowed for radio/select."))

    def _parse_options_text(self):
        self.ensure_one()
        if not self.options_text:
            return []
        raw = self.options_text.replace("\r\n", "\n")
        chunks = []
        for line in raw.split("\n"):
            parts = re.split(r"[,，;；|]+", line)
            chunks.extend(parts)
        seen = set()
        options = []
        for item in chunks:
            value = item.strip()
            if value and value not in seen:
                seen.add(value)
                options.append(value)
        return options

    def _ensure_option_lines_from_text(self):
        for record in self:
            if record.component_type not in ("radio", "select", "checkbox"):
                continue
            if record.option_ids:
                continue
            options = record._parse_options_text()
            if not options:
                continue
            default_set = set()
            if record.default_options_text:
                raw = record.default_options_text.replace("\r\n", "\n")
                chunks = []
                for line in raw.split("\n"):
                    chunks.extend(re.split(r"[,，;；|]+", line))
                default_set = {item.strip() for item in chunks if item.strip()}
            lines = []
            for idx, name in enumerate(options, start=1):
                lines.append(
                    (0, 0, {"sequence": idx * 10, "name": name, "is_default": name in default_set})
                )
            if lines:
                record.write({"option_ids": lines})

    def get_option_list(self):
        self.ensure_one()
        if self.option_ids:
            return [opt.name for opt in self.option_ids.sorted(lambda o: (o.sequence, o.id)) if opt.name]
        return self._parse_options_text()

    def get_root_option_list(self):
        self.ensure_one()
        if not self.option_ids:
            return self.get_option_list()
        roots = self.option_ids.filtered(lambda o: not o.parent_option_id).sorted(lambda o: (o.sequence, o.id))
        return [opt.name for opt in roots if opt.name]

    def get_conditional_options_json(self):
        self.ensure_one()
        items = []
        for opt in self.option_ids.sorted(lambda o: (o.sequence, o.id)):
            if not opt.name:
                continue
            items.append(
                {
                    "id": opt.id,
                    "name": opt.name,
                    "parent_id": opt.parent_option_id.id if opt.parent_option_id else 0,
                }
            )
        return json.dumps(items, ensure_ascii=False)

    def get_default_option_list(self):
        self.ensure_one()
        if self.option_ids:
            defaults = [opt.name for opt in self.option_ids.filtered("is_default").sorted(lambda o: (o.sequence, o.id))]
            option_set = set(self.get_option_list())
            return [item for item in defaults if item in option_set]
        if not self.default_options_text:
            return []
        raw = self.default_options_text.replace("\r\n", "\n")
        chunks = []
        for line in raw.split("\n"):
            parts = re.split(r"[,，;；|]+", line)
            chunks.extend(parts)
        wanted = [item.strip() for item in chunks if item.strip()]
        if not wanted:
            return []
        option_set = set(self.get_option_list())
        return [item for item in wanted if item in option_set]

    def get_default_single_option(self):
        self.ensure_one()
        defaults = self.get_default_option_list()
        return defaults[0] if defaults else ""

    def action_open_settings(self):
        self.ensure_one()
        view = self.env.ref("mobile_form_builder.view_x_mobile_form_component_settings_form")
        return {
            "type": "ir.actions.act_window",
            "name": _("Component Settings"),
            "res_model": "x_mobile.form.component",
            "view_mode": "form",
            "view_id": view.id,
            "res_id": self.id,
            "target": "new",
            "context": {"default_form_id": self.form_id.id},
        }

    def apply_input_rules(self, value):
        self.ensure_one()
        if self.component_type == "number_wheel":
            normalized = (value or "").strip()
            if not normalized:
                return ""
            if not re.fullmatch(r"-?\d+", normalized):
                raise ValidationError(_("Field '%s' must be an integer.") % (self.name,))
            num = int(normalized)
            if num < self.number_wheel_min or num > self.number_wheel_max:
                raise ValidationError(
                    _("Field '%s' must be between %s and %s.")
                    % (self.name, self.number_wheel_min, self.number_wheel_max)
                )
            if (num - self.number_wheel_min) % (self.number_wheel_step or 1) != 0:
                raise ValidationError(
                    _("Field '%s' must follow step %s.") % (self.name, self.number_wheel_step)
                )
            return str(num)

        if self.component_type not in ("input", "textarea", "email", "formatted_number", "multiline_text"):
            return value

        normalized = (value or "").strip()

        if self.component_type == "email":
            if normalized and not re.fullmatch(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", normalized):
                raise ValidationError(_("Field '%s' must be a valid email address.") % (self.name,))
            if self.min_length and normalized and len(normalized) < self.min_length:
                raise ValidationError(
                    _("Field '%s' must be at least %s characters.") % (self.name, self.min_length)
                )
            if self.max_length and normalized and len(normalized) > self.max_length:
                raise ValidationError(
                    _("Field '%s' cannot exceed %s characters.") % (self.name, self.max_length)
                )
            return normalized

        if self.component_type == "formatted_number":
            pattern = (self.number_format_pattern or "").strip()
            if pattern:
                digits = re.sub(r"\D+", "", normalized)
                needed = pattern.count("0")
                if normalized and not digits:
                    raise ValidationError(
                        _("Field '%s' must match format '%s'.") % (self.name, pattern)
                    )
                if digits and len(digits) != needed:
                    raise ValidationError(
                        _("Field '%s' must match format '%s'.") % (self.name, pattern)
                    )
                if digits:
                    out = []
                    idx = 0
                    for ch in pattern:
                        if ch == "0":
                            out.append(digits[idx])
                            idx += 1
                        else:
                            out.append(ch)
                    normalized = "".join(out)
            return normalized

        if self.case_mode == "upper":
            normalized = normalized.upper()
        elif self.case_mode == "lower":
            normalized = normalized.lower()

        if self.only_digits and normalized and not re.fullmatch(r"\d+", normalized):
            raise ValidationError(_("Field '%s' only allows digits.") % (self.name,))

        if normalized:
            if self.validation_mode == "alpha" and not re.fullmatch(r"[A-Za-z]+", normalized):
                raise ValidationError(_("Field '%s' only allows letters.") % (self.name,))
            if self.validation_mode == "alnum" and not re.fullmatch(r"[A-Za-z0-9]+", normalized):
                raise ValidationError(_("Field '%s' only allows letters and numbers.") % (self.name,))
            if self.validation_mode == "phone" and not re.fullmatch(r"^1[3-9]\d{9}$", normalized):
                raise ValidationError(_("Field '%s' must be a valid mobile phone number.") % (self.name,))
            if self.validation_mode == "email" and not re.fullmatch(
                r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", normalized
            ):
                raise ValidationError(_("Field '%s' must be a valid email address.") % (self.name,))
            if self.validation_mode == "custom_regex":
                regex_text = (self.custom_regex or "").strip()
                if regex_text and not re.fullmatch(regex_text, normalized):
                    raise ValidationError(_("Field '%s' does not match custom regex.") % (self.name,))

        if self.min_length and normalized and len(normalized) < self.min_length:
            raise ValidationError(
                _("Field '%s' must be at least %s characters.") % (self.name, self.min_length)
            )

        if self.max_length and normalized and len(normalized) > self.max_length:
            raise ValidationError(
                _("Field '%s' cannot exceed %s characters.") % (self.name, self.max_length)
            )

        return normalized

    def get_front_pattern(self):
        self.ensure_one()
        mapping = {
            "alpha": r"[A-Za-z]+",
            "alnum": r"[A-Za-z0-9]+",
            "phone": r"1[3-9]\d{9}",
            "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        }
        if self.validation_mode == "custom_regex":
            return (self.custom_regex or "").strip()
        return mapping.get(self.validation_mode, "")

    def get_front_pattern_hint(self):
        self.ensure_one()
        hints = {
            "alpha": "Only letters, e.g. John",
            "alnum": "Letters and numbers only, e.g. A12B3",
            "phone": "China mobile phone, e.g. 13800138000",
            "email": "Email format, e.g. user@example.com",
            "custom_regex": "Custom regex, e.g. ^[A-Z]{2}\\d{6}$",
        }
        return hints.get(self.validation_mode, "")

    def compute_age_from_date_string(self, date_value):
        self.ensure_one()
        if not date_value:
            return "0"
        try:
            birth_date = fields.Date.from_string(date_value)
        except Exception:
            birth_date = None
        if not birth_date:
            return "0"
        today = fields.Date.context_today(self)
        years = today.year - birth_date.year
        if (today.month, today.day) < (birth_date.month, birth_date.day):
            years -= 1
        if years < 0:
            years = 0
        return str(years)

    def evaluate_age_policy(self, age_value):
        self.ensure_one()
        if self.component_type != "age_auto":
            return {"block": False, "message": "", "warn": ""}
        try:
            age_num = int((age_value or "0").strip() or "0")
        except Exception:
            age_num = 0

        if self.age_min is not None and self.age_min_action != "none" and age_num < self.age_min:
            msg = (self.age_min_message or "").strip() or _("Age is below the minimum requirement.")
            if self.age_min_action == "block":
                return {"block": True, "message": msg, "warn": ""}
            return {"block": False, "message": "", "warn": msg}

        if self.age_max is not None and self.age_max_action != "none" and age_num > self.age_max:
            msg = (self.age_max_message or "").strip() or _("Age is above the maximum requirement.")
            if self.age_max_action == "block":
                return {"block": True, "message": msg, "warn": ""}
            return {"block": False, "message": "", "warn": msg}

        return {"block": False, "message": "", "warn": ""}

    def format_date_value(self, date_value):
        """Format ISO date (YYYY-MM-DD) to configured string format."""
        self.ensure_one()
        val = (date_value or "").strip()
        if not val:
            return ""
        try:
            d = fields.Date.from_string(val)
        except Exception:
            d = None
        if not d:
            return val
        if self.date_format == "mmddyyyy":
            return f"{d.month:02d}{d.day:02d}{d.year:04d}"
        if self.date_format == "ddmmyyyy":
            return f"{d.day:02d}{d.month:02d}{d.year:04d}"
        return f"{d.year:04d}{d.month:02d}{d.day:02d}"

    def get_number_wheel_values(self):
        self.ensure_one()
        start = int(self.number_wheel_min or 0)
        stop = int(self.number_wheel_max or 0)
        step = int(self.number_wheel_step or 1)
        if step <= 0:
            step = 1
        if start > stop:
            start, stop = 0, 100
        values = []
        cur = start
        safe_guard = 0
        while cur <= stop and safe_guard < 5000:
            values.append(cur)
            cur += step
            safe_guard += 1
        return values

    def validate_uploaded_file(self, upload):
        self.ensure_one()
        if self.component_type != "file_upload":
            return
        if not upload or not upload.filename:
            return

        stream = upload.stream
        current_pos = stream.tell()
        stream.seek(0, 2)
        file_size = stream.tell()
        stream.seek(0)

        if self.file_max_mb and file_size > int(self.file_max_mb * 1024 * 1024):
            raise ValidationError(
                _("Field '%s' exceeds max file size %.2f MB.") % (self.name, self.file_max_mb)
            )

        accept = (self.file_accept or "").strip()
        if accept:
            tokens = [x.strip().lower() for x in accept.split(",") if x.strip()]
            filename = (upload.filename or "").lower()
            mimetype = (upload.mimetype or "").lower()

            allowed = False
            for token in tokens:
                if token.startswith("."):
                    if filename.endswith(token):
                        allowed = True
                        break
                elif token.endswith("/*"):
                    prefix = token[:-1]
                    if mimetype.startswith(prefix):
                        allowed = True
                        break
                else:
                    if mimetype == token:
                        allowed = True
                        break
            if not allowed:
                raise ValidationError(
                    _("Field '%s' file type is not allowed. Allowed: %s") % (self.name, accept)
                )

        stream.seek(current_pos)

    def _parse_visibility_values(self):
        self.ensure_one()
        if self.visibility_match_option_ids:
            return [x.name for x in self.visibility_match_option_ids if x.name]
        raw = (self.visibility_match_values or "").replace("\r\n", "\n")
        chunks = []
        for line in raw.split("\n"):
            chunks.extend(re.split(r"[,，;；|]+", line))
        return [item.strip() for item in chunks if item.strip()]

    def get_visibility_match_values_text(self):
        self.ensure_one()
        return ", ".join(self._parse_visibility_values())

    def is_visible_in_public_form(self, form_data):
        self.ensure_one()
        if not self.visibility_enabled:
            return True
        source = self.visibility_source_component_id
        if not source or not source.key:
            return True
        wanted = set(self._parse_visibility_values())
        if not wanted:
            return True
        if source.component_type == "checkbox":
            selected = [(x or "").strip() for x in form_data.getlist(source.key)]
        else:
            selected = [((form_data.get(source.key) or "").strip())]
        matched = any(v in wanted for v in selected if v)
        if self.visibility_mode == "hide_if_match":
            return not matched
        return matched


class MobileFormSubmission(models.Model):
    _name = "x_mobile.form.submission"
    _description = "Mobile Form Submission"
    _order = "submit_date desc, id desc"

    form_id = fields.Many2one("x_mobile.form", required=True, ondelete="cascade")
    name = fields.Char(default=lambda self: _("New"), readonly=True)
    submit_date = fields.Datetime(default=fields.Datetime.now, required=True)
    client_identifier = fields.Char(index=True, readonly=True)
    answer_json = fields.Text()
    searchable_content = fields.Char(readonly=True)
    answer_preview = fields.Char(compute="_compute_answer_preview")
    form_preview_html = fields.Html(compute="_compute_form_preview_html", sanitize=False)
    line_ids = fields.One2many("x_mobile.form.submission.line", "submission_id")
    confirm_key1_value = fields.Char(index=True, copy=False)
    confirm_key2_value = fields.Char(index=True, copy=False)
    is_confirmed = fields.Boolean(default=False, index=True, copy=False)
    confirmed_at = fields.Datetime(readonly=True, copy=False)
    confirmed_by = fields.Many2one("res.users", readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        seq = self.env["ir.sequence"].sudo()
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = seq.next_by_code("x_mobile.form.submission") or _("New")
        records = super().create(vals_list)
        records._sync_answer_json_from_lines()
        return records

    def write(self, vals):
        result = super().write(vals)
        if "line_ids" in vals:
            self._sync_answer_json_from_lines()
        return result

    def _compute_answer_preview(self):
        for record in self:
            pairs = []
            for line in record.line_ids[:4]:
                raw_value = line.value_text or ""
                if raw_value.startswith("data:image"):
                    short_value = "[signature/image]"
                else:
                    compact = " ".join(raw_value.split())
                    short_value = compact[:60] + ("..." if len(compact) > 60 else "")
                pairs.append(f"{line.label}: {short_value}")
            preview = " | ".join(pairs)
            record.answer_preview = preview[:260] + ("..." if len(preview) > 260 else "")

    @api.depends(
        "line_ids.label",
        "line_ids.key",
        "line_ids.value_text",
        "line_ids.sequence_snapshot",
        "line_ids.component_type_snapshot",
    )
    def _compute_form_preview_html(self):
        for record in self:
            lines = record.line_ids.sorted(
                key=lambda l: ((l.sequence_snapshot if l.sequence_snapshot else 999999), l.id)
            )
            if not lines:
                record.form_preview_html = "<div class='mform-back-preview-empty'>No data</div>"
                continue

            items = []
            for line in lines:
                label = escape(line.label or line.key or "")
                raw_value = line.value_text or ""
                is_signature = (
                    line.component_type_snapshot == "signature" or raw_value.startswith("data:image")
                )
                if line.component_type_snapshot == "file_upload" and line.attachment_id:
                    fname = escape(line.value_text or line.attachment_id.name or "file")
                    url = f"/web/content/{line.attachment_id.id}?download=1"
                    value_html = (
                        f"<div class='mform-back-value'><a href=\"{url}\" target=\"_blank\">{fname}</a></div>"
                    )
                elif is_signature and raw_value.startswith("data:image"):
                    src = raw_value.replace('"', "")
                    value_html = (
                        f"<div class='mform-back-value'><img src=\"{src}\" "
                        f"class='mform-back-sign' alt='signature'/></div>"
                    )
                else:
                    text_html = escape(raw_value).replace("\n", "<br/>")
                    value_html = f"<div class='mform-back-value'>{text_html}</div>"

                items.append(
                    "<div class='mform-back-item'>"
                    f"<div class='mform-back-label'>{label}</div>"
                    f"{value_html}"
                    "</div>"
                )

            record.form_preview_html = (
                "<div class='mform-back-preview'>"
                "<style>"
                ".mform-back-preview{border:1px solid #d9e0ea;border-radius:10px;padding:12px;background:#fff;}"
                ".mform-back-item{padding:10px 4px;border-bottom:1px dashed #d8dee9;}"
                ".mform-back-item:last-child{border-bottom:none;}"
                ".mform-back-label{font-weight:700;color:#334155;margin-bottom:4px;}"
                ".mform-back-value{color:#1f2937;word-break:break-word;white-space:normal;}"
                ".mform-back-sign{max-height:120px;max-width:280px;border:1px solid #cfd8e6;border-radius:6px;}"
                ".mform-back-preview-empty{color:#6b7280;}"
                "</style>"
                + "".join(items)
                + "</div>"
            )

    def _sync_answer_json_from_lines(self):
        for record in self:
            if not record.line_ids:
                try:
                    payload = json.loads(record.answer_json or "{}")
                    if not isinstance(payload, dict):
                        payload = {}
                except Exception:
                    payload = {}
                search_items = []
                for key, value in payload.items():
                    text_value = str(value) if value is not None else ""
                    search_items.append(f"{key} {text_value}")
                record.searchable_content = " | ".join(search_items)
                continue
            payload = {line.key: (line.value_text or "") for line in record.line_ids}
            record.answer_json = json.dumps(payload, ensure_ascii=False)
            record.searchable_content = " | ".join(
                [f"{line.label or line.key} {line.value_text or ''}" for line in record.line_ids]
            )

    def _get_selected_records(self):
        active_ids = self.env.context.get("active_ids") or self.ids
        records = self.browse(active_ids).exists()
        if not records:
            raise UserError(_("Please select at least one submission."))
        return records

    def action_export_selected_xlsx(self):
        records = self._get_selected_records()
        ids_str = ",".join(str(x) for x in records.ids)
        return {
            "type": "ir.actions.act_url",
            "url": f"/mform/export_selected_xlsx?ids={ids_str}",
            "target": "self",
        }

    def action_export_selected_pdf(self):
        records = self._get_selected_records().sorted("submit_date")
        return self.env.ref("mobile_form_builder.action_report_mobile_form_submission").report_action(records)

    def action_confirm(self):
        now = fields.Datetime.now()
        for rec in self:
            rec.write({"is_confirmed": True, "confirmed_at": now, "confirmed_by": self.env.user.id})
        return True

    def action_unconfirm(self):
        for rec in self:
            rec.write({"is_confirmed": False, "confirmed_at": False, "confirmed_by": False})
        return True

    def get_submit_date_company_tz_str(self):
        """Used by PDF template to render submit_date in the company timezone."""
        self.ensure_one()
        tz = _mform_company_tz(self.env)
        try:
            local_dt = fields.Datetime.context_timestamp(self.env.user.with_context(tz=tz), self.submit_date)
        except Exception:
            local_dt = self.submit_date
        try:
            return local_dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return fields.Datetime.to_string(self.submit_date)


class MobileFormSubmissionLine(models.Model):
    _name = "x_mobile.form.submission.line"
    _description = "Mobile Form Submission Line"
    _order = "id"

    submission_id = fields.Many2one("x_mobile.form.submission", required=True, ondelete="cascade")
    component_id = fields.Many2one("x_mobile.form.component", ondelete="set null")
    sequence_snapshot = fields.Integer()
    component_type_snapshot = fields.Char()
    attachment_id = fields.Many2one("ir.attachment", ondelete="set null")
    key = fields.Char(required=True)
    label = fields.Char(required=True)
    value_text = fields.Text()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records.filtered("attachment_id"):
            rec.attachment_id.sudo().write({"res_model": rec._name, "res_id": rec.id})
        records.mapped("submission_id")._sync_answer_json_from_lines()
        return records

    def write(self, vals):
        result = super().write(vals)
        self.mapped("submission_id")._sync_answer_json_from_lines()
        return result

    def unlink(self):
        submissions = self.mapped("submission_id")
        result = super().unlink()
        submissions._sync_answer_json_from_lines()
        return result


class MobileFormComponentOption(models.Model):
    _name = "x_mobile.form.component.option"
    _description = "Mobile Form Component Option"
    _order = "sequence, id"

    component_id = fields.Many2one("x_mobile.form.component", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    parent_option_id = fields.Many2one(
        "x_mobile.form.component.option",
        string="Parent Option",
        domain="[('component_id', '=', component_id)]",
        ondelete="cascade",
    )
    child_option_ids = fields.One2many("x_mobile.form.component.option", "parent_option_id", string="Child Options")
    is_default = fields.Boolean(string="Default")

    _option_name_component_uniq = models.Constraint(
        "unique(component_id, name)",
        "Option names must be unique per component.",
    )

    @api.constrains("parent_option_id", "component_id")
    def _check_parent_option(self):
        for rec in self:
            if not rec.parent_option_id:
                continue
            if rec.parent_option_id == rec:
                raise ValidationError(_("Option cannot reference itself as parent."))
            if rec.parent_option_id.component_id != rec.component_id:
                raise ValidationError(_("Parent option must belong to the same component."))

    @api.constrains("is_default", "component_id")
    def _check_single_default(self):
        for rec in self:
            comp = rec.component_id
            if comp.component_type in ("radio", "select"):
                if len(comp.option_ids.filtered("is_default")) > 1:
                    raise ValidationError(_("Only one default option is allowed for radio/select."))


def build_xlsx_content(form):
    try:
        import xlsxwriter
    except ImportError as exc:
        raise UserError(_("xlsxwriter is required for Excel export.")) from exc

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    sheet = workbook.add_worksheet("Submissions")

    header_fmt = workbook.add_format({"bold": True, "bg_color": "#E8EEF6"})
    wrap_fmt = workbook.add_format({"text_wrap": True})

    components = form.component_ids.filtered("include_in_export").sorted("sequence")
    if not components:
        components = form.component_ids.filtered(
            lambda c: c.component_type not in ("display", "image", "section")
        ).sorted("sequence")

    headers = ["Submission", "Submitted At", "Confirmed"] + [component.name for component in components]
    for col, title in enumerate(headers):
        sheet.write(0, col, title, header_fmt)

    row = 1
    for submission in form.submission_ids:
        sheet.write(row, 0, submission.name)
        sheet.write(row, 1, _mform_format_dt_company_tz(form.env, submission.submit_date))
        sheet.write(row, 2, "Yes" if submission.is_confirmed else "No")

        value_map = {}
        if submission.line_ids:
            value_map = {line.key: (line.value_text or "") for line in submission.line_ids}
        else:
            payload = json.loads(submission.answer_json or "{}")
            value_map = {key: (str(value) if value is not None else "") for key, value in payload.items()}

        for idx, component in enumerate(components, start=3):
            sheet.write(row, idx, value_map.get(component.key, ""), wrap_fmt)
        row += 1

    sheet.set_column(0, 0, 18)
    sheet.set_column(1, 1, 22)
    sheet.set_column(2, 2, 10)
    if headers:
        sheet.set_column(3, max(3, len(headers) - 1), 28)

    workbook.close()
    output.seek(0)
    return output.read()


def build_xlsx_for_submissions(submissions):
    try:
        import xlsxwriter
    except ImportError as exc:
        raise UserError(_("xlsxwriter is required for Excel export.")) from exc

    submissions = submissions.sorted("submit_date")
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    sheet = workbook.add_worksheet("Submissions")
    header_fmt = workbook.add_format({"bold": True, "bg_color": "#E8EEF6"})
    wrap_fmt = workbook.add_format({"text_wrap": True})

    label_order = []
    label_seen = set()
    for sub in submissions:
        for line in sub.line_ids.sorted("id"):
            label = line.label or line.key
            if label not in label_seen:
                label_seen.add(label)
                label_order.append(label)

    headers = ["Submission", "Form", "Submitted At", "Confirmed"] + label_order
    for col, title in enumerate(headers):
        sheet.write(0, col, title, header_fmt)

    row = 1
    for sub in submissions:
        sheet.write(row, 0, sub.name or "")
        sheet.write(row, 1, sub.form_id.name or "")
        sheet.write(row, 2, _mform_format_dt_company_tz(sub.env, sub.submit_date))
        sheet.write(row, 3, "Yes" if sub.is_confirmed else "No")
        values = {}
        for line in sub.line_ids:
            values[line.label or line.key] = line.value_text or ""
        for idx, label in enumerate(label_order, start=4):
            value = values.get(label, "")
            if isinstance(value, str) and value.startswith("data:image"):
                value = "[signature/image]"
            sheet.write(row, idx, value, wrap_fmt)
        row += 1

    sheet.set_column(0, 0, 18)
    sheet.set_column(1, 1, 24)
    sheet.set_column(2, 2, 22)
    sheet.set_column(3, 3, 10)
    if headers:
        sheet.set_column(4, max(4, len(headers) - 1), 28)

    workbook.close()
    output.seek(0)
    return output.read()
