# -*- coding: utf-8 -*-
from odoo import fields, models


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    portal_project_visible = fields.Boolean(
        string='Visible en portal Control de Obra',
        help='Permite mostrar este adjunto a clientes dentro del portal de Control de Obra.',
    )
