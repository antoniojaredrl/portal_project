# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class PortalProjectCostApproval(models.Model):
    _name = 'portal.project.cost.approval'
    _description = 'Corte de costos para aprobación del cliente'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'task_id, version desc, id desc'

    name = fields.Char(string='Referencia', required=True, readonly=True, default=lambda self: _('Nuevo'))
    task_id = fields.Many2one('project.task', string='Tarea / OT', required=True, ondelete='cascade', index=True)
    version = fields.Integer(string='Versión del corte', required=True, readonly=True, default=1)
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('supervisor_review', 'Pendiente Supervisor Cliente'),
        ('purchase_review', 'Pendiente Compras Cliente'),
        ('approved', 'Aprobado'),
        ('rejected', 'Rechazado'),
        ('cancelled', 'Cancelado'),
    ], string='Estado de aprobación', default='draft', required=True, tracking=True, index=True)
    line_ids = fields.One2many('portal.project.cost.approval.line', 'approval_id', string='Detalle de costos', readonly=True)
    currency_id = fields.Many2one('res.currency', string='Moneda', required=True, readonly=True)
    amount_before_fee = fields.Monetary(string='Costo antes de Fee', readonly=True, currency_field='currency_id')
    fee_percent = fields.Float(string='Porcentaje de Fee', readonly=True)
    fee_amount = fields.Monetary(string='Importe de Fee', readonly=True, currency_field='currency_id')
    total_amount = fields.Monetary(string='Total presentado', readonly=True, currency_field='currency_id')
    requested_by_id = fields.Many2one('res.users', string='Solicitado por', readonly=True)
    requested_date = fields.Datetime(string='Fecha de solicitud', readonly=True)
    supervisor_partner_id = fields.Many2one('res.partner', string='Aprobado por Supervisor Cliente', readonly=True)
    supervisor_approved_date = fields.Datetime(string='Fecha de aprobación del Supervisor', readonly=True)
    purchase_partner_id = fields.Many2one('res.partner', string='Aprobado por Compras Cliente', readonly=True)
    purchase_approved_date = fields.Datetime(string='Fecha de aprobación de Compras', readonly=True)
    rejected_by_id = fields.Many2one('res.partner', string='Rechazado por', readonly=True)
    rejected_date = fields.Datetime(string='Fecha de rechazo', readonly=True)
    rejection_note = fields.Text(string='Motivo del rechazo', readonly=True)
    company_id = fields.Many2one(string='Compañía', related='task_id.company_id', store=True, readonly=True)

    _sql_constraints = [
        ('task_version_unique', 'unique(task_id, version)', 'La versión del corte debe ser única por tarea.'),
    ]

    def action_submit(self):
        for approval in self:
            if not approval.line_ids:
                raise UserError(_('No se puede enviar un corte sin líneas de costo.'))
            approval.write({
                'state': 'supervisor_review',
                'requested_by_id': self.env.user.id,
                'requested_date': fields.Datetime.now(),
            })
            approval.task_id.message_post(body=_('Corte de costos %s enviado al Supervisor Cliente.') % approval.name)

    def action_portal_approve_supervisor(self, user, note=False):
        for approval in self.filtered(lambda item: item.state == 'supervisor_review'):
            approval.write({
                'state': 'purchase_review',
                'supervisor_partner_id': user.partner_id.id,
                'supervisor_approved_date': fields.Datetime.now(),
            })
            approval.message_post(body=_('Aprobado por Supervisor Cliente: %s. %s') % (user.partner_id.display_name, note or ''))
            approval.task_id.message_post(body=_('%(cut)s aprobado por Supervisor Cliente: %(partner)s.') % {
                'cut': approval.name, 'partner': user.partner_id.display_name,
            })

    def action_portal_approve_purchase(self, user, note=False):
        for approval in self.filtered(lambda item: item.state == 'purchase_review'):
            approval.write({
                'state': 'approved',
                'purchase_partner_id': user.partner_id.id,
                'purchase_approved_date': fields.Datetime.now(),
            })
            approval.message_post(body=_('Aprobación económica final por: %s. %s') % (user.partner_id.display_name, note or ''))
            approval.task_id.message_post(body=_('%(cut)s recibió la aprobación económica final de %(partner)s.') % {
                'cut': approval.name, 'partner': user.partner_id.display_name,
            })

    def action_portal_reject(self, user, note):
        if not (note or '').strip():
            raise UserError(_('El comentario de rechazo es obligatorio.'))
        for approval in self.filtered(lambda item: item.state in ('supervisor_review', 'purchase_review')):
            approval.write({
                'state': 'rejected',
                'rejected_by_id': user.partner_id.id,
                'rejected_date': fields.Datetime.now(),
                'rejection_note': note.strip(),
            })
            approval.message_post(body=_('Corte rechazado por %(partner)s: %(note)s') % {
                'partner': user.partner_id.display_name, 'note': note.strip(),
            })
            approval.task_id.message_post(body=_('%(cut)s rechazado por %(partner)s: %(note)s') % {
                'cut': approval.name, 'partner': user.partner_id.display_name, 'note': note.strip(),
            })


