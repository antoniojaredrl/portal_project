# -*- coding: utf-8 -*-
from odoo import fields, models
from odoo.osv.expression import AND, OR


class ProjectProject(models.Model):
    _inherit = 'project.project'

    privacy_visibility = fields.Selection(
        selection_add=[
            (
                'invited_internal_portal',
                'Solo usuarios invitados internos y portal',
            ),
        ],
        ondelete={'invited_internal_portal': 'set default'},
    )

    def _portal_project_invited_visibility_key(self):
        return 'invited_internal_portal'

    def _portal_project_invited_access_domain(self, user=None):
        user = user or self.env.user
        partner = user.partner_id
        if not partner:
            return [('id', '=', 0)]

        access_domains = []
        if 'message_partner_ids' in self._fields:
            access_domains.append([('message_partner_ids', 'in', [partner.id])])
        if user and 'user_id' in self._fields:
            access_domains.append([('user_id', '=', user.id)])
        if 'collaborator_ids' in self._fields:
            access_domains.append([('collaborator_ids.partner_id', '=', partner.id)])
        if 'member_ids' in self._fields:
            access_domains.append([('member_ids', 'in', [user.id])])
        if not access_domains:
            return [('id', '=', 0)]

        return AND([
            [('privacy_visibility', '=', self._portal_project_invited_visibility_key())],
            OR(access_domains),
        ])

    def _portal_project_invited_projects_for_user(self, user=None):
        return self.sudo().search(self._portal_project_invited_access_domain(user=user))
