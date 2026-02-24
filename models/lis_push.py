import json
import ssl
import time
import urllib.error
import urllib.request

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


SAMPLE_TYPE_SELECTION = [
    ("blood", "Whole Blood"),
    ("serum", "Serum"),
    ("plasma", "Plasma"),
    ("urine", "Urine"),
    ("swab", "Swab"),
    ("saliva", "Saliva"),
    ("stool", "Stool"),
    ("sputum", "Sputum"),
    ("semen", "Semen"),
    ("tissue", "Tissue"),
    ("csf", "CSF"),
    ("other", "Other"),
]


class MobileFormLisEndpoint(models.Model):
    _name = "x_mobile.lis.endpoint"
    _description = "Mobile Form LIS Endpoint"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name, id"

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)
    base_url = fields.Char(
        required=True,
        help="LIS base URL, for example: https://lis.example.com",
    )
    endpoint_code = fields.Char(required=True, help="External API endpoint code configured in LIS.", tracking=True)
    auth_type = fields.Selection(
        [
            ("none", "None"),
            ("api_key", "API Key"),
            ("bearer", "Bearer Token"),
            ("basic", "Basic Auth"),
        ],
        default="api_key",
        required=True,
        tracking=True,
    )
    api_key = fields.Char()
    bearer_token = fields.Char()
    username = fields.Char()
    password = fields.Char()
    timeout_seconds = fields.Integer(default=20, required=True, tracking=True)
    verify_ssl = fields.Boolean(default=True, tracking=True)
    notes = fields.Text()
    mapping_ids = fields.One2many("x_mobile.lis.mapping", "endpoint_id", string="Mappings")
    metadata_item_ids = fields.One2many("x_mobile.lis.meta.item", "endpoint_id", string="Metadata Items")
    metadata_sync_time = fields.Datetime(readonly=True, copy=False)
    metadata_sync_message = fields.Char(readonly=True, copy=False)

    @api.constrains("timeout_seconds")
    def _check_timeout_seconds(self):
        for rec in self:
            if rec.timeout_seconds <= 0:
                raise ValidationError(_("Timeout must be greater than 0 seconds."))

    def _base_api_path(self):
        self.ensure_one()
        base = (self.base_url or "").strip().rstrip("/")
        code = (self.endpoint_code or "").strip()
        if not base or not code:
            raise UserError(_("LIS endpoint base URL and endpoint code are required."))
        return f"{base}/lab/api/v1/{code}"

    def _build_headers(self):
        self.ensure_one()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.auth_type == "api_key":
            if not self.api_key:
                raise UserError(_("LIS endpoint API key is not configured."))
            headers["X-API-Key"] = self.api_key
        elif self.auth_type == "bearer":
            if not self.bearer_token:
                raise UserError(_("LIS endpoint bearer token is not configured."))
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        elif self.auth_type == "basic":
            if not self.username:
                raise UserError(_("LIS endpoint username is not configured."))
            raw = f"{self.username}:{self.password or ''}".encode("utf-8")
            import base64

            headers["Authorization"] = f"Basic {base64.b64encode(raw).decode('utf-8')}"
        return headers

    def _call_jsonrpc(self, path, payload):
        self.ensure_one()
        url = f"{self._base_api_path()}/{path.lstrip('/')}"
        body = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": payload,
            "id": int(time.time() * 1000),
        }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url=url, data=data, headers=self._build_headers(), method="POST")
        ssl_ctx = None
        if not self.verify_ssl:
            ssl_ctx = ssl._create_unverified_context()
        timeout = int(self.timeout_seconds or 20)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
                response_body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as err:
            detail = (err.read() or b"").decode("utf-8", errors="replace")
            raise UserError(_("LIS API HTTP error %(code)s: %(detail)s") % {"code": err.code, "detail": detail})
        except urllib.error.URLError as err:
            raise UserError(_("Unable to connect LIS API: %s") % (err.reason or err,))

        try:
            parsed = json.loads(response_body or "{}")
        except Exception:
            raise UserError(_("LIS API returned invalid JSON: %s") % response_body[:500])

        # Odoo json routes usually return JSON-RPC wrapper with result.
        if isinstance(parsed, dict) and "result" in parsed:
            parsed = parsed.get("result") or {}
        if not isinstance(parsed, dict):
            raise UserError(_("LIS API returned unexpected payload."))
        return parsed

    def push_external_request(self, payload):
        self.ensure_one()
        return self._call_jsonrpc("requests", payload)

    def _call_http_json_get(self, path):
        self.ensure_one()
        url = f"{self._base_api_path()}/{path.lstrip('/')}"
        req = urllib.request.Request(url=url, headers=self._build_headers(), method="GET")
        ssl_ctx = None
        if not self.verify_ssl:
            ssl_ctx = ssl._create_unverified_context()
        timeout = int(self.timeout_seconds or 20)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
                response_body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as err:
            detail = (err.read() or b"").decode("utf-8", errors="replace")
            raise UserError(_("LIS metadata API HTTP error %(code)s: %(detail)s") % {"code": err.code, "detail": detail})
        except urllib.error.URLError as err:
            raise UserError(_("Unable to connect LIS metadata API: %s") % (err.reason or err,))
        try:
            parsed = json.loads(response_body or "{}")
        except Exception:
            raise UserError(_("LIS metadata API returned invalid JSON: %s") % response_body[:500])
        if not isinstance(parsed, dict):
            raise UserError(_("LIS metadata API returned unexpected payload."))
        if not parsed.get("ok"):
            raise UserError(parsed.get("error") or _("LIS metadata API returned failed response."))
        return parsed

    def action_sync_metadata(self):
        item_obj = self.env["x_mobile.lis.meta.item"].sudo()
        for rec in self:
            try:
                sample_types = rec._call_http_json_get("meta/sample_types").get("sample_types") or []
                services = rec._call_http_json_get("meta/services").get("services") or []
                profiles = rec._call_http_json_get("meta/profiles").get("profiles") or []

                existing = item_obj.search([("endpoint_id", "=", rec.id)])
                existing_map = {(x.item_type, x.code): x for x in existing}
                seen = set()

                def _upsert(item_type, rows):
                    for row in rows:
                        code = (row.get("code") or "").strip()
                        if not code:
                            continue
                        key = (item_type, code)
                        seen.add(key)
                        vals = {
                            "endpoint_id": rec.id,
                            "item_type": item_type,
                            "code": code,
                            "name": (row.get("name") or code).strip(),
                            "sample_type_code": (row.get("sample_type") or "").strip(),
                            "is_default": bool(row.get("is_default")),
                            "active": True,
                        }
                        found = existing_map.get(key)
                        if found:
                            found.write(vals)
                        else:
                            item_obj.create(vals)

                _upsert("sample_type", sample_types)
                _upsert("service", services)
                _upsert("profile", profiles)

                stale = existing.filtered(lambda x: (x.item_type, x.code) not in seen)
                stale.write({"active": False})

                rec.write(
                    {
                        "metadata_sync_time": fields.Datetime.now(),
                        "metadata_sync_message": _("OK: sample_types=%(a)s, services=%(b)s, profiles=%(c)s")
                        % {"a": len(sample_types), "b": len(services), "c": len(profiles)},
                    }
                )
            except Exception as err:
                rec.write({"metadata_sync_time": fields.Datetime.now(), "metadata_sync_message": str(err)[:512]})
                raise


