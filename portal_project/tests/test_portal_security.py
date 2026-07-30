import base64

from odoo import Command
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestPortalProjectSecurity(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.client_a = cls.env['res.partner'].create({
            'name': 'Portal Security Client A',
            'is_company': True,
        })
        cls.client_b = cls.env['res.partner'].create({
            'name': 'Portal Security Client B',
            'is_company': True,
        })
        cls.portal_user = new_test_user(
            cls.env(context=dict(cls.env.context, no_reset_password=True)),
            login='portal_security_user',
            name='Portal Security User',
            groups='base.group_portal',
        )
        cls.portal_user.partner_id.parent_id = cls.client_a

        cls.project_a, cls.project_b = cls.env['project.project'].create([{
            'name': 'Portal Security Project A',
            'partner_id': cls.client_a.id,
            'privacy_visibility': 'invited_internal_portal',
            'is_proyecto_obra': True,
            'company_id': cls.env.company.id,
        }, {
            'name': 'Portal Security Project B',
            'partner_id': cls.client_b.id,
            'privacy_visibility': 'invited_internal_portal',
            'is_proyecto_obra': True,
            'company_id': cls.env.company.id,
        }])
        (cls.project_a | cls.project_b).message_subscribe(
            partner_ids=[cls.portal_user.partner_id.id],
        )

        cls.task_a, cls.task_b = cls.env['project.task'].create([{
            'name': 'Portal Security Task A',
            'project_id': cls.project_a.id,
            'partner_id': cls.client_a.id,
        }, {
            'name': 'Portal Security Task B',
            'project_id': cls.project_b.id,
            'partner_id': cls.client_b.id,
        }])

    def _portal_project(self):
        return self.env['portal.project'].with_user(self.portal_user)

    def _create_user_map(self, role='client_admin', portal_role='viewer'):
        return self.env['portal.project.user.map'].create({
            'partner_id': self.portal_user.partner_id.id,
            'role': role,
            'portal_role': portal_role,
            'follow_visibility_scope': 'global',
            'company_ids': [Command.link(self.env.company.id)],
        })

    def test_portal_user_without_map_cannot_access_control_obra(self):
        portal_project = self._portal_project()

        self.assertFalse(portal_project._get_portal_role())
        self.assertFalse(portal_project._get_portal_action_role())
        self.assertFalse(portal_project._get_portal_task(self.task_a.id))

    def test_inactive_map_cannot_access_control_obra(self):
        self._create_user_map().active = False

        self.assertFalse(self._portal_project()._get_portal_task(self.task_a.id))

    def test_mapped_user_only_accesses_own_client_tasks(self):
        self._create_user_map()
        portal_project = self._portal_project()

        self.assertEqual(portal_project._get_portal_task(self.task_a.id).id, self.task_a.id)
        self.assertFalse(portal_project._get_portal_task(self.task_b.id))

    def test_non_purchases_user_cannot_open_financial_record(self):
        self._create_user_map(role='client_admin')

        task, record = self._portal_project()._get_portal_record(
            self.task_a.id,
            'expense',
            999999,
        )

        self.assertEqual(task.id, self.task_a.id)
        self.assertFalse(record)

    def test_only_purchases_role_has_financial_permission(self):
        user_map = self._create_user_map(role='client_admin')
        portal_project = self._portal_project()

        self.assertFalse(portal_project._portal_can_view_financial_summary())
        user_map.role = 'purchases_user'
        self.assertTrue(portal_project._portal_can_view_financial_summary())

    def test_only_explicitly_visible_attachments_are_exposed(self):
        self._create_user_map(role='purchases_user')
        hidden_attachment, visible_attachment = self.env['ir.attachment'].create([{
            'name': 'internal.txt',
            'datas': base64.b64encode(b'internal'),
            'res_model': self.task_a._name,
            'res_id': self.task_a.id,
            'portal_project_visible': False,
        }, {
            'name': 'portal.txt',
            'datas': base64.b64encode(b'portal'),
            'res_model': self.task_a._name,
            'res_id': self.task_a.id,
            'portal_project_visible': True,
        }])

        attachments = self._portal_project()._get_record_attachments(self.task_a)

        self.assertIn(visible_attachment, attachments)
        self.assertNotIn(hidden_attachment, attachments)
