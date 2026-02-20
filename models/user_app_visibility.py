from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    visible_app_menu_ids = fields.Many2many(
        "ir.ui.menu",
        "res_users_visible_app_menu_rel",
        "user_id",
        "menu_id",
        string="Visible Apps",
        domain=[("parent_id", "=", False), ("active", "=", True)],
        help="If set, only the selected apps are shown on the desktop for this user.",
    )

    def write(self, vals):
        result = super().write(vals)
        if "visible_app_menu_ids" in vals:
            self.env.registry.clear_cache()
        return result


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    def _allowed_app_ids_for_user(self):
        user = self.env.user
        if user.has_group("base.group_system"):
            return set()
        allowed_roots = user.visible_app_menu_ids.filtered("active")
        if not allowed_roots:
            return set()
        return set(allowed_roots.ids)

    def get_user_roots(self):
        roots = super().get_user_roots()
        allowed_ids = self._allowed_app_ids_for_user()
        if not allowed_ids:
            return roots
        return roots.filtered(lambda menu: menu.id in allowed_ids)

    def load_menus(self, debug):
        menus = super().load_menus(debug)
        allowed_ids = self._allowed_app_ids_for_user()
        if not allowed_ids:
            return menus

        filtered = {}
        for key, item in menus.items():
            if key == "root":
                continue
            if not isinstance(key, int):
                continue
            app_id = item.get("app_id")
            if app_id not in allowed_ids:
                continue
            copied = dict(item)
            copied["children"] = [
                cid
                for cid in item.get("children", [])
                if cid in menus and menus.get(cid, {}).get("app_id") in allowed_ids
            ]
            filtered[key] = copied

        root = menus.get("root", {"id": False, "name": "root", "children": []})
        filtered["root"] = {
            "id": root.get("id", False),
            "name": root.get("name", "root"),
            "children": [cid for cid in root.get("children", []) if cid in allowed_ids],
        }
        return filtered
