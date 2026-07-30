# -*- coding: utf-8 -*-

from odoo import api, fields, models


PORTAL_MOVEMENT_CATEGORY_SELECTION = [
    ('materials', 'Materiales'),
    ('labor', 'Mano de Obra'),
    ('equipment_tools', 'Equipo y Herramientas'),
    ('external_services', 'Servicios Externos'),
]


class ProductCategory(models.Model):
    _inherit = 'product.category'

    portal_movement_category = fields.Selection(
        selection=PORTAL_MOVEMENT_CATEGORY_SELECTION,
        string='Categoría PU',
        index=True,
        help='Clasificación PU que se asignará a los productos de esta categoría.',
    )

    def write(self, vals):
        result = super().write(vals)
        movement_category = vals.get('portal_movement_category')
        if movement_category:
            products = self.env['product.template'].search([
                ('categ_id', 'in', self.ids),
            ])
            products.write({
                'portal_movement_category': movement_category,
            })
        return result


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    portal_movement_category = fields.Selection(
        selection=PORTAL_MOVEMENT_CATEGORY_SELECTION,
        string='Categoría PU',
        default='materials',
        index=True,
        help='Clasificación utilizada para agrupar el producto en los movimientos del portal.',
    )

    @api.onchange('categ_id')
    def _onchange_categ_id_portal_movement_category(self):
        for product in self:
            if product.categ_id.portal_movement_category:
                product.portal_movement_category = (
                    product.categ_id.portal_movement_category
                )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            category = self.env['product.category'].browse(vals.get('categ_id'))
            if category.portal_movement_category:
                vals['portal_movement_category'] = (
                    category.portal_movement_category
                )
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('categ_id'):
            category = self.env['product.category'].browse(vals['categ_id'])
            if category.portal_movement_category:
                vals['portal_movement_category'] = (
                    category.portal_movement_category
                )
        return super().write(vals)
