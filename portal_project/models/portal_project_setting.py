# -*- coding: utf-8 -*-
from odoo import fields, models


class PortalProjectPartnerSetting(models.Model):
    _name = 'portal.project.partner.setting'
    _description = 'Configuración portal control de obra por cliente'

    partner_id = fields.Many2one(
        'res.partner',
        string='Cliente',
        required=True,
        ondelete='cascade',
        index=True,
    )
    profit_percentage = fields.Float(string='Porcentaje utilidad control de obra')

    _sql_constraints = [
        (
            'partner_unique',
            'unique(partner_id)',
            'Solo puede existir una configuración de portal por cliente.',
        ),
    ]
