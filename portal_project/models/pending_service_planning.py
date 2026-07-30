from odoo import api, fields, models


class PendingService(models.Model):
    _inherit = 'pending.service'

    planned_material_ids = fields.One2many(
        'pending.service.planned.material',
        'service_id',
        string='Materiales Planeados',
    )
    planned_labor_ids = fields.One2many(
        'pending.service.planned.labor',
        'service_id',
        string='Mano de Obra Planeada',
    )
    planned_service_ex_ids = fields.One2many(
        'pending.service.planned.service.externo',
        'service_id',
        string='Servicios Externos',
    )


class PendingServicePlannedMaterial(models.Model):
    _name = 'pending.service.planned.material'
    _description = 'Material, Equipo/Herramienta y Servicio Planeado'
    _order = 'sequence, id'

    service_id = fields.Many2one('pending.service', string='Servicio Pendiente', required=True, ondelete='cascade')
    sequence = fields.Integer(string='Secuencia', default=10)
    codigo = fields.Integer(string='Código', compute='_compute_codigo')
    service_line_id = fields.Many2one(
        'pending.service.line',
        string='Actividad',
        domain="[('service_id', '=', service_id)]",
    )
    tipo_recurso = fields.Selection([
        ('material', 'Material'),
        ('equipo_herramienta', 'Equipos/Herramientas'),
        ('servicio', 'Servicio'),
    ], string='Tipo de recurso', required=True, default='material')
    allowed_product_ids = fields.Many2many(
        'product.product',
        compute='_compute_allowed_product_ids',
        string='Recursos permitidos',
    )
    product_id = fields.Many2one('product.product', string='Recurso', required=True)
    qty_planned = fields.Float(string='Cantidad Planeada', default=0.0)
    expected_consumption_date = fields.Date(
        string='Fecha consumo esperado',
        help='Fecha en que se espera consumir este costo. Esta fecha alimenta la curva de Costo Planeado.',
    )
    cost_unit = fields.Monetary(
        string='Valor Unitario',
        currency_field='currency_id',
        compute='_compute_cost_unit',
        store=True,
        readonly=False,
    )
    cost_planned = fields.Monetary(
        string='Valor Total',
        currency_field='currency_id',
        compute='_compute_cost_planned',
        store=True,
    )
    qty_used = fields.Float(string='Cantidad Consumida', compute='_compute_actuals')
    cost_used = fields.Monetary(string='Costo Consumido', currency_field='currency_id', compute='_compute_actuals')
    currency_id = fields.Many2one('res.currency', string='Moneda', related='service_id.company_id.currency_id', readonly=True)

    def init(self):
        self.env.cr.execute("""
            UPDATE pending_service_planned_material
               SET tipo_recurso = 'equipo_herramienta'
             WHERE tipo_recurso IN ('equipo', 'herramienta')
        """)

    @api.depends('tipo_recurso')
    def _compute_allowed_product_ids(self):
        Product = self.env['product.product']
        Category = self.env['product.category']
        all_products = Product.search([])
        if 'pending_resource_type' not in Category._fields:
            for line in self:
                line.allowed_product_ids = all_products
            return
        for line in self:
            categories = Category.search([
                ('pending_resource_type', '=', line.tipo_recurso or 'material')
            ])
            if categories:
                line.allowed_product_ids = Product.search([
                    ('categ_id', 'child_of', categories.ids)
                ])
            else:
                line.allowed_product_ids = all_products

    @api.onchange('tipo_recurso')
    def _onchange_tipo_recurso(self):
        if self.product_id and self.product_id not in self.allowed_product_ids:
            self.product_id = False

    @api.depends('service_id.planned_material_ids')
    def _compute_codigo(self):
        for line in self:
            line.codigo = 0
        for service in self.mapped('service_id'):
            def _sort_key(material):
                material_id = material.id
                return (material.sequence, material_id if isinstance(material_id, int) else 0)
            for index, material in enumerate(service.planned_material_ids.sorted(key=_sort_key), 1):
                material.codigo = index

    @api.depends('product_id')
    def _compute_cost_unit(self):
        for line in self:
            line.cost_unit = line.product_id.standard_price if line.product_id else 0.0

    @api.depends('cost_unit', 'qty_planned')
    def _compute_cost_planned(self):
        for line in self:
            line.cost_planned = line.cost_unit * line.qty_planned

    def _compute_actuals(self):
        for line in self:
            tasks = line.service_id.task_ids | line.service_id.service_line_ids.mapped('task_id')
            qty_used = 0.0
            cost_used = 0.0
            if tasks and line.product_id:
                moves = self.env['stock.move'].sudo().search([
                    ('task_id', 'in', tasks.ids),
                    ('product_id', '=', line.product_id.id),
                    ('state', '=', 'done'),
                ])
                qty_used += sum(moves.mapped('product_qty'))
                cost_used += sum((m.price_unit or m.product_id.standard_price or 0.0) * (m.quantity or 0.0) for m in moves)

                purchase_lines = self.env['purchase.order.line'].sudo().search([
                    ('task_id', 'in', tasks.ids),
                    ('product_id', '=', line.product_id.id),
                    ('order_id.state', 'in', ('purchase', 'done')),
                ])
                consumed_purchase_lines = (
                    moves.mapped('purchase_line_id')
                    if 'purchase_line_id' in moves._fields
                    else self.env['purchase.order.line']
                )
                if not consumed_purchase_lines:
                    consumed_purchase_lines = purchase_lines.filtered(
                        lambda pl: bool(pl.move_ids & moves) if 'move_ids' in pl._fields else False
                    )
                purchase_lines_to_cost = purchase_lines - consumed_purchase_lines
                cost_used += sum(purchase_lines_to_cost.mapped('price_subtotal'))

                expenses = self.env['hr.expense'].sudo().search([
                    ('task_id', 'in', tasks.ids),
                    ('product_id', '=', line.product_id.id),
                    ('sheet_id.state', 'in', ['approve', 'post', 'done']),
                ])
                cost_used += sum(expenses.mapped('total_amount'))

            line.qty_used = qty_used
            line.cost_used = cost_used