class MobileFormLisMetaItem(models.Model):
    _name = "x_mobile.lis.meta.item"
    _description = "Mobile Form LIS Metadata Item"
    _order = "item_type, code, id"

    endpoint_id = fields.Many2one("x_mobile.lis.endpoint", required=True, ondelete="cascade", index=True)
    item_type = fields.Selection(
        [("sample_type", "Sample Type"), ("service", "Service"), ("profile", "Profile")],
        required=True,
        index=True,
    )
    code = fields.Char(required=True, index=True)
    name = fields.Char(required=True)
    sample_type_code = fields.Char(help="Optional sample type code linked to service/profile.")
    is_default = fields.Boolean(default=False)
    active = fields.Boolean(default=True)
    display_name_label = fields.Char(compute="_compute_display_name_label")

    _item_uniq = models.Constraint(
        "unique(endpoint_id, item_type, code)",
        "Metadata code must be unique per endpoint and type.",
    )

    @api.depends("code", "name")
    def _compute_display_name_label(self):
        for rec in self:
            rec.display_name_label = "[%s] %s" % (rec.code or "", rec.name or "")


class MobileFormLisMapping(models.Model):
    _name = "x_mobile.lis.mapping"
    _description = "Mobile Form LIS Mapping"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name, id"

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)
    form_id = fields.Many2one("x_mobile.form", required=True, ondelete="cascade", tracking=True)
    endpoint_id = fields.Many2one("x_mobile.lis.endpoint", required=True, ondelete="restrict", tracking=True)

    external_uid_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="External UID Field",
        domain="[('form_id', '=', form_id)]",
        ondelete="set null",
    )
    patient_name_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="Patient Name Field",
        domain="[('form_id', '=', form_id)]",
        ondelete="set null",
    )
    patient_identifier_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="Patient Identifier Field",
        domain="[('form_id', '=', form_id)]",
        ondelete="set null",
    )
    patient_gender_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="Patient Gender Field",
        domain="[('form_id', '=', form_id)]",
        ondelete="set null",
    )
    patient_birthdate_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="Patient Birthdate Field",
        domain="[('form_id', '=', form_id)]",
        ondelete="set null",
    )
    patient_phone_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="Patient Phone Field",
        domain="[('form_id', '=', form_id)]",
        ondelete="set null",
    )
    physician_name_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="Physician Name Field",
        domain="[('form_id', '=', form_id)]",
        ondelete="set null",
    )
    physician_ref_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="Physician Ref Field",
        domain="[('form_id', '=', form_id)]",
        ondelete="set null",
    )
    clinical_note_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="Clinical Note Field",
        domain="[('form_id', '=', form_id)]",
        ondelete="set null",
    )
    clinical_note_component_ids = fields.Many2many(
        "x_mobile.form.component",
        "x_mobile_lis_mapping_note_component_rel",
        "mapping_id",
        "component_id",
        string="Clinical Note Fields",
        domain="[('form_id', '=', form_id)]",
        help="Multiple fields are allowed. Their values will be joined by spaces and sent as clinical_note.",
    )
    preferred_template_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="Template Code Field",
        domain="[('form_id', '=', form_id)]",
        ondelete="set null",
    )
    # Backward compatibility for legacy clients/customized views.
    sample_type_mode = fields.Selection(
        [("fixed", "Fixed"), ("field", "From Field")],
        default="fixed",
        string="(Deprecated) Sample Type Source",
    )
    sample_type_fixed = fields.Selection(
        SAMPLE_TYPE_SELECTION,
        default="swab",
        string="(Deprecated) Fixed Sample Type",
    )
    sample_type_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="(Deprecated) Sample Type Field",
        domain="[('form_id', '=', form_id)]",
        ondelete="set null",
    )

    priority_mode = fields.Selection(
        [("fixed", "Fixed"), ("field", "From Field")],
        default="fixed",
        required=True,
    )
    priority_fixed = fields.Selection(
        [("routine", "Routine"), ("urgent", "Urgent"), ("stat", "STAT")],
        default="routine",
        required=True,
    )
    priority_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="Priority Field",
        domain="[('form_id', '=', form_id)]",
        ondelete="set null",
    )

    combo_ids = fields.One2many("x_mobile.lis.mapping.combo", "mapping_id", string="Project/Specimen Combos")
    line_ids = fields.One2many("x_mobile.lis.mapping.line", "mapping_id", string="Request Lines")

    @api.constrains("active", "form_id")
    def _check_single_active_mapping(self):
        for rec in self.filtered(lambda x: x.active and x.form_id):
            others = self.search_count([("id", "!=", rec.id), ("active", "=", True), ("form_id", "=", rec.form_id.id)])
            if others:
                raise ValidationError(
                    _("Only one active LIS mapping is allowed for the same form: %s") % rec.form_id.display_name
                )

    @api.constrains("line_ids", "combo_ids")
    def _check_line_required(self):
        for rec in self:
            if not rec.combo_ids and not rec.line_ids:
                raise ValidationError(_("Mapping '%s' must have at least one request line.") % rec.display_name)

    @api.constrains(
        "form_id",
        "external_uid_component_id",
        "patient_name_component_id",
        "patient_identifier_component_id",
        "patient_gender_component_id",
        "patient_birthdate_component_id",
        "patient_phone_component_id",
        "physician_name_component_id",
        "physician_ref_component_id",
        "clinical_note_component_id",
        "clinical_note_component_ids",
        "preferred_template_component_id",
        "sample_type_component_id",
        "priority_component_id",
    )
    def _check_component_form_consistency(self):
        component_fields = [
            "external_uid_component_id",
            "patient_name_component_id",
            "patient_identifier_component_id",
            "patient_gender_component_id",
            "patient_birthdate_component_id",
            "patient_phone_component_id",
            "physician_name_component_id",
            "physician_ref_component_id",
            "clinical_note_component_id",
            "preferred_template_component_id",
            "sample_type_component_id",
            "priority_component_id",
        ]
        for rec in self:
            if not rec.form_id:
                continue
            for fname in component_fields:
                comp = rec[fname]
                if comp and comp.form_id != rec.form_id:
                    raise ValidationError(_("Field '%s' must use a component from the selected form.") % rec._fields[fname].string)
            for comp in rec.clinical_note_component_ids:
                if comp.form_id != rec.form_id:
                    raise ValidationError(_("Clinical Note Fields must use components from the selected form."))

    @api.model
    def get_active_mapping_for_form(self, form):
        form = form if isinstance(form, models.BaseModel) else self.env["x_mobile.form"].browse(form)
        mapping = self.search([("form_id", "=", form.id), ("active", "=", True)], limit=1)
        if not mapping:
            raise UserError(_("No active LIS mapping found for form '%s'.") % form.display_name)
        return mapping


