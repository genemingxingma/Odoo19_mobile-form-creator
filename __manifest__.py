{
    "name": "Mobile Form Designer",
    "summary": "Design shareable mobile data collection forms with QR links",
    "version": "19.0.1.0.0",
    "category": "Tools",
    "author": "mamingxing",
    "company": "iMyTest",
    "images": ["static/description/icon.png"],
    "license": "LGPL-3",
    "depends": ["base", "web", "website"],
    "data": [
        "data/sequence.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/confirm_wizard_views.xml",
        "views/form_builder_views.xml",
        "views/form_builder_templates.xml",
        "views/form_builder_report.xml",
    ],
    "assets": {
        # In Odoo 19 the backend webclient loads `web.assets_web`.
        "web.assets_web": [
            "mobile_form_builder/static/src/css/back_collapse.css",
            "mobile_form_builder/static/src/js/back_collapse.js",
            "mobile_form_builder/static/src/js/submission_list_confirm.js",
            "mobile_form_builder/static/src/js/confirm_wizard_enter.js",
            "mobile_form_builder/static/src/xml/submission_list_confirm.xml",
        ],
        "web.assets_frontend": [
            "mobile_form_builder/static/src/css/mobile_form.css",
            "mobile_form_builder/static/src/js/mobile_form.js",
        ],
    },
    "pre_init_hook": "pre_init_check_barcode_dependencies",
    "application": True,
    "installable": True,
}
