# -*- coding: utf-8 -*-
from odoo import models


class ReportPortalProjectControlObra(models.AbstractModel):
    _name = 'report.portal_project.report_portal_control_obra_task_pdf'
    _description = 'Reporte PDF Portal Control de Obra'

    def _get_report_values(self, docids, data=None):
        data = data or {}
        portal_user_id = data.get('portal_user_id')
        helper = self.env['portal.project']
        if portal_user_id:
            helper = helper.with_user(portal_user_id)

        tasks = self.env['project.task'].sudo().browse(docids).exists()
        values_by_task = {
            task.id: helper._get_task_portal_values(task)
            for task in tasks
        }
        return {
            'doc_ids': tasks.ids,
            'doc_model': 'project.task',
            'docs': tasks,
            'values_by_task': values_by_task,
        }