class MobileFormLisMappingLine(models.Model):
    _name = "x_mobile.lis.mapping.line"
    _description = "Mobile Form LIS Mapping Line"
    _order = "sequence, id"

    mapping_id = fields.Many2one("x_mobile.lis.mapping", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    name = fields.Char(help="Optional label for this request line template.")

    line_type = fields.Selection([("service", "Service"), ("profile", "Profile")], default="service", required=True)
    service_code = fields.Char(string="Service Code (Manual Fallback)")
    profile_code = fields.Char(string="Profile Code (Manual Fallback)")
    service_meta_id = fields.Many2one(
        "x_mobile.lis.meta.item",
        string="Service (From LIS Metadata)",
        domain="[('endpoint_id', '=', mapping_id.endpoint_id), ('item_type', '=', 'service'), ('active', '=', True)]",
        ondelete="set null",
        help="Recommended: choose from LIS metadata. If empty, manual fallback code is used.",
    )
    profile_meta_id = fields.Many2one(
        "x_mobile.lis.meta.item",
        string="Profile (From LIS Metadata)",
        domain="[('endpoint_id', '=', mapping_id.endpoint_id), ('item_type', '=', 'profile'), ('active', '=', True)]",
        ondelete="set null",
        help="Recommended: choose from LIS metadata. If empty, manual fallback code is used.",
    )

    specimen_ref_mode = fields.Selection([("fixed", "Fixed"), ("field", "From Field")], default="fixed", required=True)
    specimen_ref_fixed = fields.Char(default="SP1", required=True)
    specimen_ref_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="Specimen Ref Field",
        ondelete="set null",
    )
    specimen_barcode_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="Specimen Barcode Field",
        ondelete="set null",
    )
    specimen_sample_type_mode = fields.Selection(
        [("fixed", "Fixed"), ("field", "From Field")],
        default="fixed",
        required=True,
        string="Specimen Sample Type Source",
    )
    specimen_sample_type_fixed = fields.Selection(
        SAMPLE_TYPE_SELECTION,
        default="swab",
        required=True,
        string="Fixed Specimen Sample Type (Manual Fallback)",
        help="Used when source is Fixed and no metadata item is selected.",
    )
    specimen_sample_type_meta_id = fields.Many2one(
        "x_mobile.lis.meta.item",
        string="Fixed Specimen Sample Type (From LIS Metadata)",
        domain="[('endpoint_id', '=', mapping_id.endpoint_id), ('item_type', '=', 'sample_type'), ('active', '=', True)]",
        ondelete="set null",
        help="Recommended: choose from LIS metadata. If empty, manual fallback value is used.",
    )
    specimen_sample_type_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="Specimen Sample Type Field",
        ondelete="set null",
    )
    note_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="Line Note Field",
        ondelete="set null",
    )

    @api.constrains("line_type", "service_code", "profile_code", "service_meta_id", "profile_meta_id", "quantity")
    def _check_line_values(self):
        for rec in self:
            if rec.quantity <= 0:
                raise ValidationError(_("Request line quantity must be greater than 0."))
            if rec.line_type == "service" and not ((rec.service_code or "").strip() or rec.service_meta_id):
                raise ValidationError(_("Service code is required when line type is Service."))
            if rec.line_type == "profile" and not ((rec.profile_code or "").strip() or rec.profile_meta_id):
                raise ValidationError(_("Profile code is required when line type is Profile."))

    @api.constrains(
        "mapping_id",
        "specimen_ref_component_id",
        "specimen_barcode_component_id",
        "specimen_sample_type_component_id",
        "note_component_id",
    )
    def _check_line_component_form_consistency(self):
        for rec in self:
            form = rec.mapping_id.form_id
            if not form:
                continue
            if rec.service_meta_id and rec.service_meta_id.endpoint_id != rec.mapping_id.endpoint_id:
                raise ValidationError(_("Service (From API) must belong to mapping endpoint metadata."))
            if rec.profile_meta_id and rec.profile_meta_id.endpoint_id != rec.mapping_id.endpoint_id:
                raise ValidationError(_("Profile (From API) must belong to mapping endpoint metadata."))
            if rec.specimen_sample_type_meta_id and rec.specimen_sample_type_meta_id.endpoint_id != rec.mapping_id.endpoint_id:
                raise ValidationError(_("Specimen Sample Type (From API) must belong to mapping endpoint metadata."))
            for comp in (
                rec.specimen_ref_component_id,
                rec.specimen_barcode_component_id,
                rec.specimen_sample_type_component_id,
                rec.note_component_id,
            ):
                if comp and comp.form_id != form:
                    raise ValidationError(_("Line components must belong to mapping form '%s'.") % form.display_name)