class PendingServicePlannedLabor(models.Model):
    _name = 'pending.service.planned.labor'
    _description = 'Mano de Obra Planeada'
    _order = 'sequence, id'

    service_id = fields.Many2one('pending.service', string='Servicio Pendiente', required=True, ondelete='cascade')
    sequence = fields.Integer(string='Secuencia', default=10)
    codigo = fields.Integer(string='Código', compute='_compute_codigo')
    service_line_id = fields.Many2one(
        'pending.service.line',
        string='Actividad',
        domain="[('service_id', '=', service_id)]",
    )
    employee_id = fields.Many2one('hr.employee', string='Empleado', required=True)
    description = fields.Char(string='Descripción')
    job_id = fields.Many2one(
        'hr.job',
        string='Categoría',
        compute='_compute_job_id',
        store=True,
        readonly=False,
    )
    hours_planned = fields.Float(string='Horas Planeadas', default=0.0)
    expected_consumption_date = fields.Date(
        string='Fecha consumo esperado',
        help='Fecha en que se espera consumir este costo. Esta fecha alimenta la curva de Costo Planeado.',
    )
    cost_unit = fields.Monetary(
        string='Valor Unitario',
        currency_field='currency_id',
        compute='_compute_cost_unit',
        store=True,
        readonly=False,
    )
    cost_planned = fields.Monetary(
        string='Valor Total',
        currency_field='currency_id',
        compute='_compute_cost_planned',
        store=True,
    )
    hours_used = fields.Float(string='Horas Reales', compute='_compute_actuals')
    cost_used = fields.Monetary(string='Costo Consumido', currency_field='currency_id', compute='_compute_actuals')
    currency_id = fields.Many2one('res.currency', string='Moneda', related='service_id.company_id.currency_id', readonly=True)

    @api.depends('service_id.planned_labor_ids')
    def _compute_codigo(self):
        for line in self:
            line.codigo = 0
        for service in self.mapped('service_id'):
            def _sort_key(labor):
                labor_id = labor.id
                return (labor.sequence, labor_id if isinstance(labor_id, int) else 0)
            for index, labor in enumerate(service.planned_labor_ids.sorted(key=_sort_key), 1):
                labor.codigo = index

    @api.depends('employee_id')
    def _compute_job_id(self):
        for line in self:
            line.job_id = line.employee_id.job_id if line.employee_id else False

    @api.depends('employee_id')
    def _compute_cost_unit(self):
        for line in self:
            if line.employee_id:
                line.cost_unit = getattr(line.employee_id, 'hourly_cost', 0.0) or getattr(line.employee_id, 'timesheet_cost', 0.0)
            else:
                line.cost_unit = 0.0

    @api.depends('cost_unit', 'hours_planned')
    def _compute_cost_planned(self):
        for line in self:
            line.cost_planned = line.cost_unit * line.hours_planned

    def _compute_actuals(self):
        for line in self:
            tasks = line.service_id.task_ids | line.service_id.service_line_ids.mapped('task_id')
            hours_used = 0.0
            cost_used = 0.0
            if tasks and line.employee_id:
                labor_lines = self.env['compensation.line'].sudo().search([
                    ('task_id', 'in', tasks.ids),
                    ('employee_id', '=', line.employee_id.id),
                ])
                hours_used += sum(labor_lines.mapped('regular_hours') + labor_lines.mapped('extra_hours'))
                cost_used += sum(labor_lines.mapped('total_cost'))

            line.hours_used = hours_used
            line.cost_used = cost_used