class PortalProjectCostApprovalLine(models.Model):
    _name = 'portal.project.cost.approval.line'
    _description = 'Línea snapshot del corte de costos'
    _order = 'category, date, id'

    approval_id = fields.Many2one('portal.project.cost.approval', string='Corte de costos', required=True, ondelete='cascade', index=True)
    category = fields.Selection([
        ('materials', 'Materiales'),
        ('labor', 'Mano de Obra'),
        ('equipment_tools', 'Equipos y Herramientas'),
        ('external_services', 'Servicios Externos'),
    ], string='Categoría PU', required=True, index=True)
    source_type = fields.Char(string='Tipo de origen', readonly=True)
    source_model = fields.Char(string='Modelo de origen', readonly=True)
    source_res_id = fields.Integer(string='ID del registro de origen', readonly=True)
    date = fields.Date(string='Fecha del costo', readonly=True)
    description = fields.Char(string='Descripción', required=True, readonly=True)
    unit = fields.Char(string='Unidad', readonly=True)
    quantity = fields.Float(string='Cantidad', readonly=True)
    unit_amount = fields.Monetary(string='Precio unitario', readonly=True, currency_field='currency_id')
    amount = fields.Monetary(string='Importe total', readonly=True, currency_field='currency_id')
    currency_id = fields.Many2one(string='Moneda', related='approval_id.currency_id', store=True, readonly=True)


class ProjectTask(models.Model):
    _inherit = 'project.task'

    cost_approval_ids = fields.One2many('portal.project.cost.approval', 'task_id', string='Cortes de costos')
    cost_approval_count = fields.Integer(string='Cantidad de cortes de costos', compute='_compute_cost_approval_count')

    def _compute_cost_approval_count(self):
        for task in self:
            task.cost_approval_count = len(task.cost_approval_ids)

    def action_create_cost_approval(self):
        self.ensure_one()
        active = self.cost_approval_ids.filtered(lambda item: item.state in ('draft', 'supervisor_review', 'purchase_review'))
        if active:
            raise UserError(_('Ya existe un corte activo para esta tarea.'))
        portal = self.env['portal.project']
        currency = portal._get_task_currency(self)
        values = portal._get_task_portal_values(self)
        rows = []
        approved_amount_by_source = {}
        approved_lines = self.cost_approval_ids.filtered(
            lambda item: item.state == 'approved'
        ).mapped('line_ids')
        for approved_line in approved_lines:
            source_key = (approved_line.source_model, approved_line.source_res_id)
            approved_amount_by_source[source_key] = (
                approved_amount_by_source.get(source_key, 0.0) + approved_line.amount
            )

        def add_line(source, source_type, description, product, quantity, unit, amount, date=False):
            source_key = (source._name, source.id)
            previously_approved = approved_amount_by_source.get(source_key, 0.0)
            amount_to_approve = amount - previously_approved
            if currency.is_zero(amount_to_approve):
                return
            quantity_to_approve = (
                quantity * amount_to_approve / amount
                if amount and quantity else quantity if not previously_approved else 0.0
            )
            category = 'labor' if source_type == 'labor' else (
                product.product_tmpl_id.portal_movement_category if product else 'materials'
            )
            category = category if category in ('materials', 'labor', 'equipment_tools', 'external_services') else 'materials'
            rows.append((0, 0, {
                'category': category, 'source_type': source_type,
                'source_model': source._name, 'source_res_id': source.id,
                'date': fields.Date.to_date(date), 'description': description or source.display_name,
                'unit': unit or '-', 'quantity': quantity_to_approve,
                'unit_amount': amount / quantity if quantity else amount_to_approve,
                'amount': amount_to_approve,
            }))

        if values['open_book_lines']:
            for line in values['open_book_lines']:
                description = line.employee_id.job_id.display_name if line.source_type == 'labor' and line.employee_id.job_id else line.description
                add_line(line, line.source_type, description, line.product_id, line.quantity,
                         line.uom_id.display_name if line.uom_id else '-', line.subtotal, line.date)
        else:
            for expense in values['approved_expenses']:
                amount = values['expense_pricelist_map'][expense.id]['sale_subtotal_converted']
                add_line(expense, 'expense', expense.name, expense.product_id, expense.quantity or 1.0,
                         expense.product_uom_id.display_name if expense.product_uom_id else '-', amount, expense.date)
            for line in values['purchase_lines_to_cost']:
                amount = values['purchase_line_pricelist_map'][line.id]['subtotal_converted']
                add_line(line, 'purchase', line.name, line.product_id, line.product_qty,
                         line.product_uom.display_name, amount, line.order_id.date_approve or line.order_id.date_order)
            for move in values['stock_moves']:
                amount = portal._get_stock_move_cost(move)
                add_line(move, 'stock', move.product_id.display_name, move.product_id, move.quantity,
                         move.product_uom.display_name, amount, move.date)
            for labor in values['labor_lines']:
                amount = values['labor_pricelist_map'][labor.id]
                description = labor.employee_id.job_id.display_name if labor.employee_id.job_id else labor.employee_id.name
                add_line(labor, 'labor', description, False, labor.regular_hours, _('Horas'), amount, labor.date)

        version = max(self.cost_approval_ids.mapped('version') or [0]) + 1
        fee_percent = self.open_book_fee_percent if 'open_book_fee_percent' in self._fields else 0.0
        amount_before_fee = sum(command[2]['amount'] for command in rows)
        if not rows:
            raise UserError(_(
                'No existen costos nuevos ni diferencias pendientes de aprobación. '
                'Los costos actuales ya fueron incluidos en cortes aprobados.'
            ))
        approval = self.env['portal.project.cost.approval'].create({
            'name': '%s / Corte %s' % (self.display_name, version),
            'task_id': self.id, 'version': version, 'currency_id': currency.id,
            'amount_before_fee': amount_before_fee, 'fee_percent': fee_percent,
            'fee_amount': amount_before_fee * fee_percent / 100.0,
            'total_amount': amount_before_fee * (1.0 + fee_percent / 100.0),
            'line_ids': rows,
        })
        approval.action_submit()
        return {
            'type': 'ir.actions.act_window', 'res_model': 'portal.project.cost.approval',
            'res_id': approval.id, 'view_mode': 'form', 'target': 'current',
        }