class MobileFormLisMappingCombo(models.Model):
    _name = "x_mobile.lis.mapping.combo"
    _description = "Mobile Form LIS Mapping Combo"
    _order = "sequence, id"

    mapping_id = fields.Many2one("x_mobile.lis.mapping", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, default="Combo")
    endpoint_id = fields.Many2one(related="mapping_id.endpoint_id", store=True, readonly=True)
    form_id = fields.Many2one(related="mapping_id.form_id", store=True, readonly=True)
    service_meta_ids = fields.Many2many(
        "x_mobile.lis.meta.item",
        "x_mobile_lis_mapping_combo_service_rel",
        "combo_id",
        "meta_id",
        string="Services",
        domain="[('endpoint_id', '=', endpoint_id), ('item_type', '=', 'service'), ('active', '=', True)]",
    )
    profile_meta_ids = fields.Many2many(
        "x_mobile.lis.meta.item",
        "x_mobile_lis_mapping_combo_profile_rel",
        "combo_id",
        "meta_id",
        string="Profiles",
        domain="[('endpoint_id', '=', endpoint_id), ('item_type', '=', 'profile'), ('active', '=', True)]",
    )
    quantity = fields.Integer(default=1, required=True)
    note_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="Line Note Field",
        domain="[('form_id', '=', form_id)]",
        ondelete="set null",
    )
    specimen_ids = fields.One2many("x_mobile.lis.mapping.specimen", "combo_id", string="Specimens")
    service_count = fields.Integer(compute="_compute_counts")
    profile_count = fields.Integer(compute="_compute_counts")
    specimen_count = fields.Integer(compute="_compute_counts")

    @api.depends("service_meta_ids", "profile_meta_ids", "specimen_ids")
    def _compute_counts(self):
        for rec in self:
            rec.service_count = len(rec.service_meta_ids)
            rec.profile_count = len(rec.profile_meta_ids)
            rec.specimen_count = len(rec.specimen_ids)

    @api.constrains("service_meta_ids", "profile_meta_ids", "specimen_ids")
    def _check_combo_values(self):
        for rec in self:
            if not rec.service_meta_ids and not rec.profile_meta_ids:
                raise ValidationError(_("Combo '%s' must bind at least one service or profile.") % rec.display_name)
            if not rec.specimen_ids:
                raise ValidationError(_("Combo '%s' must have at least one specimen row.") % rec.display_name)
            for meta in rec.service_meta_ids:
                if meta.endpoint_id != rec.endpoint_id:
                    raise ValidationError(_("Combo services must belong to selected endpoint metadata."))
            for meta in rec.profile_meta_ids:
                if meta.endpoint_id != rec.endpoint_id:
                    raise ValidationError(_("Combo profiles must belong to selected endpoint metadata."))