class PendingServicePlannedServiceEx(models.Model):
    _name = 'pending.service.planned.service.externo'
    _description = 'Servicio Externo Planeado'
    _order = 'sequence, id'

    service_id = fields.Many2one('pending.service', string='Servicio Pendiente', required=True, ondelete='cascade')
    sequence = fields.Integer(string='Secuencia', default=10)
    codigo = fields.Integer(string='Código', compute='_compute_codigo')
    service_line_id = fields.Many2one(
        'pending.service.line',
        string='Actividad',
        domain="[('service_id', '=', service_id)]",
    )
    product_id = fields.Many2one(
        'product.product',
        string='Servicio/Recurso',
        required=True,
        domain="[('type', '=', 'service')]",
    )
    description = fields.Char(
        string='Descripción del Servicio',
        compute='_compute_description',
        store=True,
        readonly=False,
    )
    partner_id = fields.Many2one('res.partner', string='Proveedor')
    uom_id = fields.Many2one(
        'uom.uom',
        string='Unidad de medida',
        compute='_compute_uom_id',
        store=True,
        readonly=False,
    )
    qty_planned = fields.Float(string='Cantidad Planeada', default=1.0)
    expected_consumption_date = fields.Date(
        string='Fecha consumo esperado',
        help='Fecha en que se espera consumir este costo. Esta fecha alimenta la curva de Costo Planeado.',
    )
    cost_unit = fields.Monetary(
        string='Costo Unitario',
        currency_field='currency_id',
        compute='_compute_cost_unit',
        store=True,
        readonly=False,
    )
    cost_planned = fields.Monetary(
        string='Valor Total',
        currency_field='currency_id',
        compute='_compute_cost_planned',
        store=True,
    )
    qty_used = fields.Float(string='Cantidad Consumida', compute='_compute_actuals')
    cost_used = fields.Monetary(string='Costo Consumido', currency_field='currency_id', compute='_compute_actuals')
    currency_id = fields.Many2one('res.currency', string='Moneda', related='service_id.company_id.currency_id', readonly=True)

    @api.depends('service_id.planned_service_ex_ids')
    def _compute_codigo(self):
        for line in self:
            line.codigo = 0
        for service in self.mapped('service_id'):
            services = service.planned_service_ex_ids.sorted(
                key=lambda service_line: (service_line.sequence, service_line.id if isinstance(service_line.id, int) else 0)
            )
            for index, service_line in enumerate(services, 1):
                service_line.codigo = index

    @api.depends('product_id')
    def _compute_description(self):
        for line in self:
            line.description = line.product_id.display_name if line.product_id else ''

    @api.depends('product_id')
    def _compute_uom_id(self):
        for line in self:
            line.uom_id = line.product_id.uom_id if line.product_id else False

    @api.depends('product_id')
    def _compute_cost_unit(self):
        for line in self:
            line.cost_unit = line.product_id.standard_price if line.product_id else 0.0

    @api.depends('cost_unit', 'qty_planned')
    def _compute_cost_planned(self):
        for line in self:
            line.cost_planned = line.cost_unit * line.qty_planned

    def _compute_actuals(self):
        for line in self:
            tasks = line.service_id.task_ids | line.service_id.service_line_ids.mapped('task_id')
            qty_used = 0.0
            cost_used = 0.0
            if tasks and line.product_id:
                purchase_lines = self.env['purchase.order.line'].sudo().search([
                    ('task_id', 'in', tasks.ids),
                    ('product_id', '=', line.product_id.id),
                    ('order_id.state', 'in', ('purchase', 'done')),
                ])
                if line.partner_id:
                    purchase_lines = purchase_lines.filtered(lambda pl: pl.partner_id == line.partner_id)

                qty_used += sum(purchase_lines.mapped('product_qty'))
                cost_used += sum(purchase_lines.mapped('price_subtotal'))

                expenses = self.env['hr.expense'].sudo().search([
                    ('task_id', 'in', tasks.ids),
                    ('product_id', '=', line.product_id.id),
                    ('sheet_id.state', 'in', ['approve', 'post', 'done']),
                ])
                if line.partner_id:
                    expenses = expenses.filtered(lambda expense: getattr(expense, 'partner_id', False) == line.partner_id)

                qty_used += sum(expenses.mapped('quantity'))
                cost_used += sum(expenses.mapped('total_amount'))

            line.qty_used = qty_used
            line.cost_used = cost_used
