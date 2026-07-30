# -*- coding: utf-8 -*-
from odoo import _, fields, models


class PortalProjectTaskApproval(models.Model):
    _name = 'portal.project.task.approval'
    _description = 'VoBo portal control de obra'
    _order = 'task_id, sequence, id'

    task_id = fields.Many2one(
        'project.task',
        string='Tarea',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sequence = fields.Integer(default=10)
    approval_role = fields.Selection(
        [
            ('client', 'Cliente'),
            ('client_supervisor', 'Supervisor Cliente'),
            ('purchases', 'Área de compras cliente'),
        ],
        string='VoBo',
        required=True,
    )
    state = fields.Selection(
        [
            ('pending', 'Pendiente'),
            ('approved', 'Aprobado'),
        ],
        string='Estado',
        required=True,
        default='pending',
    )
    partner_id = fields.Many2one('res.partner', string='Aprobado por', readonly=True)
    user_id = fields.Many2one('res.users', string='Usuario', readonly=True)
    approved_date = fields.Datetime(string='Fecha de aprobación', readonly=True)
    note = fields.Text(string='Comentario')
    company_id = fields.Many2one(
        'res.company',
        string='Compañía',
        related='task_id.company_id',
        store=True,
        readonly=True,
    )

    _sql_constraints = [
        (
            'task_role_unique',
            'unique(task_id, approval_role)',
            'Solo puede existir un VoBo por tipo en cada tarea.',
        ),
    ]

    def _get_role_label(self):
        selection = self._fields['approval_role']._description_selection(self.env)
        return dict(selection).get(self.approval_role, self.approval_role)

    def action_portal_approve(self, user, note=False):
        partner = user.partner_id
        for approval in self:
            approval.write({
                'state': 'approved',
                'partner_id': partner.id,
                'user_id': user.id,
                'approved_date': fields.Datetime.now(),
                'note': note or False,
            })
            approval.task_id.sudo().message_post(
                body=_('VoBo aprobado por %(role)s: %(partner)s') % {
                    'role': approval._get_role_label(),
                    'partner': partner.display_name,
                },
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
                author_id=partner.id,
            )
        return True