class MobileFormLisMappingSpecimen(models.Model):
    _name = "x_mobile.lis.mapping.specimen"
    _description = "Mobile Form LIS Mapping Specimen"
    _order = "sequence, id"

    combo_id = fields.Many2one("x_mobile.lis.mapping.combo", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    name = fields.Char(default="Specimen")
    mapping_id = fields.Many2one(related="combo_id.mapping_id", store=True, readonly=True)
    endpoint_id = fields.Many2one(related="combo_id.endpoint_id", store=True, readonly=True)
    form_id = fields.Many2one(related="combo_id.form_id", store=True, readonly=True)

    specimen_ref_mode = fields.Selection([("fixed", "Fixed"), ("field", "From Field")], default="fixed", required=True)
    specimen_ref_fixed = fields.Char(default="SP1", required=True)
    specimen_ref_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="Specimen Ref Field",
        domain="[('form_id', '=', form_id)]",
        ondelete="set null",
    )
    specimen_barcode_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="Specimen Barcode Field",
        domain="[('form_id', '=', form_id)]",
        ondelete="set null",
    )
    specimen_sample_type_mode = fields.Selection(
        [("fixed", "Fixed"), ("field", "From Field")],
        default="fixed",
        required=True,
        string="Specimen Sample Type Source",
    )
    specimen_sample_type_fixed = fields.Selection(
        SAMPLE_TYPE_SELECTION,
        default="swab",
        required=True,
        string="Fixed Specimen Sample Type (Manual Fallback)",
    )
    specimen_sample_type_meta_id = fields.Many2one(
        "x_mobile.lis.meta.item",
        string="Fixed Specimen Sample Type (From LIS Metadata)",
        domain="[('endpoint_id', '=', endpoint_id), ('item_type', '=', 'sample_type'), ('active', '=', True)]",
        ondelete="set null",
    )
    specimen_sample_type_component_id = fields.Many2one(
        "x_mobile.form.component",
        string="Specimen Sample Type Field",
        domain="[('form_id', '=', form_id)]",
        ondelete="set null",
    )

    @api.constrains(
        "specimen_ref_component_id",
        "specimen_barcode_component_id",
        "specimen_sample_type_component_id",
        "specimen_sample_type_meta_id",
    )
    def _check_specimen_consistency(self):
        for rec in self:
            if rec.specimen_sample_type_meta_id and rec.specimen_sample_type_meta_id.endpoint_id != rec.endpoint_id:
                raise ValidationError(_("Specimen sample type metadata must belong to selected endpoint metadata."))
            for comp in (
                rec.specimen_ref_component_id,
                rec.specimen_barcode_component_id,
                rec.specimen_sample_type_component_id,
            ):
                if comp and comp.form_id != rec.form_id:
                    raise ValidationError(_("Specimen components must belong to mapping form '%s'.") % rec.form_id.display_name)


class MobileFormSubmission(models.Model):
    _inherit = "x_mobile.form.submission"

    lis_push_state = fields.Selection(
        [("none", "Not Pushed"), ("success", "Pushed"), ("failed", "Failed")],
        default="none",
        copy=False,
        index=True,
    )
    lis_push_time = fields.Datetime(copy=False)
    lis_push_message = fields.Char(copy=False)
    lis_request_no = fields.Char(copy=False, index=True)
    lis_last_mapping_id = fields.Many2one("x_mobile.lis.mapping", readonly=True, copy=False)

    def _lis_answer_map(self):
        self.ensure_one()
        payload = {}
        if self.answer_json:
            try:
                parsed = json.loads(self.answer_json)
                if isinstance(parsed, dict):
                    payload = parsed
            except Exception:
                payload = {}
        if not payload and self.line_ids:
            payload = {line.key: line.value_text for line in self.line_ids if line.key}
        return payload

    def _lis_value_from_component(self, component, answer_map, default=""):
        if not component or not component.key:
            return default
        value = answer_map.get(component.key)
        if value is None:
            return default
        if isinstance(value, (list, tuple, set)):
            return ",".join(str(v).strip() for v in value if str(v).strip()) or default
        if isinstance(value, dict):
            for k in ("text", "value", "code", "raw", "data"):
                v = value.get(k)
                if isinstance(v, (str, int, float)) and str(v).strip():
                    return str(v).strip()
            return default
        text = str(value).strip()
        return text or default

    def _build_lis_payload(self, mapping):
        self.ensure_one()
        answer_map = self._lis_answer_map()
        payload = {}

        external_uid = self._lis_value_from_component(mapping.external_uid_component_id, answer_map, default="")
        if external_uid:
            payload["external_uid"] = external_uid

        patient = {}
        patient_name = self._lis_value_from_component(mapping.patient_name_component_id, answer_map, default="")
        if patient_name:
            patient["name"] = patient_name
        patient_identifier = self._lis_value_from_component(mapping.patient_identifier_component_id, answer_map, default="")
        if patient_identifier:
            patient["identifier"] = patient_identifier
        patient_gender = self._lis_value_from_component(mapping.patient_gender_component_id, answer_map, default="").lower()
        if patient_gender:
            patient["gender"] = patient_gender if patient_gender in ("male", "female", "other", "unknown") else "unknown"
        patient_birthdate = self._lis_value_from_component(mapping.patient_birthdate_component_id, answer_map, default="")
        if patient_birthdate:
            patient["birthdate"] = patient_birthdate
        patient_phone = self._lis_value_from_component(mapping.patient_phone_component_id, answer_map, default="")
        if patient_phone:
            patient["phone"] = patient_phone
        if patient:
            payload["patient"] = patient

        physician = {}
        physician_name = self._lis_value_from_component(mapping.physician_name_component_id, answer_map, default="")
        if physician_name:
            physician["name"] = physician_name
        physician_ref = self._lis_value_from_component(mapping.physician_ref_component_id, answer_map, default="")
        if physician_ref:
            physician["partner_ref"] = physician_ref
        if physician:
            payload["physician"] = physician

        priority = mapping.priority_fixed if mapping.priority_mode == "fixed" else self._lis_value_from_component(
            mapping.priority_component_id, answer_map, default=mapping.priority_fixed
        )
        if priority:
            payload["priority"] = priority

        clinical_note = ""
        if mapping.clinical_note_component_ids:
            note_parts = []
            for comp in mapping.clinical_note_component_ids.sorted(lambda c: (c.sequence or 999999, c.id)):
                val = self._lis_value_from_component(comp, answer_map, default="")
                if val:
                    note_parts.append(val)
            clinical_note = " ".join(note_parts).strip()
        if not clinical_note:
            clinical_note = self._lis_value_from_component(mapping.clinical_note_component_id, answer_map, default="")
        if clinical_note:
            payload["clinical_note"] = clinical_note
        preferred_template = self._lis_value_from_component(mapping.preferred_template_component_id, answer_map, default="")
        if preferred_template:
            payload["preferred_template_code"] = preferred_template

        lines = []
        if mapping.combo_ids:
            for combo in mapping.combo_ids.sorted("sequence"):
                note = self._lis_value_from_component(combo.note_component_id, answer_map, default="")
                for specimen in combo.specimen_ids.sorted("sequence"):
                    specimen_ref = (
                        (specimen.specimen_ref_fixed or "SP1")
                        if specimen.specimen_ref_mode == "fixed"
                        else self._lis_value_from_component(
                            specimen.specimen_ref_component_id,
                            answer_map,
                            default=specimen.specimen_ref_fixed or "SP1",
                        )
                    )
                    specimen_barcode = self._lis_value_from_component(
                        specimen.specimen_barcode_component_id, answer_map, default=""
                    )
                    specimen_sample_type = (
                        (specimen.specimen_sample_type_meta_id.code if specimen.specimen_sample_type_meta_id else specimen.specimen_sample_type_fixed)
                        if specimen.specimen_sample_type_mode == "fixed"
                        else self._lis_value_from_component(
                            specimen.specimen_sample_type_component_id,
                            answer_map,
                            default=specimen.specimen_sample_type_fixed,
                        )
                    )
                    for meta in combo.service_meta_ids:
                        line_payload = {
                            "line_type": "service",
                            "service_code": (meta.code or "").strip(),
                            "quantity": 1,
                            "specimen_ref": specimen_ref or "SP1",
                        }
                        if specimen_barcode:
                            line_payload["specimen_barcode"] = specimen_barcode
                        if specimen_sample_type:
                            line_payload["specimen_sample_type"] = specimen_sample_type
                        if note:
                            line_payload["note"] = note
                        lines.append(line_payload)
                    for meta in combo.profile_meta_ids:
                        line_payload = {
                            "line_type": "profile",
                            "profile_code": (meta.code or "").strip(),
                            "quantity": 1,
                            "specimen_ref": specimen_ref or "SP1",
                        }
                        if specimen_barcode:
                            line_payload["specimen_barcode"] = specimen_barcode
                        if specimen_sample_type:
                            line_payload["specimen_sample_type"] = specimen_sample_type
                        if note:
                            line_payload["note"] = note
                        lines.append(line_payload)
        else:
            for line in mapping.line_ids.sorted("sequence"):
                line_payload = {
                    "line_type": line.line_type,
                    "quantity": 1,
                }
                if line.line_type == "service":
                    code = (line.service_meta_id.code if line.service_meta_id else line.service_code) or ""
                    line_payload["service_code"] = code.strip()
                else:
                    code = (line.profile_meta_id.code if line.profile_meta_id else line.profile_code) or ""
                    line_payload["profile_code"] = code.strip()

                specimen_ref = (
                    (line.specimen_ref_fixed or "SP1")
                    if line.specimen_ref_mode == "fixed"
                    else self._lis_value_from_component(line.specimen_ref_component_id, answer_map, default=line.specimen_ref_fixed or "SP1")
                )
                line_payload["specimen_ref"] = specimen_ref or "SP1"

                specimen_barcode = self._lis_value_from_component(line.specimen_barcode_component_id, answer_map, default="")
                if specimen_barcode:
                    line_payload["specimen_barcode"] = specimen_barcode

                specimen_sample_type = (
                    (line.specimen_sample_type_meta_id.code if line.specimen_sample_type_meta_id else line.specimen_sample_type_fixed)
                    if line.specimen_sample_type_mode == "fixed"
                    else self._lis_value_from_component(
                        line.specimen_sample_type_component_id,
                        answer_map,
                        default=line.specimen_sample_type_fixed,
                    )
                )
                if specimen_sample_type:
                    line_payload["specimen_sample_type"] = specimen_sample_type

                note = self._lis_value_from_component(line.note_component_id, answer_map, default="")
                if note:
                    line_payload["note"] = note

                lines.append(line_payload)
        payload["lines"] = lines
        return payload

    def action_push_to_lis(self):
        records = self._get_selected_records()
        if not (
            self.env.user.has_group("mobile_form_builder.group_mobile_form_admin")
            or self.env.user.has_group("mobile_form_builder.group_mobile_form_user")
        ):
            raise UserError(_("You do not have permission to push submissions to LIS."))

        success = 0
        failed = 0
        failed_names = []
        mapping_obj = self.env["x_mobile.lis.mapping"].sudo()
        now = fields.Datetime.now()

        for rec in records:
            mapping = False
            try:
                mapping = mapping_obj.get_active_mapping_for_form(rec.form_id)
                payload = rec._build_lis_payload(mapping)
                response = mapping.endpoint_id.sudo().push_external_request(payload)
                if not response.get("ok"):
                    raise UserError(response.get("error") or _("LIS API returned failed response."))
                request_info = response.get("request") or {}
                rec.sudo().write(
                    {
                        "lis_push_state": "success",
                        "lis_push_time": now,
                        "lis_push_message": "",
                        "lis_request_no": request_info.get("request_no") or False,
                        "lis_last_mapping_id": mapping.id,
                    }
                )
                success += 1
            except Exception as err:
                failed += 1
                failed_names.append(rec.display_name)
                rec.sudo().write(
                    {
                        "lis_push_state": "failed",
                        "lis_push_time": now,
                        "lis_push_message": str(err)[:1024],
                        "lis_last_mapping_id": mapping.id if mapping else False,
                    }
                )

        level = "success" if failed == 0 else ("warning" if success else "danger")
        msg = _("LIS push done. Success: %(ok)s, Failed: %(fail)s") % {"ok": success, "fail": failed}
        if failed_names:
            msg = msg + "\n" + _("Failed submissions: %s") % ", ".join(failed_names[:10])
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Push to LIS"),
                "message": msg,
                "type": level,
                "sticky": failed > 0,
            },
        }
