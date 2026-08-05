# -*- coding: utf-8 -*-
import base64
import json
import datetime
import pytz
from collections import defaultdict
from urllib.parse import urlencode

from odoo import fields

from markupsafe import Markup, escape
from odoo import http, _
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager
from odoo.osv.expression import AND, OR


class PortalProjectController(CustomerPortal):

    def _prepare_portal_layout_values(self):
        values = super()._prepare_portal_layout_values()
        portal_project = request.env['portal.project']
        values['portal_project_services_only'] = bool(
            portal_project._is_portal_user()
            and portal_project._get_portal_user_map(active_only=True)
        )
        return values

    def _portal_project_to_int(self, value):
        try:
            return int(value) if value else False
        except (TypeError, ValueError):
            return False

    def _portal_project_to_float(self, value):
        try:
            return float(str(value).replace(',', '.')) if value not in (None, '') else False
        except (TypeError, ValueError):
            return False

    def _portal_project_to_percentage(self, value):
        percentage = self._portal_project_to_float(value)
        if percentage is False:
            return False
        return max(0.0, min(percentage, 100.0))

    def _portal_project_to_date(self, value):
        try:
            return fields.Date.to_date(value) if value else False
        except (TypeError, ValueError):
            return False

    def _get_user_timezone(self):
        tz_name = request.env.context.get('tz') or request.env.user.tz or 'UTC'
        try:
            return pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            return pytz.UTC

    def _local_date_to_utc(self, date_value, end_of_day=False):
        local_time = datetime.time.max if end_of_day else datetime.time.min
        local_dt = datetime.datetime.combine(date_value, local_time)
        localized_dt = self._get_user_timezone().localize(local_dt)
        return fields.Datetime.to_string(localized_dt.astimezone(pytz.UTC).replace(tzinfo=None))

    def _local_datetime_domain(self, field_name, date_from=False, date_to=False):
        domain = []
        if date_from:
            domain.append((field_name, '>=', self._local_date_to_utc(date_from)))
        if date_to:
            domain.append((field_name, '<=', self._local_date_to_utc(date_to, end_of_day=True)))
        return domain

    def _get_today_date(self):
        return fields.Date.context_today(request.env.user)

    def _portal_project_url(self, path, **params):
        clean_params = {}
        for key, value in params.items():
            if value is None or value is False or value == '':
                continue
            if isinstance(value, datetime.date):
                value = fields.Date.to_string(value)
            clean_params[key] = value
        return '%s?%s' % (path, urlencode(clean_params)) if clean_params else path

    def _get_task_list_back_url(self, **kw):
        allowed_params = (
            'sortby', 'search_in', 'search', 'sort_order', 'groupby', 'page_size',
            'date_from', 'date_to', 'supervisor_id', 'client_supervisor_id',
            'project_id', 'plant_id', 'sale_order_id', 'invoice_status', 'filter_kpi', 'view',
            'progress_from', 'progress_to',
        )
        return self._portal_project_url(
            '/my/control-obra/tareas',
            **{key: kw.get(key) for key in allowed_params},
        )

    def _get_pending_cost_approval_domain(self, task_ids=None):
        portal_project = request.env['portal.project']
        user_map = portal_project._get_portal_user_map(active_only=True)
        domain = [('id', '=', 0)]
        if user_map and user_map.portal_role == 'authorizer':
            if user_map.role == 'client_supervisor':
                domain = [('state', '=', 'supervisor_review')]
            elif user_map.role == 'purchases_user':
                domain = [('state', '=', 'purchase_review')]
        if task_ids is not None:
            domain.append(('task_id', 'in', task_ids or [0]))
        return domain

    def _get_task_navigation(self, task, **kw):
        """Return previous/next visible tasks while preserving list context."""
        portal_project = request.env['portal.project']
        Task = request.env['project.task'].sudo()
        domain = portal_project._get_portal_task_domain()

        search = kw.get('search')
        search_in = kw.get('search_in', 'content')
        if search:
            if search_in == 'project':
                domain = AND([domain, [('project_id.name', 'ilike', search)]])
            elif search_in == 'name':
                domain = AND([domain, [('name', 'ilike', search)]])
            else:
                domain = AND([domain, OR([
                    [('name', 'ilike', search)],
                    [('project_id.name', 'ilike', search)],
                    [('sale_line_id.name', 'ilike', search)],
                ])])

        date_from = self._portal_project_to_date(kw.get('date_from'))
        date_to = self._portal_project_to_date(kw.get('date_to'))
        progress_from = self._portal_project_to_percentage(kw.get('progress_from'))
        progress_to = self._portal_project_to_percentage(kw.get('progress_to'))
        if progress_from is not False and progress_to is not False and progress_from > progress_to:
            progress_from, progress_to = progress_to, progress_from
        filters = []
        for param, field_name in (
            ('supervisor_id', 'supervisor_interno'),
            ('client_supervisor_id', 'supervisor_cliente'),
            ('project_id', 'project_id'),
            ('plant_id', 'planta_trabajo'),
            ('sale_order_id', 'sale_order_id'),
        ):
            value = self._portal_project_to_int(kw.get(param))
            if value:
                filters.append((field_name, '=', value))
        if date_from:
            filters.append(('create_date', '>=', date_from))
        if date_to:
            filters.append(('create_date', '<=', date_to))
        invoice_status = kw.get('invoice_status')
        if invoice_status == 'no_sale_order':
            filters.append(('sale_order_id', '=', False))
        elif invoice_status:
            filters.append(('sale_order_id.invoice_status', '=', invoice_status))
        if progress_from is not False:
            filters.append(('progress', '>=', progress_from))
        if progress_to is not False:
            filters.append(('progress', '<=', progress_to))
        if filters:
            domain = AND([domain, filters])

        tasks = Task.search(domain)
        filter_kpi = kw.get('filter_kpi')
        if filter_kpi == 'in_progress':
            tasks = tasks.filtered(lambda item: self._get_task_state_classification(item)[0] == 'ejecucion')
        elif filter_kpi == 'on_time':
            today_date = self._get_today_date()
            tasks = tasks.filtered(lambda item: (
                self._get_task_sla_status(item, today_date)['measurable']
                and self._get_task_sla_status(item, today_date)['on_time']
            ))
        elif filter_kpi == 'hh_today':
            line_domain = [('task_id', 'in', tasks.ids)]
            if date_from:
                line_domain.append(('date', '>=', date_from))
            if date_to:
                line_domain.append(('date', '<=', date_to))
            tasks = tasks.filtered(lambda item: item.id in self._get_compensation_lines(line_domain).mapped('task_id').ids)
        elif filter_kpi == 'cost_month':
            today_date = self._get_today_date()
            month_start = datetime.date(today_date.year, today_date.month, 1)
            month_end = (
                datetime.date(today_date.year + 1, 1, 1)
                if today_date.month == 12
                else datetime.date(today_date.year, today_date.month + 1, 1)
            ) - datetime.timedelta(days=1)
            expense_task_ids = request.env['hr.expense'].sudo().search([
                ('task_id', 'in', tasks.ids),
                ('sheet_id.state', 'in', ['approve', 'post', 'done']),
                ('date', '>=', month_start), ('date', '<=', month_end),
            ]).mapped('task_id').ids
            stock_task_ids = request.env['stock.move'].sudo().search([
                ('task_id', 'in', tasks.ids), ('state', '=', 'done'),
                ('picking_type_id.code', '=', 'outgoing'),
                *self._local_datetime_domain('date', month_start, month_end),
            ]).mapped('task_id').ids
            labor_task_ids = self._get_compensation_lines([
                ('task_id', 'in', tasks.ids),
                ('date', '>=', month_start), ('date', '<=', month_end),
            ]).mapped('task_id').ids
            cost_task_ids = set(expense_task_ids + stock_task_ids + labor_task_ids)
            tasks = tasks.filtered(lambda item: item.id in cost_task_ids)

        sortby = kw.get('sortby') if kw.get('sortby') in (
            'date', 'name', 'project', 'progress', 'internal_supervisor',
            'client_supervisor', 'invoice_status',
        ) else 'date'
        sort_keys = {
            'date': lambda item: item.create_date or item.write_date,
            'name': lambda item: (item.name or '').lower(),
            'project': lambda item: (item.project_id.name or '').lower(),
            'progress': lambda item: item.progress or 0.0,
            'internal_supervisor': lambda item: (
                item.supervisor_interno.name or ''
                if 'supervisor_interno' in item._fields and item.supervisor_interno else ''
            ).lower(),
            'client_supervisor': lambda item: (
                item.supervisor_cliente.name or ''
                if 'supervisor_cliente' in item._fields and item.supervisor_cliente else ''
            ).lower(),
            'invoice_status': lambda item: (
                portal_project._selection_label(item.sale_order_id, 'invoice_status')
                if item.sale_order_id else _('Sin OS')
            ).lower(),
        }
        tasks = tasks.sorted(
            key=sort_keys[sortby],
            reverse=kw.get('sort_order', 'desc') == 'desc',
        )
        task_ids = tasks.ids
        if task.id not in task_ids:
            return {'previous_url': False, 'next_url': False, 'position': False, 'total': len(task_ids)}
        index = task_ids.index(task.id)
        context_params = {
            key: value for key, value in kw.items()
            if key in (
                'sortby', 'search_in', 'search', 'sort_order', 'groupby', 'page_size',
                'date_from', 'date_to', 'supervisor_id', 'client_supervisor_id',
                'project_id', 'plant_id', 'sale_order_id', 'invoice_status', 'filter_kpi',
                'view', 'progress_from', 'progress_to',
            )
        }
        return {
            'previous_url': self._portal_project_url(
                '/my/control-obra/%s' % task_ids[index - 1], **context_params
            ) if index else False,
            'next_url': self._portal_project_url(
                '/my/control-obra/%s' % task_ids[index + 1], **context_params
            ) if index + 1 < len(task_ids) else False,
            'position': index + 1,
            'total': len(task_ids),
        }

    def _empty_recordset(self, model_name):
        return request.env[model_name].sudo().browse()

    def _get_compensation_lines(self, domain, order='date desc, id desc', limit=None, offset=0):
        if 'compensation.line' not in request.env.registry.models:
            return self._empty_recordset('account.analytic.line')
        return request.env['compensation.line'].sudo().search(domain, order=order, limit=limit, offset=offset)

    def _get_compensation_model(self):
        if 'compensation.line' not in request.env.registry.models:
            return request.env['account.analytic.line'].sudo()
        return request.env['compensation.line'].sudo()

    def _sum_field(self, records, field_name):
        if not records or field_name not in records._fields:
            return 0.0
        return sum(records.mapped(field_name))

    def _service_sale_orders(self, service):
        if not service or 'sale_order_id' not in service._fields:
            return self._empty_recordset('sale.order')
        return service.sale_order_id

    def _service_lines(self, service):
        if not service or 'service_line_ids' not in service._fields:
            return self._empty_recordset('pending.service.line')
        return service.service_line_ids

    def _get_service_project(self, service):
        if not service:
            return False
        if 'project_id' in service._fields and service.project_id:
            return service.project_id
        supervisor = service.supervisor_id if 'supervisor_id' in service._fields else False
        if supervisor and 'proyecto_supervisor' in supervisor._fields:
            return supervisor.proyecto_supervisor
        return False

    def _get_task_pending_line(self, task, origin_service=False):
        sale_line = task.sale_line_id if 'sale_line_id' in task._fields else False
        if sale_line and 'pending_line_id' in sale_line._fields and sale_line.pending_line_id:
            return sale_line.pending_line_id
        if origin_service and 'service_line_ids' in origin_service._fields:
            return origin_service.service_line_ids.filtered(
                lambda line: 'task_id' in line._fields and line.task_id == task
            )[:1]
        if 'pending.service.line' in request.env.registry.models:
            return request.env['pending.service.line'].sudo().search([
                ('task_id', '=', task.id),
            ], limit=1)
        return self._empty_recordset('pending.service.line')

    def _get_pending_line_total(self, line, target_currency):
        if not line:
            return 0.0
        service = line.service_id
        company = (
            service.company_id
            if service and 'company_id' in service._fields and service.company_id
            else request.env.company
        )
        source_currency = company.currency_id
        return request.env['portal.project']._convert_amount(
            line.total or 0.0,
            source_currency,
            target_currency or source_currency,
            company,
            fields.Date.context_today(request.env.user),
        )

    def _get_sale_line_total(self, sale_line, target_currency):
        if not sale_line:
            return 0.0
        source_currency = sale_line.currency_id or sale_line.order_id.currency_id
        company = sale_line.company_id or sale_line.order_id.company_id or request.env.company
        return request.env['portal.project']._convert_amount(
            sale_line.price_subtotal or 0.0,
            source_currency,
            target_currency or source_currency,
            company,
            fields.Date.to_date(sale_line.order_id.date_order) or fields.Date.context_today(request.env.user),
        )

    def _get_service_task_count(self, service):
        if not service:
            return 0
        tasks = self._empty_recordset('project.task')
        if 'task_ids' in service._fields:
            tasks |= service.task_ids
        if 'service_line_ids' in service._fields and 'task_id' in service.service_line_ids._fields:
            tasks |= service.service_line_ids.mapped('task_id')
        if 'project.task' in request.env.registry.models:
            Task = request.env['project.task'].sudo()
            if 'servicio_pendiente' in Task._fields:
                tasks |= Task.search([('servicio_pendiente', '=', service.id)])
        computed_count = service.task_count if 'task_count' in service._fields else 0
        return max(len(tasks), computed_count or 0)

    def _filter_services_without_tasks(self, services):
        return services.filtered(lambda service: not self._get_service_task_count(service))

    def _service_has_cost_plan(self, service):
        return bool(
            service
            and (
                ('planned_material_ids' in service._fields and service.planned_material_ids)
                or ('planned_labor_ids' in service._fields and service.planned_labor_ids)
                or ('planned_service_ex_ids' in service._fields and service.planned_service_ex_ids)
            )
        )

    def _get_task_completion_date(self, task):
        for field_name in ('date_finished', 'date_done', 'closed_date'):
            if field_name in task._fields and task[field_name]:
                return fields.Date.to_date(task[field_name])
        return fields.Date.to_date(task.write_date)

    def _get_task_sla_status(self, task, today_date):
        deadline = fields.Date.to_date(task.date_deadline)
        if not deadline:
            return {
                'measurable': False,
                'on_time': False,
                'label': _('Sin fecha'),
                'class': 'secondary',
            }
        if task.state == '1_done':
            completion_date = self._get_task_completion_date(task) or today_date
            on_time = completion_date <= deadline
        else:
            on_time = deadline >= today_date
        return {
            'measurable': True,
            'on_time': on_time,
            'label': '100%' if on_time else '0%',
            'class': 'success' if on_time else 'danger',
        }

    def _get_portal_task_filter_domain(self, supervisor_id=False, client_supervisor_id=False,
                                       project_id=False, plant_id=False, include_dates=True,
                                       date_from=False, date_to=False):
        portal_project = request.env['portal.project']
        filter_domain = []
        if include_dates:
            if date_from:
                filter_domain.append(('create_date', '>=', date_from))
            if date_to:
                filter_domain.append(('create_date', '<=', date_to))
        if supervisor_id:
            filter_domain.append(('supervisor_interno', '=', supervisor_id))
        if client_supervisor_id:
            filter_domain.append(('supervisor_cliente', '=', client_supervisor_id))
        if project_id:
            filter_domain.append(('project_id', '=', project_id))
        if plant_id:
            filter_domain.append(('planta_trabajo', '=', plant_id))
        return filter_domain

    def _get_portal_pending_service_domain(self, supervisor_id=False, client_supervisor_id=False,
                                           project_id=False, plant_id=False, date_from=False,
                                           date_to=False):
        if 'pending.service' not in request.env.registry.models:
            return [('id', '=', 0)]

        PendingService = request.env['pending.service']
        portal_project = request.env['portal.project']
        user_map = portal_project._get_portal_user_map(active_only=False)
        if portal_project._is_portal_user() and (not user_map or not user_map.active):
            return [('id', '=', 0)]

        domain = [('active', '=', True)]

        if user_map and user_map.company_ids and 'company_id' in PendingService._fields:
            domain.append(('company_id', 'in', user_map.company_ids.ids))
        if (
            user_map
            and user_map.supervisor_interno_ids
            and 'supervisor_id' in PendingService._fields
        ):
            domain.append(('supervisor_id', 'in', user_map.supervisor_interno_ids.ids))

        def service_project_domain(project_ids):
            if not project_ids:
                return [('id', '=', 0)]
            if 'project_id' in PendingService._fields:
                return [('project_id', 'in', project_ids)]
            if (
                'supervisor_id' in PendingService._fields
                and 'proyecto_supervisor' in request.env['hr.employee']._fields
            ):
                return [('supervisor_id.proyecto_supervisor', 'in', project_ids)]
            return [('id', '=', 0)]

        def service_without_project_domain():
            if 'project_id' in PendingService._fields:
                return [('project_id', '=', False)]
            if (
                'supervisor_id' in PendingService._fields
                and 'proyecto_supervisor' in request.env['hr.employee']._fields
            ):
                return OR([
                    [('supervisor_id', '=', False)],
                    [('supervisor_id.proyecto_supervisor', '=', False)],
                ])
            return [('id', '=', 0)]

        def related_project_access_domain(fallback_domain):
            if 'project_id' in PendingService._fields:
                return portal_project._get_portal_related_project_access_domain(
                    'project_id',
                    fallback_domain,
                )
            invited_project_ids = portal_project._get_portal_invited_project_ids()
            if portal_project._is_portal_user():
                service_project_access_domain = OR([
                    service_project_domain(invited_project_ids),
                    service_without_project_domain(),
                ])
                return AND([
                    service_project_access_domain,
                    fallback_domain,
                ])
            return fallback_domain

        access_domain = []
        if user_map and user_map.role == 'internal_supervisor':
            if not user_map.supervisor_interno_id:
                return [('id', '=', 0)]
            access_domain.append(('supervisor_id', '=', user_map.supervisor_interno_id.id))
        elif user_map and user_map.role == 'client_supervisor':
            client_supervisors = portal_project._get_portal_client_supervisor_records(user_map=user_map)
            if not client_supervisors:
                return [('id', '=', 0)]
            access_domain.append(('supervisor_planta_id', 'in', client_supervisors.ids))
        else:
            commercial_partner = portal_project._get_portal_commercial_partner()
            if not commercial_partner:
                return [('id', '=', 0)]
            access_domain.append(('cliente_servicio', 'child_of', commercial_partner.id))

        if portal_project._is_portal_user():
            individual_follow_visibility = (
                user_map
                and user_map.follow_visibility_scope == 'individual'
            )
            visible_task_domain = portal_project._get_portal_task_domain()
            task_filter_domain = self._get_portal_task_filter_domain(
                supervisor_id=supervisor_id,
                client_supervisor_id=client_supervisor_id,
                project_id=project_id,
                plant_id=plant_id,
                include_dates=False,
            )
            if task_filter_domain:
                visible_task_domain = AND([visible_task_domain, task_filter_domain])
            visible_tasks = request.env['project.task'].sudo().search(visible_task_domain)
            visible_service_ids = (
                visible_tasks.mapped('servicio_pendiente').ids
                if visible_tasks and 'servicio_pendiente' in visible_tasks._fields
                else []
            )
            if 'pending.service.line' in request.env.registry.models:
                visible_service_ids += request.env['pending.service.line'].sudo().search([
                    ('task_id', 'in', visible_tasks.ids),
                ]).mapped('service_id').ids

            portal_service_access_domains = []
            visible_service_ids = list(set(visible_service_ids))
            if visible_service_ids:
                portal_service_access_domains.append([('id', 'in', visible_service_ids)])

            if individual_follow_visibility:
                if 'message_partner_ids' in PendingService._fields:
                    portal_service_access_domains.append(AND([
                        related_project_access_domain(access_domain),
                        [('message_partner_ids', 'in', [request.env.user.partner_id.id])],
                    ]))
            else:
                portal_service_access_domains.append(related_project_access_domain(access_domain))
                invited_project_ids = portal_project._get_portal_invited_project_ids()
                if invited_project_ids:
                    portal_service_access_domains.append(AND([
                        access_domain,
                        service_project_domain(invited_project_ids),
                    ]))

            portal_service_access_domain = (
                OR(portal_service_access_domains)
                if portal_service_access_domains
                else [('id', '=', 0)]
            )
            domain = AND([
                domain,
                portal_service_access_domain,
            ])
        else:
            domain = AND([domain, related_project_access_domain(access_domain)])

        if project_id:
            domain = AND([domain, service_project_domain([project_id])])
        if supervisor_id:
            domain.append(('supervisor_id', '=', supervisor_id))
        if client_supervisor_id:
            domain.append(('supervisor_planta_id', '=', client_supervisor_id))
        if plant_id:
            domain.append(('planta_centro', '=', plant_id))
        domain.extend(self._local_datetime_domain('date_start', date_from, date_to))
        return domain

    def _get_portal_planning_service_count(self, **filters):
        if 'pending.service' not in request.env.registry.models:
            return 0
        domain = AND([
            self._get_portal_pending_service_domain(**filters),
            [('state', '=', 'draft')],
        ])
        services = request.env['pending.service'].sudo().search(domain)
        return len(self._filter_services_without_tasks(services))

    def _get_portal_service_request_domain(self, supervisor_id=False, client_supervisor_id=False,
                                           project_id=False, plant_id=False,
                                           date_from=False, date_to=False):
        if 'pending.service' not in request.env.registry.models:
            return [('id', '=', 0)]
        return AND([
            self._get_portal_pending_service_domain(
                supervisor_id=supervisor_id,
                client_supervisor_id=client_supervisor_id,
                project_id=project_id,
                plant_id=plant_id,
                date_from=date_from,
                date_to=date_to,
            ),
            [('state', '=', 'draft')],
        ])

    def _get_portal_service_request_count(self, **filters):
        if 'pending.service' not in request.env.registry.models:
            return 0
        services = request.env['pending.service'].sudo().search(
            self._get_portal_service_request_domain(**filters)
        )
        return len(self._filter_services_without_tasks(services))

    def _get_portal_client_supervisor_domain(self, commercial_partner):
        if not commercial_partner:
            return [('id', '=', 0)]
        return [('cliente', 'child_of', commercial_partner.id)]

    def _get_portal_task_ids(self, task_domain):
        tasks = request.env['project.task'].sudo().search(task_domain)
        return tasks.ids

    def _get_portal_hh_line_domain(self, task_domain, date_from=False, date_to=False):
        task_ids = self._get_portal_task_ids(task_domain)
        line_domain = [('task_id', 'in', task_ids or [0])]
        if date_from:
            line_domain.append(('date', '>=', date_from))
        if date_to:
            line_domain.append(('date', '<=', date_to))
        return line_domain

    def _get_portal_hh_compensation_lines(self, task_domain, date_from=False, date_to=False, order='date desc, id desc',
                                          limit=None, offset=0):
        line_domain = self._get_portal_hh_line_domain(task_domain, date_from, date_to)
        return self._get_compensation_lines(line_domain, order=order, limit=limit, offset=offset)

    def _get_portal_hh_totals(self, line_domain):
        CompensationLine = self._get_compensation_model()
        if CompensationLine._name != 'compensation.line':
            return {'count': 0, 'regular_hours': 0.0, 'extra_hours': 0.0, 'total_cost': 0.0}
        groups = CompensationLine.read_group(
            line_domain,
            ['regular_hours:sum', 'extra_hours:sum', 'total_cost:sum'],
            [],
        )
        totals = groups[0] if groups else {}
        return {
            'count': CompensationLine.search_count(line_domain),
            'regular_hours': totals.get('regular_hours') or 0.0,
            'extra_hours': totals.get('extra_hours') or 0.0,
            'total_cost': totals.get('total_cost') or 0.0,
        }

    def _get_portal_grouped_rows(self, rows, groupby, group_title_getter):
        groups = {}
        for row in rows:
            title = group_title_getter(row)
            groups.setdefault(title, []).append(row)
        return [
            {
                'title': title,
                'rows': group_rows,
                'count': len(group_rows),
            }
            for title, group_rows in groups.items()
        ]

    def _get_portal_task_group_title(self, task, groupby):
        if groupby == 'task':
            return task.name if task else _('Sin tarea')
        if groupby == 'sale_order':
            return task.sale_order_id.name if task and task.sale_order_id else _('Sin OS')
        if groupby == 'project':
            return task.project_id.name if task and task.project_id else _('Sin proyecto')
        if groupby == 'plant':
            return (
                task.planta_trabajo.name
                if task and 'planta_trabajo' in task._fields and task.planta_trabajo
                else _('Sin planta')
            )
        if groupby == 'internal_supervisor':
            return (
                task.supervisor_interno.name
                if task and 'supervisor_interno' in task._fields and task.supervisor_interno
                else _('Sin Supervisor Ayasa')
            )
        if groupby == 'client_supervisor':
            return (
                task.supervisor_cliente.name
                if task and 'supervisor_cliente' in task._fields and task.supervisor_cliente
                else _('Sin Supervisor Cliente')
            )
        return _('Sin agrupar')

    def _get_portal_hh_hours_by_date(self, line_domain):
        CompensationLine = self._get_compensation_model()
        if CompensationLine._name != 'compensation.line':
            return {}
        groups = CompensationLine.read_group(
            line_domain,
            ['regular_hours:sum', 'extra_hours:sum'],
            ['date:day'],
            lazy=False,
        )
        hours_by_date = {}
        for group in groups:
            date_value = False
            for range_values in (group.get('__range') or {}).values():
                if range_values.get('from'):
                    date_value = fields.Date.to_date(range_values['from'][:10])
                    break
            if not date_value:
                try:
                    date_value = fields.Date.to_date(group.get('date'))
                except (TypeError, ValueError):
                    date_value = False
            if date_value:
                hours_by_date[date_value] = (group.get('regular_hours') or 0.0) + (group.get('extra_hours') or 0.0)
        return hours_by_date

    def _get_purchase_real_qty_by_line(self, purchase_lines, date_to=False):
        purchase_line_ids = purchase_lines.ids
        if not purchase_line_ids:
            return {}, {}

        move_domain = [
            ('purchase_line_id', 'in', purchase_line_ids),
            ('state', '=', 'done'),
            *self._local_datetime_domain('date', date_to=date_to),
        ]
        invoice_domain = [
            ('purchase_line_id', 'in', purchase_line_ids),
            ('move_id.state', '=', 'posted'),
        ]
        if date_to:
            invoice_domain.append(('move_id.date', '<=', date_to))

        received_qty_by_line = defaultdict(float)
        for move in request.env['stock.move'].sudo().search(move_domain):
            received_qty_by_line[move.purchase_line_id.id] += move.quantity or 0.0

        invoiced_qty_by_line = defaultdict(float)
        for invoice_line in request.env['account.move.line'].sudo().search(invoice_domain):
            invoiced_qty_by_line[invoice_line.purchase_line_id.id] += invoice_line.quantity or 0.0

        return received_qty_by_line, invoiced_qty_by_line

    def _get_purchase_pricelist_totals_by_line_state(self, purchase_lines, date_to=False, currency=None):
        portal_project = request.env['portal.project']
        real_total = 0.0
        committed_total = 0.0
        received_qty_by_line, invoiced_qty_by_line = self._get_purchase_real_qty_by_line(purchase_lines, date_to=date_to)
        for line in purchase_lines:
            received_qty = received_qty_by_line.get(line.id, 0.0)
            invoiced_qty = invoiced_qty_by_line.get(line.id, 0.0)
            real_qty = max(received_qty, invoiced_qty)
            real_qty = min(real_qty, line.product_qty)
            committed_qty = max(0.0, line.product_qty - real_qty)

            real_total += portal_project._get_purchase_line_pricelist_subtotal(line, real_qty, currency)
            committed_total += portal_project._get_purchase_line_pricelist_subtotal(line, committed_qty, currency)
        return real_total, committed_total

    def _get_expense_pricelist_total(self, expenses, currency=None):
        portal_project = request.env['portal.project']
        return sum(
            portal_project._get_expense_pricelist_amounts(expense, currency)['sale_subtotal_converted']
            for expense in expenses
        )

    def _get_portal_real_cost_rows(self, task_domain, date_from=False, date_to=False):
        portal_project = request.env['portal.project']
        tasks = request.env['project.task'].sudo().search(task_domain)
        open_book_tasks = tasks.filtered(portal_project._task_uses_open_book_costs)
        origin_tasks = tasks - open_book_tasks
        task_ids = origin_tasks.ids
        rows = []
        currency = request.env.company.currency_id

        type_labels = {
            'expense': _('Gasto'),
            'purchase': _('Compra'),
            'stock': _('Almacén'),
            'labor': _('Mano de obra'),
            'concept_impact': _('Impacto de concepto'),
        }
        for task in open_book_tasks:
            for line in portal_project._get_task_open_book_cost_lines(
                task, date_from, date_to
            ):
                rows.append({
                    'type': line.source_type,
                    'type_label': type_labels.get(line.source_type, _('Costo MOB')),
                    'date': fields.Date.to_date(line.date),
                    'task': task,
                    'description': line.description or line.display_name,
                    'amount': line.subtotal,
                    'record': line,
                })

        expenses_domain = [
            ('task_id', 'in', task_ids),
            ('sheet_id.state', 'in', ['approve', 'post', 'done']),
        ]
        if date_from:
            expenses_domain.append(('date', '>=', date_from))
        if date_to:
            expenses_domain.append(('date', '<=', date_to))
        for expense in request.env['hr.expense'].sudo().search(expenses_domain):
            rows.append({
                'type': 'expense',
                'type_label': _('Gasto'),
                'date': fields.Date.to_date(expense.date),
                'task': expense.task_id,
                'description': expense.name or expense.display_name,
                'amount': portal_project._get_expense_pricelist_amounts(expense, currency)['sale_subtotal_converted'],
                'record': expense,
            })

        stock_domain = [
            ('task_id', 'in', task_ids),
            ('state', '=', 'done'),
            *self._local_datetime_domain('date', date_from, date_to),
        ]
        stock_moves = request.env['stock.move'].sudo().search(stock_domain)
        warehouse_stock_moves = stock_moves.filtered(lambda move: not move.purchase_line_id)
        for move in warehouse_stock_moves:
            rows.append({
                'type': 'stock',
                'type_label': _('Almacén'),
                'date': fields.Date.to_date(move.date),
                'task': move.task_id,
                'description': move.product_id.display_name or move.reference or move.name,
                'amount': portal_project._get_stock_move_cost(move),
                'record': move,
            })

        purchase_domain = [
            ('task_id', 'in', task_ids),
            ('order_id.state', 'in', ('purchase', 'done')),
        ]
        if date_from:
            purchase_domain.extend(self._local_datetime_domain('order_id.date_approve', date_from=date_from))
        if date_to:
            purchase_domain.extend(self._local_datetime_domain('order_id.date_approve', date_to=date_to))
        purchase_lines = request.env['purchase.order.line'].sudo().search(purchase_domain)
        for line in purchase_lines:
            amounts = portal_project._get_purchase_line_pricelist_amounts(line, currency)
            rows.append({
                'type': 'purchase',
                'type_label': _('Compra'),
                'date': fields.Date.to_date(line.order_id.date_approve or line.order_id.date_order),
                'task': line.task_id,
                'description': line.product_id.display_name or line.name,
                'amount': amounts['subtotal_converted'],
                'record': line,
            })

        labor_domain = [('task_id', 'in', task_ids)]
        if date_from:
            labor_domain.append(('date', '>=', date_from))
        if date_to:
            labor_domain.append(('date', '<=', date_to))
        for labor in self._get_compensation_lines(labor_domain):
            rows.append({
                'type': 'labor',
                'type_label': _('Mano de obra'),
                'date': fields.Date.to_date(labor.date),
                'task': labor.task_id,
                'description': labor.employee_id.name or labor.display_name,
                'amount': portal_project._get_labor_pricelist_subtotal(labor, currency),
                'record': labor,
            })

        rows.sort(key=lambda row: (row['date'] or datetime.date.min, row['type_label'], row['description'] or ''), reverse=True)
        return rows

    def _get_task_cost_category_values(self, task, cost_rows=None):
        portal_project = request.env['portal.project']
        currency = portal_project._get_task_currency(task)
        categories = {
            'materials': {'label': _('Materiales'), 'rows': []},
            'labor': {'label': _('Mano de Obra'), 'rows': []},
            'equipment_tools': {'label': _('Equipos y Herramientas'), 'rows': []},
            'external_services': {'label': _('Servicios Externos'), 'rows': []},
        }
        if cost_rows is None:
            cost_rows = self._get_portal_real_cost_rows([('id', '=', task.id)])
        for cost_row in cost_rows:
            record = cost_row['record']
            product = record.product_id if 'product_id' in record._fields else False
            category = (
                'labor' if cost_row['type'] == 'labor'
                else product.product_tmpl_id.portal_movement_category
                if product and 'portal_movement_category' in product.product_tmpl_id._fields
                else 'materials'
            )
            category = category if category in categories else 'materials'
            quantity = 1.0
            uom = False
            unit_amount = cost_row['amount']
            if record._name == 'project.open.book.activity.line':
                quantity = record.quantity or 0.0
                uom = record.uom_id
                unit_amount = record.unit_cost or (cost_row['amount'] / quantity if quantity else cost_row['amount'])
            elif cost_row['type'] == 'expense':
                amounts = portal_project._get_expense_pricelist_amounts(record, currency)
                quantity = amounts['quantity']
                uom = record.product_uom_id if 'product_uom_id' in record._fields else False
                unit_amount = amounts['sale_unit']
            elif cost_row['type'] == 'purchase':
                amounts = portal_project._get_purchase_line_pricelist_amounts(record, currency)
                quantity = record.product_qty or 0.0
                uom = record.product_uom
                unit_amount = amounts['price_unit']
            elif cost_row['type'] == 'stock':
                quantity = record.quantity or record.product_uom_qty or 0.0
                uom = record.product_uom
                unit_amount = cost_row['amount'] / quantity if quantity else cost_row['amount']
            elif cost_row['type'] == 'labor':
                quantity = record.regular_hours or 0.0
                uom = _('Horas')
                unit_amount = cost_row['amount'] / quantity if quantity else cost_row['amount']

            description = cost_row['description']
            if cost_row['type'] == 'labor':
                employee = record.employee_id if 'employee_id' in record._fields else False
                if employee and employee.job_id:
                    description = employee.job_id.display_name

            categories[category]['rows'].append({
                'description': description,
                'unit': uom.display_name if uom and hasattr(uom, 'display_name') else (uom or '-'),
                'quantity': quantity,
                'unit_amount_text': portal_project._format_amount(unit_amount, currency),
                'amount': cost_row['amount'],
                'amount_text': portal_project._format_amount(cost_row['amount'], currency),
            })

        total = 0.0
        for key, values in categories.items():
            values['key'] = key
            values['amount'] = sum(row['amount'] for row in values['rows'])
            values['amount_text'] = portal_project._format_amount(values['amount'], currency)
            total += values['amount']
        return {
            'categories': list(categories.values()),
            'categories_by_key': categories,
            'total': total,
            'total_text': portal_project._format_amount(total, currency),
        }

    def _get_linear_planned_amount(self, amount, start_date, deadline, date_point):
        if date_point < start_date:
            return 0.0
        if date_point >= deadline:
            return amount
        total_days = (deadline - start_date).days or 1
        elapsed_days = (date_point - start_date).days
        return amount * (elapsed_days / total_days)

    def _get_service_planned_line_amount(self, line, target_currency=None):
        portal_project = request.env['portal.project']
        company = (
            line.service_id.company_id
            if 'service_id' in line._fields and line.service_id and 'company_id' in line.service_id._fields and line.service_id.company_id
            else request.env.company
        )
        price_date = (
            fields.Date.to_date(line.expected_consumption_date)
            if 'expected_consumption_date' in line._fields and line.expected_consumption_date
            else fields.Date.context_today(request.env.user)
        )
        if 'product_id' in line._fields and line.product_id:
            quantity = (
                line.qty_planned
                if 'qty_planned' in line._fields
                else (
                    line.quantity
                    if 'quantity' in line._fields
                    else 1.0
                )
            )
            return portal_project._get_product_pricelist_subtotal(
                line.product_id,
                quantity,
                target_currency=target_currency,
                company=company,
                date=price_date,
                project=(
                    line.service_id.project_id
                    if 'service_id' in line._fields and line.service_id
                    and 'project_id' in line.service_id._fields else None
                ),
                uom=line.product_uom_id if 'product_uom_id' in line._fields else None,
            )
        line_currency = line.currency_id if 'currency_id' in line._fields and line.currency_id else company.currency_id
        return portal_project._convert_amount(
            line.cost_planned if 'cost_planned' in line._fields else 0.0,
            line_currency,
            target_currency or line_currency,
            company,
            price_date,
        )

    def _get_pending_service_lines_pricelist_total(self, service, target_currency=None):
        portal_project = request.env['portal.project']
        project = self._get_service_project(service)
        company = (
            service.company_id
            if 'company_id' in service._fields and service.company_id
            else request.env.company
        )
        return sum(
            portal_project._get_product_pricelist_subtotal(
                line.product_id,
                line.quantity if 'quantity' in line._fields else 0.0,
                target_currency=target_currency,
                company=company,
                project=project,
                uom=line.product_uom_id if 'product_uom_id' in line._fields else None,
            )
            for line in self._service_lines(service)
            if 'product_id' in line._fields and line.product_id
        )

    def _get_service_planned_cost_at_date(self, service, date_point, today_date, currency=None):
        planned_line_groups = tuple(
            service[field_name]
            for field_name in ('planned_material_ids', 'planned_labor_ids', 'planned_service_ex_ids')
            if field_name in service._fields
        )
        if not any(planned_line_groups):
            return False

        planned_cost = 0.0
        undated_cost = 0.0
        for planned_lines in planned_line_groups:
            for line in planned_lines:
                line_cost = self._get_service_planned_line_amount(line, currency)
                expected_date = (
                    fields.Date.to_date(line.expected_consumption_date)
                    if 'expected_consumption_date' in line._fields
                    else False
                )
                if expected_date:
                    if expected_date <= date_point:
                        planned_cost += line_cost
                else:
                    undated_cost += line_cost

        if undated_cost:
            start_date = fields.Date.to_date(service.date_start) if 'date_start' in service._fields else False
            start_date = start_date or today_date
            deadline = fields.Date.to_date(service.date_end_plan) if 'date_end_plan' in service._fields else False
            deadline = deadline or (start_date + datetime.timedelta(days=30))
            planned_cost += self._get_linear_planned_amount(undated_cost, start_date, deadline, date_point)
        return planned_cost

    def _get_service_planned_hours_at_date(self, service, date_point, today_date):
        if 'planned_labor_ids' not in service._fields or not service.planned_labor_ids:
            return 0.0

        planned_hours = 0.0
        undated_hours = 0.0
        for line in service.planned_labor_ids:
            line_hours = line.hours_planned or 0.0
            expected_date = (
                fields.Date.to_date(line.expected_consumption_date)
                if 'expected_consumption_date' in line._fields
                else False
            )
            if expected_date:
                if expected_date <= date_point:
                    planned_hours += line_hours
            else:
                undated_hours += line_hours

        if undated_hours:
            start_date = fields.Date.to_date(service.date_start) if 'date_start' in service._fields else False
            start_date = start_date or today_date
            deadline = fields.Date.to_date(service.date_end_plan) if 'date_end_plan' in service._fields else False
            deadline = deadline or (start_date + datetime.timedelta(days=30))
            planned_hours += self._get_linear_planned_amount(undated_hours, start_date, deadline, date_point)
        return planned_hours

    def _get_daily_date_points(self, date_from=False, date_to=False, today_date=False, default_days=15):
        today_date = today_date or self._get_today_date()
        start_date = date_from or (today_date - datetime.timedelta(days=default_days))
        end_date = date_to or today_date
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        total_days = (end_date - start_date).days
        return [start_date + datetime.timedelta(days=day) for day in range(total_days + 1)]

    def _get_linear_planned_hours_on_date(self, amount, start_date, deadline, date_point):
        if date_point < start_date or date_point > deadline:
            return 0.0
        total_days = (deadline - start_date).days + 1
        return amount / (total_days or 1)

    def _get_service_planned_hours_on_date(self, service, date_point, today_date):
        if 'planned_labor_ids' not in service._fields or not service.planned_labor_ids:
            return 0.0

        planned_hours = 0.0
        undated_hours = 0.0
        for line in service.planned_labor_ids:
            line_hours = line.hours_planned or 0.0
            expected_date = (
                fields.Date.to_date(line.expected_consumption_date)
                if 'expected_consumption_date' in line._fields
                else False
            )
            if expected_date:
                if expected_date == date_point:
                    planned_hours += line_hours
            else:
                undated_hours += line_hours

        if undated_hours:
            start_date = fields.Date.to_date(service.date_start) if 'date_start' in service._fields else False
            start_date = start_date or today_date
            deadline = fields.Date.to_date(service.date_end_plan) if 'date_end_plan' in service._fields else False
            deadline = deadline or (start_date + datetime.timedelta(days=30))
            planned_hours += self._get_linear_planned_hours_on_date(undated_hours, start_date, deadline, date_point)
        return planned_hours

    def _get_task_fallback_planned_hours_on_date(self, task, date_point, today_date):
        start_date = fields.Date.to_date(task.planned_date_begin) or fields.Date.to_date(task.create_date) or today_date
        deadline = fields.Date.to_date(task.date_deadline) or (start_date + datetime.timedelta(days=30))
        task_hours = task.allocated_hours or 40.0
        return self._get_linear_planned_hours_on_date(task_hours, start_date, deadline, date_point)

    def _get_task_fallback_planned_cost_at_date(self, task, date_point, today_date):
        start_date = fields.Date.to_date(task.planned_date_begin) or fields.Date.to_date(task.create_date) or today_date
        deadline = fields.Date.to_date(task.date_deadline) or (start_date + datetime.timedelta(days=30))
        subtotal = task.price_subtotal or (task.sale_line_id.price_subtotal if task.sale_line_id else 0.0)
        return self._get_linear_planned_amount(subtotal, start_date, deadline, date_point)

    def _get_service_planning_dates(self, service):
        planning_dates = [
            fields.Date.to_date(service.date_start) if 'date_start' in service._fields else False,
            fields.Date.to_date(service.date_end_plan) if 'date_end_plan' in service._fields else False,
        ]
        for planned_lines in tuple(
            service[field_name]
            for field_name in ('planned_material_ids', 'planned_labor_ids', 'planned_service_ex_ids')
            if field_name in service._fields
        ):
            if planned_lines and 'expected_consumption_date' in planned_lines._fields:
                planning_dates.extend(fields.Date.to_date(line.expected_consumption_date) for line in planned_lines)
        return [date for date in planning_dates if date]

    def _get_dashboard_date_points(self, tasks, today_date, date_from=False, date_to=False):
        start_candidates = [date_from or (today_date - datetime.timedelta(days=15)), today_date]
        end_candidates = [date_to or today_date, today_date]

        for task in tasks:
            task_start = fields.Date.to_date(task.planned_date_begin) or fields.Date.to_date(task.create_date)
            task_deadline = fields.Date.to_date(task.date_deadline)
            if task_start and not date_from:
                start_candidates.append(task_start)
            if task_deadline and not date_to:
                end_candidates.append(task_deadline)
            if task.servicio_pendiente:
                service_dates = self._get_service_planning_dates(task.servicio_pendiente)
                if service_dates:
                    if not date_from:
                        start_candidates.append(min(service_dates))
                    if not date_to:
                        end_candidates.append(max(service_dates))

        start_date = min(start_candidates)
        end_date = max(end_candidates)
        if end_date < start_date:
            start_date, end_date = end_date, start_date

        total_days = (end_date - start_date).days or 4
        date_points = [start_date + datetime.timedelta(days=int(i * total_days / 4)) for i in range(5)]
        date_points.append(today_date)
        return sorted(set(date_points))

    def _portal_project_message_body(self, body):
        body = (body or '').strip()
        if not body:
            return False
        return Markup('<br/>').join(Markup(escape(line)) for line in body.splitlines())

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'portal_project_work_count' in counters:
            portal_project = request.env['portal.project']
            values['portal_project_work_count'] = request.env['project.task'].sudo().search_count(
                portal_project._get_portal_task_domain()
            )
        return values

    @http.route(['/my/control-obra/nuevo-servicio'], type='http', auth='user', website=True)
    def portal_nuevo_servicio(self, **kw):
        values = self._prepare_portal_layout_values()
        portal_project = request.env['portal.project']
        if not portal_project._portal_can_create_service_request():
            return request.redirect('/my/control-obra')
        commercial_partner = portal_project._get_portal_commercial_partner()
        if not commercial_partner:
            return request.redirect('/my/home')

        # Obtener disciplinas
        disciplines = request.env['license.disciplina'].sudo().search([])
        # Obtener plantas
        plants = request.env['control.planta'].sudo().search([])
        supervisors = request.env['supervisor.area'].sudo().search(
            self._get_portal_client_supervisor_domain(commercial_partner),
            order='name',
        )
        user_map = portal_project._get_portal_user_map(active_only=True)
        default_client_supervisor = request.env['supervisor.area'].sudo().browse()
        lock_client_supervisor = (
            user_map
            and user_map.role == 'client_supervisor'
            and user_map.portal_role == 'requester'
        )
        if lock_client_supervisor:
            default_client_supervisor = portal_project._get_portal_client_supervisor_records(
                user_map=user_map,
                commercial_partner=commercial_partner,
            )[:1]
            lock_client_supervisor = bool(default_client_supervisor)
            if default_client_supervisor:
                supervisors = default_client_supervisor

        values.update({
            'disciplines': disciplines,
            'plants': plants,
            'supervisors': supervisors,
            'default_client_supervisor_id': default_client_supervisor.id,
            'lock_client_supervisor': lock_client_supervisor,
            'page_name': 'portal_project_nuevo_servicio',
        })
        return request.render('portal_project.portal_nuevo_servicio_form', values)

    @http.route(['/my/control-obra/nuevo-servicio/submit'], type='http', auth='user', methods=['POST'], website=True, csrf=True)
    def portal_nuevo_servicio_submit(self, disciplina_id=None, planta_centro=None, descripcion_servicio=None, ot_number=None, date_start=None, date_end_plan=None, supervisor_planta_id=None, priority=None, natura_servicio=None, **kw):
        portal_project = request.env['portal.project']
        if not portal_project._portal_can_create_service_request():
            return request.redirect('/my/control-obra')
        commercial_partner = portal_project._get_portal_commercial_partner()
        if not commercial_partner:
            return request.redirect('/my/home')

        if not disciplina_id:
            return request.redirect('/my/control-obra/nuevo-servicio?error=missing_fields')

        submitted_ot = (ot_number or kw.get('order_number') or '').strip()
        vals = {
            'cliente_servicio': commercial_partner.id,
            'disciplina_id': int(disciplina_id),
            'descripcion_servicio': (descripcion_servicio or '').strip(),
            'ot_number': submitted_ot,
            'state': 'draft',
            'priority': str(priority) if priority in ('0', '1', '2', '3') else '0',
            'natura_servicio': natura_servicio if natura_servicio in ('programado', 'urgencia') else False,
        }

        user_map = portal_project._get_portal_user_map(active_only=True)
        if (
            user_map
            and user_map.role == 'client_supervisor'
            and user_map.portal_role == 'requester'
        ):
            default_client_supervisor = portal_project._get_portal_client_supervisor_records(
                user_map=user_map,
                commercial_partner=commercial_partner,
            )[:1]
            if default_client_supervisor:
                supervisor_planta_id = default_client_supervisor.id

        supervisor_planta_id = self._portal_project_to_int(supervisor_planta_id)
        if supervisor_planta_id:
            supervisor = request.env['supervisor.area'].sudo().search([
                ('id', '=', supervisor_planta_id),
                *self._get_portal_client_supervisor_domain(commercial_partner),
            ], limit=1)
            if supervisor:
                vals['supervisor_planta_id'] = supervisor.id
            
        if planta_centro:
            vals['planta_centro'] = int(planta_centro)

        if date_start:
            vals['date_start'] = fields.Datetime.to_datetime(date_start)
        if date_end_plan:
            vals['date_end_plan'] = fields.Datetime.to_datetime(date_end_plan)

        # Crear el servicio pendiente
        service = request.env['pending.service'].sudo().create(vals)

        # Registrar comentario en chatter
        msg = Markup(_(
            "<div style='background-color: #f4f6f9; border-left: 4px solid #007bff; padding: 10px;'>"
            "   <p><b>✨ Servicio solicitado desde el Portal de Clientes</b></p>"
            "   <p>Creado por el usuario <b>%s</b> de la cuenta <b>%s</b>.</p>"
            "</div>"
        )) % (request.env.user.name, commercial_partner.name)
        service.message_post(body=msg)
        return request.redirect('/my/control-obra?success_create=1')

    def _get_task_state_classification(self, task):
        service = task.servicio_pendiente
        state_key = 'planeacion'
        state_label = _('Planeación')
        if service:
            if service.state == 'draft':
                state_key = 'planeacion'
                state_label = _('Planeación')
            elif service.state == 'pending':
                if (service.avance_actual or 0.0) > 0.0:
                    state_key = 'ejecucion'
                    state_label = _('En Ejecución')
                else:
                    state_key = 'programadas'
                    state_label = _('Programada')
            elif service.state == 'assigned':
                service_orders = self._service_sale_orders(service)
                invoices = service_orders.mapped('invoice_ids') if service_orders else []
                has_paid_invoice = False
                for inv in invoices:
                    if inv.payment_state in ('paid', 'in_payment'):
                        if 'substate_id' in inv._fields:
                            if inv.substate_id.id == 7:
                                has_paid_invoice = True
                                break
                        else:
                            has_paid_invoice = True
                            break
                if has_paid_invoice:
                    state_key = 'cerradas'
                    state_label = _('Cerrada')
                else:
                    state_key = 'validacion'
                    state_label = _('En Validación')
            else:
                state_key = 'cerradas'
                state_label = _('Cerrada')
        else:
            stage_name = (task.stage_id.name or '').lower()
            if task.state == '1_done' or 'cerrad' in stage_name or 'hecho' in stage_name or 'listo' in stage_name:
                state_key = 'cerradas'
                state_label = _('Cerrada')
            elif task.state == '01_in_progress' or 'ejecuc' in stage_name:
                state_key = 'ejecucion'
                state_label = _('En Ejecución')
            elif 'validac' in stage_name:
                state_key = 'validacion'
                state_label = _('En Validación')
            elif task.state == '04_waiting_normal' or 'programad' in stage_name:
                state_key = 'programadas'
                state_label = _('Programada')
            else:
                state_key = 'planeacion'
                state_label = _('Planeación')
        return state_key, state_label

    def _get_board_like_color(self, actual, planned, fallback_color=False):
        actual_rounded = round(actual or 0.0)
        planned_rounded = round(planned or 0.0)
        if actual_rounded >= planned_rounded:
            return 'green'
        if planned_rounded - actual_rounded < 10.0:
            return 'amber'
        return 'red'

    def _get_board_like_service_values(self, service, today_date):
        actual = (service.avance_actual or 0.0) if 'avance_actual' in service._fields else 0.0
        planned = (service.avance_planeado or 0.0) if 'avance_planeado' in service._fields else 0.0
        end_date = fields.Date.to_date(service.date_end_plan) if 'date_end_plan' in service._fields else False
        task_count = self._get_service_task_count(service)

        if not end_date:
            color = 'no_date'
        elif actual >= 100.0:
            color = (
                service.completion_kanban_color
                if 'completion_kanban_color' in service._fields and service.completion_kanban_color
                else (service.kanban_color if 'kanban_color' in service._fields and service.kanban_color else 'green')
            )
        elif not task_count:
            color = service.kanban_color if 'kanban_color' in service._fields and service.kanban_color else self._get_board_like_color(actual, planned)
        else:
            color = self._get_board_like_color(actual, planned)

        if not end_date:
            vencimiento_label = _('Sin Planeacion')
            dias_al_vencimiento = False
        elif actual >= 100.0:
            vencimiento_label = _('Completado')
            dias_al_vencimiento = False
        else:
            dias_al_vencimiento = (end_date - today_date).days
            if dias_al_vencimiento > 0:
                vencimiento_label = _('Aun Falta: %s dias') % dias_al_vencimiento
            elif dias_al_vencimiento == 0:
                vencimiento_label = _('Hoy: 0 dias')
            else:
                vencimiento_label = _('Vencido: %s dias') % abs(dias_al_vencimiento)

        return {
            'color': color,
            'actual': actual,
            'planned': planned,
            'end_date': end_date,
            'dias_al_vencimiento': dias_al_vencimiento,
            'vencimiento_label': vencimiento_label,
        }

    def _get_portal_task_kanban_values(self, task, state_key, state_label, today_date):
        portal_project = request.env['portal.project']
        origin_service = (
            task.servicio_pendiente
            if 'servicio_pendiente' in task._fields and task.servicio_pendiente
            else False
        )
        if not origin_service and 'pending.service' in request.env.registry.models:
            origin_service = request.env['pending.service'].sudo().search([
                '|',
                ('service_line_ids.task_id', '=', task.id),
                ('avances_pend.task_id', '=', task.id),
            ], limit=1)
        start_date = fields.Date.to_date(task.planned_date_begin) or fields.Date.to_date(task.create_date) or today_date
        end_date = fields.Date.to_date(task.date_deadline)
        progress = max(0.0, min(task.progress or 0.0, 100.0))
        avance_facturado = max(0.0, min(getattr(task, 'avance_facturado', 0.0) or 0.0, 100.0))
        planned_progress = 0.0

        if end_date:
            if today_date >= end_date:
                planned_progress = 100.0
            elif today_date <= start_date:
                planned_progress = 0.0
            else:
                total_days = (end_date - start_date).days or 1
                elapsed_days = (today_date - start_date).days
                planned_progress = max(0.0, min((elapsed_days / total_days) * 100.0, 100.0))

        if not end_date:
            kanban_color = 'no_date'
            vencimiento_label = _('Sin Planeacion')
            dias_al_vencimiento = False
        elif progress >= 100.0:
            kanban_color = 'green'
            vencimiento_label = _('Completado')
            dias_al_vencimiento = False
        else:
            dias_al_vencimiento = (end_date - today_date).days
            if dias_al_vencimiento > 0:
                vencimiento_label = _('Aun Falta: %s dias') % dias_al_vencimiento
            elif dias_al_vencimiento == 0:
                vencimiento_label = _('Hoy: 0 dias')
            else:
                vencimiento_label = _('Vencido: %s dias') % abs(dias_al_vencimiento)

            progress_gap = planned_progress - progress
            if progress >= planned_progress:
                kanban_color = 'green'
            elif progress_gap < 10.0:
                kanban_color = 'amber'
            else:
                kanban_color = 'red'

        if origin_service:
            if 'date_start' in origin_service._fields:
                start_date = fields.Date.to_date(origin_service.date_start) or start_date
            if 'date_end_plan' in origin_service._fields:
                end_date = fields.Date.to_date(origin_service.date_end_plan) or end_date
            service_values = self._get_board_like_service_values(origin_service, today_date)
            progress = max(0.0, min(service_values['actual'], 100.0))
            planned_progress = max(0.0, min(service_values['planned'], 100.0))
            avance_facturado = (
                max(0.0, min(origin_service.avance_facturado or 0.0, 100.0))
                if 'avance_facturado' in origin_service._fields
                else 0.0
            )
            kanban_color = service_values['color']
            vencimiento_label = service_values['vencimiento_label']
            dias_al_vencimiento = service_values['dias_al_vencimiento']

        priority_raw = task.priority or '0'
        priority_label = _('Alta') if priority_raw == '1' else _('Normal')
        priority_class = 'danger' if priority_raw == '1' else 'dark'
        service_orders = self._service_sale_orders(origin_service)
        related_sale_order = task.sale_order_id or service_orders[:1]
        source_label = _('Con OS') if related_sale_order else _('Sin OS')
        source_key = 'sale' if related_sale_order else 'pending'
        currency = portal_project._get_task_currency(task)
        project = task.project_id
        is_priced_open_book = bool(
            project
            and 'is_open_book' in project._fields
            and project.is_open_book
            and project.open_book_pricelist_id
        )
        pending_line = self._get_task_pending_line(task, origin_service)
        if is_priced_open_book:
            total_cost = portal_project._get_task_portal_values(task)['total_cost']
            fee_percent = (
                task.open_book_activity_id.fee_percent
                if task.open_book_activity_id
                else project.open_book_fee_percent
            )
            amount = total_cost * (1.0 + (fee_percent or 0.0) / 100.0)
        elif task.sale_line_id:
            amount = self._get_sale_line_total(task.sale_line_id, currency)
        else:
            amount = self._get_pending_line_total(pending_line, currency)
        denominator = task._get_progress_denominator() if hasattr(task, '_get_progress_denominator') else 0.0
        invoice_count = len(related_sale_order.invoice_ids) if related_sale_order else 0
        invoice_label = portal_project._selection_label(related_sale_order, 'invoice_status') if related_sale_order else _('Sin OS')
        client = task.partner_id or (related_sale_order.partner_id if related_sale_order else False) or task.project_id.partner_id
        discipline = (
            origin_service.disciplina_id
            if origin_service and 'disciplina_id' in origin_service._fields and origin_service.disciplina_id
            else task.disc
        )

        return {
            'title': task.name or '',
            'href': '/my/control-obra/%s' % task.id,
            'source_key': source_key,
            'source_label': source_label,
            'state_key': state_key,
            'state_label': state_label,
            'stage_label': task.stage_id.name or state_label,
            'priority_label': priority_label,
            'priority_class': priority_class,
            'kanban_color': kanban_color,
            'vencimiento_label': vencimiento_label,
            'dias_al_vencimiento': dias_al_vencimiento,
            'is_vencido': bool(dias_al_vencimiento is not False and dias_al_vencimiento < 0),
            'start_text': fields.Date.to_string(start_date) if start_date else '-',
            'end_text': fields.Date.to_string(end_date) if end_date else False,
            'amount_text': portal_project._format_amount(amount, currency),
            'qty_total': denominator,
            'qty_done': task.quant_progress or 0.0,
            'avance_planeado': round(planned_progress),
            'avance': round(progress),
            'avance_facturado': round(avance_facturado),
            'client_name': client.name if client else '-',
            'disciplina_name': discipline.name if discipline else _('No aplica'),
            'supervisor_name': task.supervisor_interno.name if 'supervisor_interno' in task._fields and task.supervisor_interno else '-',
            'client_supervisor_name': task.supervisor_cliente.name if 'supervisor_cliente' in task._fields and task.supervisor_cliente else '-',
            'invoice_label': invoice_label,
            'invoice_count': invoice_count,
            'advance_count': len(task.sub_update_ids),
            'order_label': task.sale_order_id.name if task.sale_order_id else (related_sale_order.name if related_sale_order else source_label),
        }

    def _get_portal_pending_service_kanban_values(self, service, today_date):
        portal_project = request.env['portal.project']
        service_values = self._get_board_like_service_values(service, today_date)
        start_date = (
            fields.Date.to_date(service.date_start) if 'date_start' in service._fields else False
        ) or (
            fields.Date.to_date(service.date) if 'date' in service._fields else False
        )
        end_date = service_values['end_date']
        project = self._get_service_project(service)
        currency = (
            project.currency_id
            if project and project.currency_id
            else service.company_id.currency_id
            if 'company_id' in service._fields and service.company_id
            else request.env.company.currency_id
        )
        service_lines = self._service_lines(service)
        quantity = self._sum_field(service_lines, 'quantity')
        done_qty = self._sum_field(service_lines, 'total_avances')
        priority_raw = (service.priority or '0') if 'priority' in service._fields else '0'
        priority_label = _('Alta') if priority_raw in ('1', '2', '3') else _('Normal')
        priority_class = 'danger' if priority_raw in ('1', '2', '3') else 'dark'
        supervisor = service.supervisor_id if 'supervisor_id' in service._fields else False
        client = service.cliente_servicio if 'cliente_servicio' in service._fields else False
        discipline = service.disciplina_id if 'disciplina_id' in service._fields else False
        is_priced_open_book = bool(
            project
            and 'is_open_book' in project._fields
            and project.is_open_book
            and project.open_book_pricelist_id
        )
        if is_priced_open_book:
            base_amount = self._get_pending_service_lines_pricelist_total(service, currency)
            amount = base_amount * (1.0 + (project.open_book_fee_percent or 0.0) / 100.0)
        else:
            amount = sum(
                self._get_pending_line_total(line, currency)
                for line in service_lines
            )

        return {
            'title': (
                service.name
                or (service.ot_number if 'ot_number' in service._fields else False)
                or (service.order_number if 'order_number' in service._fields else False)
                or _('Servicio pendiente')
            ),
            'href': False,
            'source_key': 'pending',
            'source_label': _('Pendiente sin tarea'),
            'state_key': 'planeacion',
            'state_label': _('Planeacion'),
            'stage_label': _('Planeacion'),
            'priority_label': priority_label,
            'priority_class': priority_class,
            'kanban_color': service_values['color'],
            'vencimiento_label': service_values['vencimiento_label'],
            'dias_al_vencimiento': service_values['dias_al_vencimiento'],
            'is_vencido': bool(service_values['dias_al_vencimiento'] is not False and service_values['dias_al_vencimiento'] < 0),
            'start_text': fields.Date.to_string(start_date) if start_date else '-',
            'end_text': fields.Date.to_string(end_date) if end_date else False,
            'amount_text': portal_project._format_amount(amount, currency),
            'qty_total': quantity,
            'qty_done': done_qty,
            'avance_planeado': round(max(0.0, min(service_values['planned'], 100.0))),
            'avance': round(max(0.0, min(service_values['actual'], 100.0))),
            'avance_facturado': (
                round(max(0.0, min(service.avance_facturado or 0.0, 100.0)))
                if 'avance_facturado' in service._fields
                else 0
            ),
            'client_name': client.name if client else '-',
            'disciplina_name': discipline.name if discipline else _('No aplica'),
            'supervisor_name': supervisor.name if supervisor else '-',
            'client_supervisor_name': service.supervisor_planta_id.name if 'supervisor_planta_id' in service._fields and service.supervisor_planta_id else '-',
            'invoice_label': _('Sin OS'),
            'invoice_count': 0,
            'advance_count': 0,
            'order_label': (
                (service.ot_number if 'ot_number' in service._fields else False)
                or (service.order_number if 'order_number' in service._fields else False)
                or _('Sin OS')
            ),
        }

    @http.route(['/my/control-obra'], type='http', auth='user', website=True)
    def portal_my_control_obra(self, date_from=None, date_to=None, supervisor_id=None,
                               client_supervisor_id=None, project_id=None, plant_id=None,
                               **kw):
        values = self._prepare_portal_layout_values()
        portal_project = request.env['portal.project']
        Task = request.env['project.task'].sudo()
        domain = portal_project._get_portal_task_domain()
        base_domain = domain

        supervisor_id = self._portal_project_to_int(supervisor_id)
        client_supervisor_id = self._portal_project_to_int(client_supervisor_id)
        project_id = self._portal_project_to_int(project_id)
        plant_id = self._portal_project_to_int(plant_id)
        date_from = self._portal_project_to_date(date_from)
        date_to = self._portal_project_to_date(date_to)

        filter_domain = []
        if date_from:
            filter_domain.append(('create_date', '>=', date_from))
        if date_to:
            filter_domain.append(('create_date', '<=', date_to))
        if supervisor_id:
            filter_domain.append(('supervisor_interno', '=', supervisor_id))
        if client_supervisor_id:
            filter_domain.append(('supervisor_cliente', '=', client_supervisor_id))
        if project_id:
            filter_domain.append(('project_id', '=', project_id))
        if plant_id:
            filter_domain.append(('planta_trabajo', '=', plant_id))
        if filter_domain:
            domain = AND([domain, filter_domain])

        dashboard_tasks = Task.search(domain)
        pending_cost_approval_domain = self._get_pending_cost_approval_domain(dashboard_tasks.ids)
        show_cost_approval_kpi = pending_cost_approval_domain[0] != ('id', '=', 0)
        pending_cost_approval_count = request.env['portal.project.cost.approval'].sudo().search_count(
            pending_cost_approval_domain
        ) if show_cost_approval_kpi else 0

        # ----------------------------------------------------
        # DASHBOARD CALCULATIONS
        # ----------------------------------------------------
        today_date = self._get_today_date()
        start_of_month = datetime.date(today_date.year, today_date.month, 1)
        if today_date.month == 12:
            end_of_month = datetime.date(today_date.year + 1, 1, 1) - datetime.timedelta(days=1)
        else:
            end_of_month = datetime.date(today_date.year, today_date.month + 1, 1) - datetime.timedelta(days=1)

        # Pre-classify dashboard tasks to ensure 100% KPI/Chart alignment
        dashboard_task_states = {
            t.id: self._get_task_state_classification(t)[0]
            for t in dashboard_tasks
        }

        # 1. Proyectos visibles / Total de OT operativas / En Ejecución
        total_projects = len(dashboard_tasks.mapped('project_id'))
        ot_activas = len(dashboard_tasks)
        en_ejecucion = len([tid for tid, sk in dashboard_task_states.items() if sk == 'ejecucion'])

        # 2. HH Reales (total visible, constrained by date filters when present)
        hh_task_domain = portal_project._get_portal_task_domain()
        hh_filter_domain = self._get_portal_task_filter_domain(
            supervisor_id=supervisor_id,
            client_supervisor_id=client_supervisor_id,
            project_id=project_id,
            plant_id=plant_id,
            include_dates=False,
        )
        if hh_filter_domain:
            hh_task_domain = AND([hh_task_domain, hh_filter_domain])
        hh_line_domain = self._get_portal_hh_line_domain(hh_task_domain, date_from, date_to)
        hh_totals = self._get_portal_hh_totals(hh_line_domain)
        hh_reales_hoy = hh_totals['regular_hours'] + hh_totals['extra_hours']

        # 3. % Avance Promedio
        avg_progress = sum(dashboard_tasks.mapped('progress')) / len(dashboard_tasks) if dashboard_tasks else 0.0

        # 4. Costo antes de Fee (total visible, constrained by date filters when present)
        cost_task_domain = portal_project._get_portal_task_domain()
        cost_filter_domain = self._get_portal_task_filter_domain(
            supervisor_id=supervisor_id,
            client_supervisor_id=client_supervisor_id,
            project_id=project_id,
            plant_id=plant_id,
            include_dates=False,
        )
        if cost_filter_domain:
            cost_task_domain = AND([cost_task_domain, cost_filter_domain])
        cost_rows = self._get_portal_real_cost_rows(cost_task_domain, date_from, date_to)
        costo_real_mes = sum(row['amount'] for row in cost_rows)

        # 5. SLA Cumplimiento
        tasks_on_time = 0
        total_measurable_tasks = 0
        for task in dashboard_tasks:
            sla_status = self._get_task_sla_status(task, today_date)
            if not sla_status['measurable']:
                continue
            total_measurable_tasks += 1
            if sla_status['on_time']:
                tasks_on_time += 1
        sla_cumplimiento = (tasks_on_time / total_measurable_tasks * 100.0) if total_measurable_tasks else 0.0

        # 6. Chart 1: Avance de Órdenes de Trabajo por Estado
        service_request_count = self._get_portal_service_request_count(
            supervisor_id=supervisor_id,
            client_supervisor_id=client_supervisor_id,
            project_id=project_id,
            plant_id=plant_id,
            date_from=date_from,
            date_to=date_to,
        )
        planning_service_count = self._get_portal_planning_service_count(
            supervisor_id=supervisor_id,
            client_supervisor_id=client_supervisor_id,
            project_id=project_id,
            plant_id=plant_id,
            date_from=date_from,
            date_to=date_to,
        )
        chart_states = {'planeacion': planning_service_count, 'programadas': 0, 'ejecucion': 0, 'validacion': 0, 'cerradas': 0}
        for task in dashboard_tasks:
            sk = dashboard_task_states[task.id]
            if sk != 'planeacion' and sk in chart_states:
                chart_states[sk] += 1

        # 7. Chart 2: Evolución de Avance General (Line chart - 5 date points)
        date_points = self._get_daily_date_points(date_from, date_to, today_date)
        hh_date_points = date_points
        execution_tasks = dashboard_tasks.filtered(lambda t: dashboard_task_states.get(t.id) == 'ejecucion')
        avance_real_points = []
        avance_planeado_points = []
        for dp in date_points:
            dp_progresses = []
            dp_planned_progresses = []
            for task in execution_tasks:
                denominator = task._get_progress_denominator()
                task_updates = task.sub_update_ids.filtered(
                    lambda u: (fields.Date.to_date(u.date) or fields.Date.to_date(u.create_date) or today_date) <= dp
                )
                dp_qty = sum(task_updates.mapped('unit_progress'))
                prog = (dp_qty / denominator * 100.0) if denominator > 0 else 0.0
                dp_progresses.append(min(prog, 100.0))

                start_date = fields.Date.to_date(task.planned_date_begin) or fields.Date.to_date(task.create_date) or today_date
                deadline = fields.Date.to_date(task.date_deadline) or (start_date + datetime.timedelta(days=30))
                if dp < start_date:
                    planned_prog = 0.0
                elif dp >= deadline:
                    planned_prog = 100.0
                else:
                    total_days = (deadline - start_date).days or 1
                    elapsed_days = (dp - start_date).days
                    planned_prog = elapsed_days / total_days * 100.0
                dp_planned_progresses.append(planned_prog)

            avg_real = sum(dp_progresses) / len(execution_tasks) if execution_tasks else 0.0
            avg_plan = sum(dp_planned_progresses) / len(execution_tasks) if execution_tasks else 0.0
            avance_real_points.append(round(avg_real, 1))
            avance_planeado_points.append(round(avg_plan, 1))

        # 8. Chart 3: Distribución de OT por Disciplina
        discipline_counts = {}
        for task in dashboard_tasks:
            disc_name = False
            if 'servicio_pendiente' in task._fields and task.servicio_pendiente and task.servicio_pendiente.disciplina_id:
                disc_name = task.servicio_pendiente.disciplina_id.name
            elif task.disc:
                disc_name = task.disc.name
            
            disc_name = disc_name or _('Otros')
            discipline_counts[disc_name] = discipline_counts.get(disc_name, 0) + 1

        # 9. Chart 4: HH Reales vs Planeadas (daily)
        hh_planeadas_points = []
        hh_reales_points = []
        services = dashboard_tasks.mapped('servicio_pendiente')
        tasks_without_service = dashboard_tasks.filtered(lambda t: not t.servicio_pendiente)
        dashboard_hh_by_date = self._get_portal_hh_hours_by_date([
            ('task_id', 'in', dashboard_tasks.ids or [0]),
            ('date', 'in', hh_date_points),
        ])
        for dp in hh_date_points:
            reales = dashboard_hh_by_date.get(dp, 0.0)

            planned_dist = 0.0
            for service in services:
                planned_dist += self._get_service_planned_hours_on_date(service, dp, today_date)
            for task in tasks_without_service:
                planned_dist += self._get_task_fallback_planned_hours_on_date(task, dp, today_date)
                
            hh_planeadas_points.append(round(planned_dist, 1))
            hh_reales_points.append(round(reales, 1))

        # 10. Chart 5: Costo antes de Fee, comprometido abierto vs planeado
        costo_real_points = []
        costo_planeado_points = []
        costo_comprometido_points = []
        costo_expuesto_points = []
        services_with_cost_plan = dashboard_tasks.mapped('servicio_pendiente').filtered(self._service_has_cost_plan)
        tasks_without_service_cost_plan = dashboard_tasks.filtered(
            lambda t: not t.servicio_pendiente or t.servicio_pendiente not in services_with_cost_plan
        )
        for dp in date_points:
            expenses_dp = request.env['hr.expense'].sudo().search([
                ('task_id', 'in', dashboard_tasks.ids),
                ('sheet_id.state', 'in', ['approve', 'post', 'done']),
                ('date', '<=', dp)
            ])
            purchase_lines_dp = request.env['purchase.order.line'].sudo().search([
                ('task_id', 'in', dashboard_tasks.ids),
                ('order_id.state', 'in', ('purchase', 'done')),
                *self._local_datetime_domain('order_id.date_approve', date_to=dp),
            ])
            stock_moves_dp = request.env['stock.move'].sudo().search([
                ('task_id', 'in', dashboard_tasks.ids),
                ('state', '=', 'done'),
                ('picking_type_id.code', '=', 'outgoing'),
                *self._local_datetime_domain('date', date_to=dp),
            ])
            labor_lines_dp = self._get_compensation_lines([
                ('task_id', 'in', dashboard_tasks.ids),
                ('date', '<=', dp)
            ])
            
            purchase_real_cost, purchase_committed_cost = self._get_purchase_pricelist_totals_by_line_state(
                purchase_lines_dp,
                date_to=dp,
                currency=request.env.company.currency_id,
            )
            
            warehouse_stock_moves = stock_moves_dp.filtered(lambda m: not m.purchase_line_id)
            
            # Use the same source as the cost listing: MOB cut snapshots for
            # Open Book tasks and origin-record amounts for all other tasks.
            cost_dp = sum(row['amount'] for row in self._get_portal_real_cost_rows(
                [('id', 'in', dashboard_tasks.ids)], date_to=dp
            ))
            committed_cost_dp = purchase_committed_cost
            
            planned_cost_dp = 0.0
            
            # Distribute service planned cost
            for service in services_with_cost_plan:
                planned_cost_dp += self._get_service_planned_cost_at_date(
                    service,
                    dp,
                    today_date,
                    currency=request.env.company.currency_id,
                )
                    
            # Fallback for tasks that do not have service cost planning
            for task in tasks_without_service_cost_plan:
                planned_cost_dp += self._get_task_fallback_planned_cost_at_date(task, dp, today_date)
                    
            costo_real_points.append(round(cost_dp, 2))
            costo_planeado_points.append(round(planned_cost_dp, 2))
            costo_comprometido_points.append(round(committed_cost_dp, 2))
            costo_expuesto_points.append(round(cost_dp + committed_cost_dp, 2))

        # 11. Alertas de Servicios Pendientes (Reemplazo de SLA por Cliente)
        pending_domain = AND([
            self._get_portal_pending_service_domain(
                supervisor_id=supervisor_id,
                client_supervisor_id=client_supervisor_id,
                project_id=project_id,
                plant_id=plant_id,
                date_from=date_from,
                date_to=date_to,
            ),
            [('state', '!=', 'canceled')],
        ])

        pending_services = request.env['pending.service'].sudo().search(pending_domain)
        pending_services = self._filter_services_without_tasks(pending_services)
        
        pending_service_alerts = []
        for service in pending_services:
            service_values = self._get_board_like_service_values(service, today_date)
            actual = service_values['actual']
            if actual >= 100.0:
                continue

            color = service_values['color']
            if color not in ('red', 'amber'):
                continue
            
            plan = service_values['planned']
            desviacion = plan - actual
            
            end_plan = service_values['end_date']
            dias_al_vencimiento = service_values['dias_al_vencimiento']
            delay_days = (
                abs(dias_al_vencimiento)
                if dias_al_vencimiento is not False and dias_al_vencimiento < 0
                else ((service.delay_days or 0) if 'delay_days' in service._fields else 0)
            )
            
            message = ""
            if color == 'red':
                if end_plan and end_plan < today_date:
                    message = _("Fecha vencida hace %s días") % delay_days
                else:
                    message = _("Retraso crítico de %s%%") % int(round(desviacion))
            elif color == 'amber':
                message = _("Retraso leve de %s%%") % int(round(desviacion))
                
            pending_service_alerts.append({
                'id': service.id,
                'name': service.name,
                'ot_number': (service.ot_number or '') if 'ot_number' in service._fields else '',
                'color': color,
                'avance_actual': int(round(actual)),
                'avance_planeado': int(round(plan)),
                'message': message,
                'date_end_plan': end_plan,
            })
            
        # Ordenar primero rojos, luego ámbar, y dentro de ellos por desviación descendente
        pending_service_alerts.sort(key=lambda x: (x['color'] == 'red', x['avance_planeado'] - x['avance_actual']), reverse=True)

        dashboard_data = {
            'total_projects': total_projects,
            'ot_activas': ot_activas,
            'en_ejecucion': en_ejecucion,
            'service_request_count': service_request_count,
            'hh_reales_hoy': int(hh_reales_hoy),
            'avance_promedio': int(avg_progress),
            'costo_real_mes': costo_real_mes,
            'pending_cost_approval_count': pending_cost_approval_count,
            'show_cost_approval_kpi': show_cost_approval_kpi,
            'sla_cumplimiento': int(sla_cumplimiento),
            'date_labels': Markup(json.dumps([dp.strftime('%d %b') for dp in date_points])),
            'date_labels_raw': Markup(json.dumps([dp.strftime('%Y-%m-%d') for dp in date_points])),
            'today_str': Markup(json.dumps(today_date.strftime('%Y-%m-%d'))),
            'avance_real_points': Markup(json.dumps(avance_real_points)),
            'avance_planeado_points': Markup(json.dumps(avance_planeado_points)),
            'hh_date_labels': Markup(json.dumps([dp.strftime('%d %b') for dp in hh_date_points])),
            'hh_date_labels_raw': Markup(json.dumps([dp.strftime('%Y-%m-%d') for dp in hh_date_points])),
            'states_labels': Markup(json.dumps([_('Planeación'), _('Programadas'), _('En Ejecución'), _('En Validación'), _('Cerradas')])),
            'states_data': Markup(json.dumps([chart_states['planeacion'], chart_states['programadas'], chart_states['ejecucion'], chart_states['validacion'], chart_states['cerradas']])),
            'discipline_labels': Markup(json.dumps(list(discipline_counts.keys()))),
            'discipline_data': Markup(json.dumps(list(discipline_counts.values()))),
            'discipline_total': sum(discipline_counts.values()),
            'hh_planeadas_points': Markup(json.dumps(hh_planeadas_points)),
            'hh_reales_points': Markup(json.dumps(hh_reales_points)),
            'costo_real_points': Markup(json.dumps(costo_real_points)),
            'costo_planeado_points': Markup(json.dumps(costo_planeado_points)),
            'costo_comprometido_points': Markup(json.dumps(costo_comprometido_points)),
            'costo_expuesto_points': Markup(json.dumps(costo_expuesto_points)),
            'costo_real_mes_text': portal_project._format_amount(costo_real_mes, request.env.company.currency_id),
            'pending_service_alerts': pending_service_alerts
        }

        values.update({
            'dashboard_data': dashboard_data,
            'page_name': 'portal_project_work_dashboard',
            'date_from': date_from,
            'date_to': date_to,
            'selected_supervisor_id': supervisor_id,
            'selected_client_supervisor_id': client_supervisor_id,
            'selected_project_id': project_id,
            'selected_plant_id': plant_id,
            'can_create_service_request': portal_project._portal_can_create_service_request(),
            'dashboard_kpi_urls': {
                'active': self._portal_project_url(
                    '/my/control-obra/tareas',
                    date_from=date_from,
                    date_to=date_to,
                    supervisor_id=supervisor_id,
                    client_supervisor_id=client_supervisor_id,
                    project_id=project_id,
                    plant_id=plant_id,
                    filter_kpi='active',
                    view='list',
                ),
                'in_progress': self._portal_project_url(
                    '/my/control-obra/tareas',
                    date_from=date_from,
                    date_to=date_to,
                    supervisor_id=supervisor_id,
                    client_supervisor_id=client_supervisor_id,
                    project_id=project_id,
                    plant_id=plant_id,
                    filter_kpi='in_progress',
                    view='list',
                ),
                'hh_reales': self._portal_project_url(
                    '/my/control-obra/hh-reales',
                    date_from=date_from,
                    date_to=date_to,
                    supervisor_id=supervisor_id,
                    client_supervisor_id=client_supervisor_id,
                    project_id=project_id,
                    plant_id=plant_id,
                ),
                'solicitudes': self._portal_project_url(
                    '/my/control-obra/solicitudes',
                    date_from=date_from,
                    date_to=date_to,
                    supervisor_id=supervisor_id,
                    client_supervisor_id=client_supervisor_id,
                    project_id=project_id,
                    plant_id=plant_id,
                ),
                'costo_real': self._portal_project_url(
                    '/my/control-obra/costo-real/desglose',
                    date_from=date_from,
                    date_to=date_to,
                    supervisor_id=supervisor_id,
                    client_supervisor_id=client_supervisor_id,
                    project_id=project_id,
                    plant_id=plant_id,
                ),
                'cost_approvals': self._portal_project_url(
                    '/my/control-obra/aprobaciones-costos',
                    date_from=date_from,
                    date_to=date_to,
                    supervisor_id=supervisor_id,
                    client_supervisor_id=client_supervisor_id,
                    project_id=project_id,
                    plant_id=plant_id,
                ),
                'on_time': self._portal_project_url(
                    '/my/control-obra/tareas',
                    date_from=date_from,
                    date_to=date_to,
                    supervisor_id=supervisor_id,
                    client_supervisor_id=client_supervisor_id,
                    project_id=project_id,
                    plant_id=plant_id,
                    filter_kpi='on_time',
                    view='list',
                ),
            },
            **portal_project._get_portal_filter_options(base_domain),
        })
        return request.render('portal_project.portal_my_control_obra_dashboard', values)

    @http.route(['/my/control-obra/aprobaciones-costos'], type='http', auth='user', website=True)
    def portal_my_pending_cost_approvals(self, date_from=None, date_to=None,
                                         supervisor_id=None, client_supervisor_id=None,
                                         project_id=None, plant_id=None, **kw):
        portal_project = request.env['portal.project']
        task_domain = portal_project._get_portal_task_domain()
        supervisor_id = self._portal_project_to_int(supervisor_id)
        client_supervisor_id = self._portal_project_to_int(client_supervisor_id)
        project_id = self._portal_project_to_int(project_id)
        plant_id = self._portal_project_to_int(plant_id)
        date_from = self._portal_project_to_date(date_from)
        date_to = self._portal_project_to_date(date_to)
        filter_domain = self._get_portal_task_filter_domain(
            supervisor_id=supervisor_id, client_supervisor_id=client_supervisor_id,
            project_id=project_id, plant_id=plant_id,
            date_from=date_from, date_to=date_to,
        )
        if filter_domain:
            task_domain = AND([task_domain, filter_domain])
        task_ids = request.env['project.task'].sudo().search(task_domain).ids
        approvals = request.env['portal.project.cost.approval'].sudo().search(
            self._get_pending_cost_approval_domain(task_ids),
            order='requested_date asc, id asc',
        )
        today = self._get_today_date()
        approval_rows = []
        for approval in approvals:
            requested_date = fields.Date.to_date(approval.requested_date)
            approval_rows.append({
                'approval': approval,
                'state_label': dict(approval._fields['state']._description_selection(request.env)).get(approval.state),
                'total_text': portal_project._format_amount(approval.total_amount, approval.currency_id),
                'pending_days': max(0, (today - requested_date).days) if requested_date else 0,
                'url': '/my/control-obra/%s?tab=cost-approval#cost-cut-heading-%s' % (
                    approval.task_id.id, approval.id,
                ),
            })
        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'portal_project_pending_cost_approvals',
            'approval_rows': approval_rows,
        })
        return request.render('portal_project.portal_pending_cost_approvals', values)

    @http.route(['/my/control-obra/solicitudes', '/my/control-obra/solicitudes/page/<int:page>'],
                type='http', auth='user', website=True)
    def portal_my_control_obra_service_requests(self, page=1, date_from=None, date_to=None,
                                                supervisor_id=None, client_supervisor_id=None,
                                                project_id=None, plant_id=None,
                                                page_size=25, **kw):
        values = self._prepare_portal_layout_values()
        portal_project = request.env['portal.project']
        base_domain = portal_project._get_portal_task_domain()
        page_size_options = [25, 50, 75, 100]

        supervisor_id = self._portal_project_to_int(supervisor_id)
        client_supervisor_id = self._portal_project_to_int(client_supervisor_id)
        project_id = self._portal_project_to_int(project_id)
        plant_id = self._portal_project_to_int(plant_id)
        date_from = self._portal_project_to_date(date_from)
        date_to = self._portal_project_to_date(date_to)
        page_size = self._portal_project_to_int(page_size) or 25
        page_size = page_size if page_size in page_size_options else 25

        pending_service_model_exists = 'pending.service' in request.env.registry.models
        PendingService = (
            request.env['pending.service'].sudo()
            if pending_service_model_exists
            else False
        )
        domain = self._get_portal_service_request_domain(
            supervisor_id=supervisor_id,
            client_supervisor_id=client_supervisor_id,
            project_id=project_id,
            plant_id=plant_id,
            date_from=date_from,
            date_to=date_to,
        )
        all_service_requests = (
            self._filter_services_without_tasks(
                PendingService.search(domain, order='create_date desc, id desc')
            )
            if pending_service_model_exists
            else request.env['project.task'].sudo().browse()
        )
        request_count = len(all_service_requests) if pending_service_model_exists else 0
        filter_args = {
            'date_from': date_from,
            'date_to': date_to,
            'supervisor_id': supervisor_id,
            'client_supervisor_id': client_supervisor_id,
            'project_id': project_id,
            'plant_id': plant_id,
            'page_size': page_size,
        }
        pager = portal_pager(
            url='/my/control-obra/solicitudes',
            url_args=filter_args,
            total=request_count,
            page=page,
            step=page_size,
        )
        service_requests = all_service_requests[pager['offset']:pager['offset'] + page_size]
        if pending_service_model_exists and request_count and not service_requests:
            if pager['offset']:
                pager = portal_pager(
                    url='/my/control-obra/solicitudes',
                    url_args=filter_args,
                    total=request_count,
                    page=1,
                    step=page_size,
                )
            service_requests = all_service_requests[:page_size]
        page_start = pager['offset'] + 1 if request_count else 0
        page_end = min(pager['offset'] + page_size, request_count)

        values.update({
            'page_name': 'portal_project_service_requests',
            'default_url': '/my/control-obra',
            'service_requests': service_requests,
            'can_create_service_request': portal_project._portal_can_create_service_request(),
            'service_request_load_mismatch': bool(request_count and not service_requests),
            'request_count': request_count,
            'pager': pager,
            'page_start': page_start,
            'page_end': page_end,
            'page_size': page_size,
            'page_size_options': page_size_options,
            'page_size_unit': _('solicitudes'),
            'date_from': date_from,
            'date_to': date_to,
            'selected_supervisor_id': supervisor_id,
            'selected_client_supervisor_id': client_supervisor_id,
            'selected_project_id': project_id,
            'selected_plant_id': plant_id,
            **portal_project._get_portal_filter_options(base_domain),
        })
        return request.render('portal_project.portal_my_service_requests', values)

    @http.route(['/my/control-obra/hh-reales', '/my/control-obra/hh-reales/page/<int:page>'],
                type='http', auth='user', website=True)
    def portal_my_control_obra_hh_reales(self, page=1, date_from=None, date_to=None,
                                         supervisor_id=None, client_supervisor_id=None,
                                         project_id=None, plant_id=None, groupby='none', page_size=50, **kw):
        values = self._prepare_portal_layout_values()
        portal_project = request.env['portal.project']
        task_domain = portal_project._get_portal_task_domain()
        base_domain = task_domain
        page_size_options = [25, 50, 75, 100]
        groupby_options = {
            'none': _('Sin agrupar'),
            'task': _('OT'),
            'sale_order': _('OS / Orden de venta'),
            'project': _('Proyecto'),
            'plant': _('Planta'),
            'internal_supervisor': _('Supervisor Ayasa'),
            'client_supervisor': _('Supervisor Cliente'),
            'employee': _('Empleado'),
            'department': _('Departamento'),
        }

        supervisor_id = self._portal_project_to_int(supervisor_id)
        client_supervisor_id = self._portal_project_to_int(client_supervisor_id)
        project_id = self._portal_project_to_int(project_id)
        plant_id = self._portal_project_to_int(plant_id)
        groupby = groupby if groupby in groupby_options else 'none'
        date_from = self._portal_project_to_date(date_from)
        date_to = self._portal_project_to_date(date_to)
        page_size = self._portal_project_to_int(page_size) or 50
        page_size = page_size if page_size in page_size_options else 50

        filter_domain = self._get_portal_task_filter_domain(
            supervisor_id=supervisor_id,
            client_supervisor_id=client_supervisor_id,
            project_id=project_id,
            plant_id=plant_id,
            include_dates=False,
        )
        if filter_domain:
            task_domain = AND([task_domain, filter_domain])

        line_domain = self._get_portal_hh_line_domain(task_domain, date_from, date_to)
        line_totals = self._get_portal_hh_totals(line_domain)
        line_count = line_totals['count']
        total_regular_hours = line_totals['regular_hours']
        total_extra_hours = line_totals['extra_hours']
        total_hours = total_regular_hours + total_extra_hours

        filter_args = {
            'date_from': date_from,
            'date_to': date_to,
            'supervisor_id': supervisor_id,
            'client_supervisor_id': client_supervisor_id,
            'project_id': project_id,
            'plant_id': plant_id,
            'groupby': groupby,
            'page_size': page_size,
        }
        pager = portal_pager(
            url='/my/control-obra/hh-reales',
            url_args=filter_args,
            total=line_count,
            page=page,
            step=page_size,
        )

        line_limit = None if groupby != 'none' else page_size
        line_offset = 0 if groupby != 'none' else pager['offset']
        lines = self._get_compensation_lines(line_domain, limit=line_limit, offset=line_offset)
        all_line_rows = []
        for line in lines:
            task = line.task_id
            regular_hours = line.regular_hours or 0.0
            extra_hours = line.extra_hours or 0.0
            all_line_rows.append({
                'line': line,
                'task': task,
                'date': line.date,
                'employee': line.employee_id.name if 'employee_id' in line._fields and line.employee_id else '',
                'department': line.department_id.name if 'department_id' in line._fields and line.department_id else '',
                'regular_hours': regular_hours,
                'extra_hours': extra_hours,
                'total_hours': regular_hours + extra_hours,
            })
        if groupby != 'none':
            def hh_group_title(row):
                if groupby == 'employee':
                    return row['employee'] or _('Sin empleado')
                if groupby == 'department':
                    return row['department'] or _('Sin departamento')
                return self._get_portal_task_group_title(row['task'], groupby)

            all_line_rows = sorted(all_line_rows, key=lambda row: (
                hh_group_title(row).lower(),
                row['date'] or datetime.date.min,
                row['line'].id if row['line'] else 0,
            ), reverse=False)
            line_rows = all_line_rows[pager['offset']:pager['offset'] + page_size]
            grouped_line_rows = self._get_portal_grouped_rows(line_rows, groupby, hh_group_title)
        else:
            line_rows = all_line_rows
            grouped_line_rows = []

        page_start = pager['offset'] + 1 if line_count else 0
        page_end = min(pager['offset'] + page_size, line_count)

        values.update({
            'page_name': 'portal_project_hh_reales',
            'default_url': '/my/control-obra',
            'line_rows': line_rows,
            'grouped_line_rows': grouped_line_rows,
            'line_count': line_count,
            'pager': pager,
            'page_start': page_start,
            'page_end': page_end,
            'page_size': page_size,
            'page_size_options': page_size_options,
            'page_size_unit': _('registros'),
            'groupby_options': groupby_options,
            'groupby': groupby,
            'total_regular_hours': total_regular_hours,
            'total_extra_hours': total_extra_hours,
            'total_hours': total_hours,
            'date_from': date_from,
            'date_to': date_to,
            'selected_supervisor_id': supervisor_id,
            'selected_client_supervisor_id': client_supervisor_id,
            'selected_project_id': project_id,
            'selected_plant_id': plant_id,
            **portal_project._get_portal_filter_options(base_domain),
        })
        return request.render('portal_project.portal_my_work_hh_reales', values)

    @http.route(['/my/control-obra/costo-real/desglose'],
                type='http', auth='user', website=True)
    def portal_my_control_obra_cost_breakdown(self, date_from=None, date_to=None,
                                               supervisor_id=None, client_supervisor_id=None,
                                               project_id=None, plant_id=None, **kw):
        values = self._prepare_portal_layout_values()
        portal_project = request.env['portal.project']
        Task = request.env['project.task'].sudo()
        task_domain = portal_project._get_portal_task_domain()
        supervisor_id = self._portal_project_to_int(supervisor_id)
        client_supervisor_id = self._portal_project_to_int(client_supervisor_id)
        project_id = self._portal_project_to_int(project_id)
        plant_id = self._portal_project_to_int(plant_id)
        date_from = self._portal_project_to_date(date_from)
        date_to = self._portal_project_to_date(date_to)
        filter_domain = self._get_portal_task_filter_domain(
            supervisor_id=supervisor_id,
            client_supervisor_id=client_supervisor_id,
            project_id=project_id,
            plant_id=plant_id,
            include_dates=False,
        )
        if filter_domain:
            task_domain = AND([task_domain, filter_domain])
        all_rows = self._get_portal_real_cost_rows(task_domain, date_from, date_to)
        rows_by_task = {}
        for row in all_rows:
            rows_by_task.setdefault(row['task'].id, []).append(row)
        task_breakdowns = []
        for task in Task.browse(list(rows_by_task)).sorted(
            key=lambda item: (item.name or '').lower()
        ):
            breakdown = self._get_task_cost_category_values(
                task, rows_by_task.get(task.id, [])
            )
            task_breakdowns.append({
                'task': task,
                'breakdown': breakdown,
                'detail_url': self._portal_project_url(
                    '/my/control-obra/%s' % task.id,
                    date_from=date_from,
                    date_to=date_to,
                    supervisor_id=supervisor_id,
                    client_supervisor_id=client_supervisor_id,
                    project_id=project_id,
                    plant_id=plant_id,
                ),
            })
        currency = request.env.company.currency_id
        total_amount = sum(row['amount'] for row in all_rows)
        values.update({
            'page_name': 'portal_project_cost_breakdown',
            'task_breakdowns': task_breakdowns,
            'total_amount_text': portal_project._format_amount(total_amount, currency),
            'date_from': date_from,
            'date_to': date_to,
            'selected_supervisor_id': supervisor_id,
            'selected_client_supervisor_id': client_supervisor_id,
            'selected_project_id': project_id,
            'selected_plant_id': plant_id,
            **portal_project._get_portal_filter_options(portal_project._get_portal_task_domain()),
        })
        return request.render('portal_project.portal_main_cost_breakdown', values)

    @http.route(['/my/control-obra/costo-real', '/my/control-obra/costo-real/page/<int:page>'],
                type='http', auth='user', website=True)
    def portal_my_control_obra_costo_real(self, page=1, date_from=None, date_to=None,
                                          supervisor_id=None, client_supervisor_id=None,
                                          project_id=None, plant_id=None, groupby='none', page_size=50, **kw):
        values = self._prepare_portal_layout_values()
        portal_project = request.env['portal.project']
        task_domain = portal_project._get_portal_task_domain()
        base_domain = task_domain
        page_size_options = [25, 50, 75, 100]
        groupby_options = {
            'none': _('Sin agrupar'),
            'type': _('Tipo de importe'),
            'task': _('OT'),
            'sale_order': _('OS / Orden de venta'),
            'project': _('Proyecto'),
            'plant': _('Planta'),
            'internal_supervisor': _('Supervisor Ayasa'),
            'client_supervisor': _('Supervisor Cliente'),
        }

        supervisor_id = self._portal_project_to_int(supervisor_id)
        client_supervisor_id = self._portal_project_to_int(client_supervisor_id)
        project_id = self._portal_project_to_int(project_id)
        plant_id = self._portal_project_to_int(plant_id)
        groupby = groupby if groupby in groupby_options else 'none'
        date_from = self._portal_project_to_date(date_from)
        date_to = self._portal_project_to_date(date_to)
        page_size = self._portal_project_to_int(page_size) or 50
        page_size = page_size if page_size in page_size_options else 50

        filter_domain = self._get_portal_task_filter_domain(
            supervisor_id=supervisor_id,
            client_supervisor_id=client_supervisor_id,
            project_id=project_id,
            plant_id=plant_id,
            include_dates=False,
        )
        if filter_domain:
            task_domain = AND([task_domain, filter_domain])

        all_rows = self._get_portal_real_cost_rows(task_domain, date_from, date_to)
        row_count = len(all_rows)
        total_amount = sum(row['amount'] for row in all_rows)
        total_expense = sum(row['amount'] for row in all_rows if row['type'] == 'expense')
        total_purchase = sum(row['amount'] for row in all_rows if row['type'] == 'purchase')
        total_stock = sum(row['amount'] for row in all_rows if row['type'] == 'stock')
        total_labor = sum(row['amount'] for row in all_rows if row['type'] == 'labor')
        total_concept_impact = sum(
            row['amount'] for row in all_rows
            if row['type'] == 'concept_impact'
        )

        filter_args = {
            'date_from': date_from,
            'date_to': date_to,
            'supervisor_id': supervisor_id,
            'client_supervisor_id': client_supervisor_id,
            'project_id': project_id,
            'plant_id': plant_id,
            'groupby': groupby,
            'page_size': page_size,
        }
        pager = portal_pager(
            url='/my/control-obra/costo-real',
            url_args=filter_args,
            total=row_count,
            page=page,
            step=page_size,
        )
        if groupby != 'none':
            def cost_group_title(row):
                if groupby == 'type':
                    return row['type_label'] or _('Sin tipo')
                return self._get_portal_task_group_title(row['task'], groupby)

            all_rows = sorted(all_rows, key=lambda row: (
                cost_group_title(row).lower(),
                row['date'] or datetime.date.min,
                row['type_label'] or '',
                row['description'] or '',
            ), reverse=False)
            cost_rows = all_rows[pager['offset']:pager['offset'] + page_size]
            grouped_cost_rows = self._get_portal_grouped_rows(cost_rows, groupby, cost_group_title)
        else:
            cost_rows = all_rows[pager['offset']:pager['offset'] + page_size]
            grouped_cost_rows = []
        currency = request.env.company.currency_id
        page_start = pager['offset'] + 1 if row_count else 0
        page_end = min(pager['offset'] + page_size, row_count)

        values.update({
            'page_name': 'portal_project_costo_real',
            'default_url': '/my/control-obra',
            'cost_rows': cost_rows,
            'grouped_cost_rows': grouped_cost_rows,
            'row_count': row_count,
            'pager': pager,
            'page_start': page_start,
            'page_end': page_end,
            'page_size': page_size,
            'page_size_options': page_size_options,
            'page_size_unit': _('registros'),
            'groupby_options': groupby_options,
            'groupby': groupby,
            'total_amount_text': portal_project._format_amount(total_amount, currency),
            'total_expense_text': portal_project._format_amount(total_expense, currency),
            'total_purchase_text': portal_project._format_amount(total_purchase, currency),
            'total_stock_text': portal_project._format_amount(total_stock, currency),
            'total_labor_text': portal_project._format_amount(total_labor, currency),
            'total_concept_impact_text': portal_project._format_amount(
                total_concept_impact, currency
            ),
            'date_from': date_from,
            'date_to': date_to,
            'selected_supervisor_id': supervisor_id,
            'selected_client_supervisor_id': client_supervisor_id,
            'selected_project_id': project_id,
            'selected_plant_id': plant_id,
            **portal_project._get_portal_filter_options(base_domain),
        })
        return request.render('portal_project.portal_my_work_costo_real', values)

    @http.route(['/my/control-obra/tareas', '/my/control-obra/tareas/page/<int:page>'],
                type='http', auth='user', website=True)
    def portal_my_control_obra_tasks(self, page=1, sortby=None, search=None, search_in='content',
                                     date_from=None, date_to=None, supervisor_id=None,
                                     client_supervisor_id=None, project_id=None, plant_id=None,
                                     sale_order_id=None, invoice_status=None, groupby='none', sort_order='desc',
                                     page_size=25, filter_kpi=None, view=None, progress_from=None, progress_to=None, **kw):
        values = self._prepare_portal_layout_values()
        portal_project = request.env['portal.project']
        Task = request.env['project.task'].sudo()
        domain = portal_project._get_portal_task_domain()
        base_domain = domain
        page_size_options = [25, 50, 75, 100]

        searchbar_sortings = {
            'date': {'label': _('Fecha')},
            'name': {'label': _('Tarea')},
            'project': {'label': _('Proyecto')},
            'progress': {'label': _('Avance')},
            'internal_supervisor': {'label': _('Supervisor Ayasa')},
            'client_supervisor': {'label': _('Supervisor Interno')},
            'invoice_status': {'label': _('Estado de factura')},
        }
        searchbar_inputs = {
            'content': {'input': 'content', 'label': _('Buscar')},
            'name': {'input': 'name', 'label': _('Tarea')},
            'project': {'input': 'project', 'label': _('Proyecto')},
        }
        groupby_options = {
            'none': _('Sin agrupar'),
            'project': _('Proyecto'),
            'plant': _('Planta'),
            'sale_order': _('OS / Orden de venta'),
            'internal_supervisor': _('Supervisor Ayasa'),
            'client_supervisor': _('Supervisor Interno'),
            'invoice_status': _('Estado de factura'),
        }
        sortby = sortby if sortby in searchbar_sortings else 'date'
        sort_order = sort_order if sort_order in ('asc', 'desc') else 'desc'
        groupby = groupby if groupby in groupby_options else 'none'
        active_view = view if view in ('list', 'kanban', 'gantt') else 'kanban'
        build_board_views = active_view != 'list'
        page_size = self._portal_project_to_int(page_size) or 25
        page_size = page_size if page_size in page_size_options else 25
        invoice_status_values = dict(request.env['sale.order']._fields['invoice_status']._description_selection(request.env))
        invoice_status = invoice_status if invoice_status in invoice_status_values or invoice_status == 'no_sale_order' else False
        supervisor_id = self._portal_project_to_int(supervisor_id)
        client_supervisor_id = self._portal_project_to_int(client_supervisor_id)
        project_id = self._portal_project_to_int(project_id)
        plant_id = self._portal_project_to_int(plant_id)
        sale_order_id = self._portal_project_to_int(sale_order_id)
        if sale_order_id and invoice_status == 'no_sale_order':
            invoice_status = False
        date_from = self._portal_project_to_date(date_from)
        date_to = self._portal_project_to_date(date_to)
        progress_from = self._portal_project_to_percentage(progress_from)
        progress_to = self._portal_project_to_percentage(progress_to)
        if progress_from is not False and progress_to is not False and progress_from > progress_to:
            progress_from, progress_to = progress_to, progress_from
        filter_kpi = filter_kpi if filter_kpi in ('active', 'in_progress', 'hh_today', 'on_time', 'cost_month') else False

        if search:
            if search_in == 'project':
                domain = AND([domain, [('project_id.name', 'ilike', search)]])
            elif search_in == 'name':
                domain = AND([domain, [('name', 'ilike', search)]])
            else:
                domain = AND([domain, OR([
                    [('name', 'ilike', search)],
                    [('project_id.name', 'ilike', search)],
                    [('sale_line_id.name', 'ilike', search)],
                ])])

        filter_args = {
            'sortby': sortby,
            'search_in': search_in,
            'search': search,
            'sort_order': sort_order,
            'groupby': groupby,
            'page_size': page_size,
            'date_from': date_from,
            'date_to': date_to,
            'supervisor_id': supervisor_id,
            'client_supervisor_id': client_supervisor_id,
            'project_id': project_id,
            'plant_id': plant_id,
            'sale_order_id': sale_order_id,
            'invoice_status': invoice_status,
            'filter_kpi': filter_kpi,
            'view': active_view,
            'progress_from': progress_from,
            'progress_to': progress_to,
        }
        filter_domain = []
        if date_from:
            filter_domain.append(('create_date', '>=', date_from))
        if date_to:
            filter_domain.append(('create_date', '<=', date_to))
        if supervisor_id:
            filter_domain.append(('supervisor_interno', '=', supervisor_id))
        if client_supervisor_id:
            filter_domain.append(('supervisor_cliente', '=', client_supervisor_id))
        if project_id:
            filter_domain.append(('project_id', '=', project_id))
        if plant_id:
            filter_domain.append(('planta_trabajo', '=', plant_id))
        if sale_order_id:
            filter_domain.append(('sale_order_id', '=', sale_order_id))
        if invoice_status == 'no_sale_order':
            filter_domain.append(('sale_order_id', '=', False))
        elif invoice_status:
            filter_domain.append(('sale_order_id.invoice_status', '=', invoice_status))
        if progress_from is not False:
            filter_domain.append(('progress', '>=', progress_from))
        if progress_to is not False:
            filter_domain.append(('progress', '<=', progress_to))
        if filter_domain:
            domain = AND([domain, filter_domain])

        reverse_sort = sort_order == 'desc'
        db_order_map = {
            'date': 'create_date %s, write_date %s, id %s',
            'name': 'name %s, id %s',
            'progress': 'progress %s, id %s',
        }
        sort_keys = {
            'date': lambda task: task.create_date or task.write_date,
            'name': lambda task: (task.name or '').lower(),
            'project': lambda task: (task.project_id.name or '').lower(),
            'progress': lambda task: task.progress or 0.0,
            'internal_supervisor': lambda task: (
                task.supervisor_interno.name or ''
                if 'supervisor_interno' in task._fields and task.supervisor_interno
                else ''
            ).lower(),
            'client_supervisor': lambda task: (
                task.supervisor_cliente.name or ''
                if 'supervisor_cliente' in task._fields and task.supervisor_cliente
                else ''
            ).lower(),
            'invoice_status': lambda task: (
                portal_project._selection_label(task.sale_order_id, 'invoice_status')
                if task.sale_order_id else _('Sin OS')
            ).lower(),
        }
        next_sort_orders = {
            column: 'desc' if sortby == column and sort_order == 'asc' else 'asc'
            for column in searchbar_sortings
        }
        group_titles = {
            'project': lambda task: task.project_id.name or _('Sin proyecto'),
            'plant': lambda task: (
                task.planta_trabajo.name
                if 'planta_trabajo' in task._fields and task.planta_trabajo
                else _('Sin planta')
            ),
            'sale_order': lambda task: task.sale_order_id.name if task.sale_order_id else _('Sin OS'),
            'internal_supervisor': lambda task: (
                task.supervisor_interno.name
                if 'supervisor_interno' in task._fields and task.supervisor_interno
                else _('Sin Supervisor Ayasa')
            ),
            'client_supervisor': lambda task: (
                task.supervisor_cliente.name
                if 'supervisor_cliente' in task._fields and task.supervisor_cliente
                else _('Sin Supervisor Interno')
            ),
            'invoice_status': lambda task: (
                portal_project._selection_label(task.sale_order_id, 'invoice_status')
                if task.sale_order_id else _('Sin OS')
            ),
        }
        use_db_pager = active_view == 'list' and groupby == 'none' and sortby in db_order_map and not filter_kpi
        if use_db_pager:
            table_tasks = Task.browse()
            task_count = Task.search_count(domain)
        else:
            table_tasks = Task.search(domain)

            if filter_kpi:
                task_classifications = {
                    t.id: self._get_task_state_classification(t)[0]
                    for t in table_tasks
                }
                if filter_kpi == 'active':
                    pass
                elif filter_kpi == 'in_progress':
                    table_tasks = table_tasks.filtered(lambda t: task_classifications[t.id] == 'ejecucion')
                elif filter_kpi == 'hh_today':
                    comp_lines_domain = [
                        ('task_id', 'in', table_tasks.ids),
                    ]
                    if date_from:
                        comp_lines_domain.append(('date', '>=', date_from))
                    if date_to:
                        comp_lines_domain.append(('date', '<=', date_to))
                    comp_lines = self._get_compensation_lines(comp_lines_domain)
                    tasks_with_hours = comp_lines.mapped('task_id').ids
                    table_tasks = table_tasks.filtered(lambda t: t.id in tasks_with_hours)
                elif filter_kpi == 'on_time':
                    today_date = datetime.date.today()

                    def is_task_on_time(t):
                        sla_status = self._get_task_sla_status(t, today_date)
                        return sla_status['measurable'] and sla_status['on_time']

                    table_tasks = table_tasks.filtered(is_task_on_time)
                elif filter_kpi == 'cost_month':
                    today_date = datetime.date.today()
                    start_of_month = datetime.date(today_date.year, today_date.month, 1)
                    if today_date.month == 12:
                        end_of_month = datetime.date(today_date.year + 1, 1, 1) - datetime.timedelta(days=1)
                    else:
                        end_of_month = datetime.date(today_date.year, today_date.month + 1, 1) - datetime.timedelta(days=1)

                    expenses_month = request.env['hr.expense'].sudo().search([
                        ('task_id', 'in', table_tasks.ids),
                        ('sheet_id.state', 'in', ['approve', 'post', 'done']),
                        ('date', '>=', start_of_month),
                        ('date', '<=', end_of_month)
                    ])
                    stock_moves_month = request.env['stock.move'].sudo().search([
                        ('task_id', 'in', table_tasks.ids),
                        ('state', '=', 'done'),
                        ('picking_type_id.code', '=', 'outgoing'),
                        *self._local_datetime_domain('date', start_of_month, end_of_month),
                    ])
                    labor_lines_month = self._get_compensation_lines([
                        ('task_id', 'in', table_tasks.ids),
                        ('date', '>=', start_of_month),
                        ('date', '<=', end_of_month)
                    ])
                    expense_task_ids = expenses_month.mapped('task_id').ids
                    stock_task_ids = stock_moves_month.mapped('task_id').ids
                    labor_task_ids = labor_lines_month.mapped('task_id').ids
                    cost_task_ids = set(expense_task_ids + stock_task_ids + labor_task_ids)
                    table_tasks = table_tasks.filtered(lambda t: t.id in cost_task_ids)

            task_count = len(table_tasks)
        group_total_counts = {}
        grouped_task_rows = []
        kanban_stage_order = [
            ('planeacion', _('Planeacion')),
            ('programadas', _('Programadas')),
            ('ejecucion', _('En Ejecucion')),
            ('validacion', _('En Validacion')),
            ('cerradas', _('Cerrada')),
        ]
        kanban_columns = [
            {
                'key': key,
                'title': title,
                'rows': [],
                'count': 0,
            }
            for key, title in kanban_stage_order
        ]
        kanban_columns_by_key = {
            column['key']: column
            for column in kanban_columns
        }

        if groupby != 'none':
            groups = {}
            sorted_tasks = table_tasks.sorted(key=sort_keys[sortby], reverse=reverse_sort)
            for task in sorted_tasks:
                title = group_titles[groupby](task)
                groups.setdefault(title, []).append(task.id)
            group_total_counts = {
                title: len(task_ids)
                for title, task_ids in groups.items()
            }
            sorted_tasks = Task.browse([
                task_id
                for task_ids in groups.values()
                for task_id in task_ids
            ])
        elif not use_db_pager:
            sorted_tasks = table_tasks.sorted(key=sort_keys[sortby], reverse=reverse_sort)

        pager = portal_pager(
            url='/my/control-obra/tareas',
            url_args=filter_args,
            total=task_count,
            page=page,
            step=page_size,
        )
        if use_db_pager:
            order_parts = (sort_order,) * db_order_map[sortby].count('%s')
            order = db_order_map[sortby] % order_parts
            tasks = Task.search(domain, order=order, limit=page_size, offset=pager['offset'])
        else:
            tasks = sorted_tasks[pager['offset']:pager['offset'] + page_size]
        task_rows = portal_project._get_task_portal_rows(tasks)
        for row in task_rows:
            row['href'] = self._portal_project_url('/my/control-obra/%s' % row['task'].id, **filter_args)

        if groupby != 'none':
            groups = {}
            for row in task_rows:
                title = group_titles[groupby](row['task'])
                groups.setdefault(title, []).append(row)
            grouped_task_rows = [
                {
                    'title': title,
                    'rows': rows,
                    'count': len(rows),
                    'total_count': group_total_counts.get(title, len(rows)),
                }
                for title, rows in groups.items()
            ]

        today_date = datetime.date.today()
        if build_board_views:
            kanban_tasks = table_tasks.sorted(key=sort_keys[sortby], reverse=reverse_sort)
            for row in portal_project._get_task_portal_rows(kanban_tasks):
                state_key, state_label = self._get_task_state_classification(row['task'])
                row.update({
                    'state_key': state_key,
                    'state_label': state_label,
                    'kanban': self._get_portal_task_kanban_values(
                        row['task'], state_key, state_label, today_date
                    ),
                })
                if row['kanban']['href']:
                    row['kanban']['href'] = self._portal_project_url(row['kanban']['href'], **filter_args)
                column = kanban_columns_by_key.get(state_key)
                if column:
                    column['rows'].append(row)
                    column['count'] += 1

            if 'pending.service' in request.env.registry.models:
                pending_domain = AND([
                    self._get_portal_pending_service_domain(
                        supervisor_id=supervisor_id,
                        client_supervisor_id=client_supervisor_id,
                        project_id=project_id,
                        plant_id=plant_id,
                        date_from=date_from,
                        date_to=date_to,
                    ),
                    [('state', '=', 'draft')],
                ])
                pending_services = request.env['pending.service'].sudo().search(pending_domain)
                pending_services = pending_services.filtered(lambda service: not self._get_service_task_count(service))
                if sale_order_id:
                    pending_services = pending_services.browse()
                if progress_from is not False:
                    pending_services = pending_services.filtered(
                        lambda service: ('avance_actual' in service._fields) and (service.avance_actual or 0.0) >= progress_from
                    )
                if progress_to is not False:
                    pending_services = pending_services.filtered(
                        lambda service: ('avance_actual' in service._fields) and (service.avance_actual or 0.0) <= progress_to
                    )
                if project_id and 'project_id' not in request.env['pending.service']._fields:
                    pending_services = pending_services.filtered(
                        lambda service: service.supervisor_id
                        and 'proyecto_supervisor' in service.supervisor_id._fields
                        and service.supervisor_id.proyecto_supervisor
                        and service.supervisor_id.proyecto_supervisor.id == project_id
                    )
                for service in pending_services:
                    row = {
                        'task': False,
                        'state_key': 'planeacion',
                        'state_label': _('Planeacion'),
                        'kanban': self._get_portal_pending_service_kanban_values(service, today_date),
                    }
                    column = kanban_columns_by_key.get('planeacion')
                    if column:
                        column['rows'].append(row)
                        column['count'] += 1

        page_start = pager['offset'] + 1 if task_count else 0
        page_end = min(pager['offset'] + page_size, task_count)

        gantt_tasks = []
        if build_board_views:
            for task in table_tasks:
                start = fields.Date.to_date(task.planned_date_begin) or fields.Date.to_date(task.create_date) or today_date
                end = fields.Date.to_date(task.date_deadline) or (start + datetime.timedelta(days=1))
                state_key, state_label = self._get_task_state_classification(task)
                gantt_tasks.append({
                    'id': str(task.id),
                    'name': task.name or '',
                    'start': fields.Date.to_string(start),
                    'end': fields.Date.to_string(end),
                    'progress': int(round(task.progress or 0.0)),
                    'url': self._portal_project_url('/my/control-obra/%s' % task.id, **filter_args),
                    'custom_class': 'gantt-bar-%s' % state_key,
                    'status_label': state_label,
                    'state_key': state_key,
                    'supervisor_interno': task.supervisor_interno.name if 'supervisor_interno' in task._fields and task.supervisor_interno else '',
                    'supervisor_cliente': task.supervisor_cliente.name if 'supervisor_cliente' in task._fields and task.supervisor_cliente else '',
                })

        values.update({
            'gantt_tasks_json': Markup(json.dumps(gantt_tasks)),
            'task_rows': task_rows,
            'grouped_task_rows': grouped_task_rows,
            'kanban_columns': kanban_columns,
            'page_name': 'portal_project_work',
            'task': False,
            'pager': pager,
            'task_count': task_count,
            'page_start': page_start,
            'page_end': page_end,
            'page_size_unit': _('registros'),
            'default_url': '/my/control-obra',
            'searchbar_sortings': searchbar_sortings,
            'searchbar_inputs': searchbar_inputs,
            'groupby_options': groupby_options,
            'page_size_options': page_size_options,
            'sortby': sortby,
            'sort_order': sort_order,
            'groupby': groupby,
            'page_size': page_size,
            'next_sort_orders': next_sort_orders,
            'search_in': search_in,
            'search': search,
            'date_from': date_from,
            'date_to': date_to,
            'selected_supervisor_id': supervisor_id,
            'selected_client_supervisor_id': client_supervisor_id,
            'selected_project_id': project_id,
            'selected_plant_id': plant_id,
            'selected_sale_order_id': sale_order_id,
            'selected_invoice_status': invoice_status,
            'selected_progress_from': progress_from,
            'selected_progress_to': progress_to,
            'selected_progress_from_text': '' if progress_from is False else progress_from,
            'selected_progress_to_text': '' if progress_to is False else progress_to,
            'filter_kpi': filter_kpi,
            'active_view': active_view,
            **portal_project._get_portal_filter_options(base_domain),
        })
        return request.render('portal_project.portal_my_work_tasks', values)

    @http.route(['/my/control-obra/<int:task_id>'], type='http', auth='user', website=True)
    def portal_control_obra_task_page(self, task_id, **kw):
        portal_project = request.env['portal.project']
        task = portal_project._get_portal_task(task_id)
        if not task:
            return request.redirect('/my')

        # Task-specific charts calculation
        today_date = self._get_today_date()
        start_date = fields.Date.to_date(task.planned_date_begin) or fields.Date.to_date(task.create_date) or today_date
        deadline = fields.Date.to_date(task.date_deadline) or (start_date + datetime.timedelta(days=30))
        chart_start = start_date
        chart_end = deadline
        service = task.servicio_pendiente
        if service:
            service_dates = self._get_service_planning_dates(service)
            if service_dates:
                chart_start = min([chart_start, *service_dates])
                chart_end = max([chart_end, *service_dates])
        date_points = self._get_daily_date_points(chart_start, chart_end, today_date)

        avance_real_points = []
        avance_planeado_points = []
        hh_planeadas_points = []
        hh_reales_points = []
        costo_real_points = []
        costo_planeado_points = []
        costo_comprometido_points = []
        costo_expuesto_points = []
        task_hh_real_total = 0.0
        task_cost_real_total = 0.0

        for i, dp in enumerate(date_points):
            # 1. Avance
            denominator = task._get_progress_denominator()
            # Filter updates using fallback to u.create_date if u.date is not set
            task_updates = task.sub_update_ids.filtered(
                lambda u: (fields.Date.to_date(u.date) or fields.Date.to_date(u.create_date) or today_date) <= dp
            )
            dp_qty = sum(task_updates.mapped('unit_progress'))
            prog_real = (dp_qty / denominator * 100.0) if denominator > 0 else 0.0
            prog_real = min(prog_real, 100.0)

            planned_days = (deadline - start_date).days or 1
            if dp < start_date:
                prog_plan = 0.0
            elif dp >= deadline:
                prog_plan = 100.0
            else:
                elapsed_days = (dp - start_date).days
                prog_plan = elapsed_days / planned_days * 100.0

            avance_real_points.append(round(prog_real, 1))
            avance_planeado_points.append(round(prog_plan, 1))

            service = task.servicio_pendiente

            # 3. Valores (cumulative)
            expenses_dp = request.env['hr.expense'].sudo().search([
                ('task_id', '=', task.id),
                ('sheet_id.state', 'in', ['approve', 'post', 'done']),
                ('date', '<=', dp)
            ])
            purchase_lines_dp = request.env['purchase.order.line'].sudo().search([
                ('task_id', '=', task.id),
                ('order_id.state', 'in', ('purchase', 'done')),
                *self._local_datetime_domain('order_id.date_approve', date_to=dp),
            ])
            stock_moves_dp = request.env['stock.move'].sudo().search([
                ('task_id', '=', task.id),
                ('state', '=', 'done'),
                ('picking_type_id.code', '=', 'outgoing'),
                *self._local_datetime_domain('date', date_to=dp),
            ])
            labor_lines_dp = self._get_compensation_lines([
                ('task_id', '=', task.id),
                ('date', '<=', dp)
            ])
            
            purchase_real_cost, purchase_committed_cost = self._get_purchase_pricelist_totals_by_line_state(
                purchase_lines_dp,
                date_to=dp,
                currency=portal_project._get_task_currency(task),
            )
            
            warehouse_stock_moves = stock_moves_dp.filtered(lambda m: not m.purchase_line_id)
            
            cost_dp = sum(row['amount'] for row in self._get_portal_real_cost_rows(
                [('id', '=', task.id)], date_to=dp
            ))
            committed_cost_dp = purchase_committed_cost

            if self._service_has_cost_plan(service):
                planned_cost_dp = self._get_service_planned_cost_at_date(
                    service,
                    dp,
                    today_date,
                    currency=portal_project._get_task_currency(task),
                )
            else:
                planned_cost_dp = self._get_task_fallback_planned_cost_at_date(task, dp, today_date)

            costo_real_points.append(round(cost_dp, 2))
            costo_planeado_points.append(round(planned_cost_dp, 2))
            costo_comprometido_points.append(round(committed_cost_dp, 2))
            costo_expuesto_points.append(round(cost_dp + committed_cost_dp, 2))

        hh_date_points = date_points
        hh_planeadas_points = []
        hh_reales_points = []
        task_hh_by_date = self._get_portal_hh_hours_by_date([
            ('task_id', '=', task.id),
            ('date', 'in', hh_date_points),
        ])
        for dp in hh_date_points:
            reales = task_hh_by_date.get(dp, 0.0)
            if service:
                planned = self._get_service_planned_hours_on_date(service, dp, today_date)
            else:
                planned = self._get_task_fallback_planned_hours_on_date(task, dp, today_date)
            hh_planeadas_points.append(round(planned, 1))
            hh_reales_points.append(round(reales, 1))

        task_domain = [('id', '=', task.id)]
        task_hh_totals = self._get_portal_hh_totals(self._get_portal_hh_line_domain(task_domain))
        task_hh_real_total = task_hh_totals['regular_hours'] + task_hh_totals['extra_hours']
        task_cost_real_total = sum(row['amount'] for row in self._get_portal_real_cost_rows(task_domain))

        task_sla_status = self._get_task_sla_status(task, today_date)
        task_sla_ok = task_sla_status['on_time']
        task_kpis = {
            'hh_reales': round(task_hh_real_total, 1),
            'costo_real_text': portal_project._format_amount(task_cost_real_total, portal_project._get_task_currency(task)),
            'sla_cumplimiento': task_sla_status['label'],
            'sla_class': task_sla_status['class'],
            'sla_bg_style': (
                'background-color: rgba(40, 167, 69, 0.1);'
                if task_sla_status['class'] == 'success'
                else (
                    'background-color: rgba(220, 53, 69, 0.1);'
                    if task_sla_status['class'] == 'danger'
                    else 'background-color: rgba(108, 117, 125, 0.1);'
                )
            ),
        }

        single_task_dashboard_data = {
            'date_labels': Markup(json.dumps([dp.strftime('%d %b') for dp in date_points])),
            'date_labels_raw': Markup(json.dumps([dp.strftime('%Y-%m-%d') for dp in date_points])),
            'today_str': Markup(json.dumps(today_date.strftime('%Y-%m-%d'))),
            'avance_real_points': Markup(json.dumps(avance_real_points)),
            'avance_planeado_points': Markup(json.dumps(avance_planeado_points)),
            'hh_date_labels': Markup(json.dumps([dp.strftime('%d %b') for dp in hh_date_points])),
            'hh_date_labels_raw': Markup(json.dumps([dp.strftime('%Y-%m-%d') for dp in hh_date_points])),
            'hh_planeadas_points': Markup(json.dumps(hh_planeadas_points)),
            'hh_reales_points': Markup(json.dumps(hh_reales_points)),
            'costo_real_points': Markup(json.dumps(costo_real_points)),
            'costo_planeado_points': Markup(json.dumps(costo_planeado_points)),
            'costo_comprometido_points': Markup(json.dumps(costo_comprometido_points)),
            'costo_expuesto_points': Markup(json.dumps(costo_expuesto_points)),
        }

        values = self._prepare_portal_layout_values()
        task_navigation = self._get_task_navigation(task, **kw)
        task_cost_detail_url = self._portal_project_url(
            '/my/control-obra/%s/costos' % task.id, **kw
        )
        cost_approval_values = []
        user_map = portal_project._get_portal_user_map(active_only=True)
        for approval in request.env['portal.project.cost.approval'].sudo().search([
            ('task_id', '=', task.id), ('state', '!=', 'cancelled'),
        ], order='version desc'):
            category_groups = []
            for category_key, category_label in (
                ('materials', _('Materiales')),
                ('labor', _('Mano de Obra')),
                ('equipment_tools', _('Equipos y Herramientas')),
                ('external_services', _('Servicios Externos')),
            ):
                lines = approval.line_ids.filtered(lambda line: line.category == category_key)
                category_groups.append({
                    'key': category_key, 'label': category_label, 'lines': lines,
                    'amount_text': portal_project._format_amount(sum(lines.mapped('amount')), approval.currency_id),
                })
            can_supervisor = bool(
                approval.state == 'supervisor_review' and user_map
                and user_map.active and user_map.role == 'client_supervisor'
                and user_map.portal_role == 'authorizer'
            )
            can_purchase = bool(
                approval.state == 'purchase_review' and user_map
                and user_map.active and user_map.role == 'purchases_user'
                and user_map.portal_role == 'authorizer'
            )
            cost_approval_values.append({
                'approval': approval,
                'categories': category_groups,
                'can_approve': can_supervisor or can_purchase,
                'approve_url': '/my/control-obra/%s/cost-approval/%s/approve' % (task.id, approval.id),
                'reject_url': '/my/control-obra/%s/cost-approval/%s/reject' % (task.id, approval.id),
                'amount_before_fee_text': portal_project._format_amount(approval.amount_before_fee, approval.currency_id),
                'fee_amount_text': portal_project._format_amount(approval.fee_amount, approval.currency_id),
                'total_amount_text': portal_project._format_amount(approval.total_amount, approval.currency_id),
                'state_label': dict(approval._fields['state']._description_selection(request.env)).get(approval.state),
            })
        values.update({
            'task': task,
            'page_name': 'portal_project_work',
            'active_task_tab': 'cost-approval' if kw.get('tab') == 'cost-approval' else 'charts',
            'task_back_url': self._get_task_list_back_url(**kw),
            'task_navigation': task_navigation,
            'task_cost_detail_url': task_cost_detail_url,
            'cost_approval_values': cost_approval_values,
            'task_detail_sections': portal_project._get_task_detail_sections(task),
            'task_approval_values': portal_project._get_task_approval_values(task),
            'messages': portal_project._get_record_messages(task),
            'message_post_url': '/my/control-obra/%s/message' % task.id,
            'show_conversation': True,
            'single_task_dashboard_data': single_task_dashboard_data,
            'task_kpis': task_kpis,
            **portal_project._get_task_portal_values(task),
        })
        return request.render('portal_project.portal_work_task_page', values)

    @http.route('/my/control-obra/<int:task_id>/cost-approval/<int:approval_id>/<string:action>',
                type='http', auth='user', methods=['POST'], website=True, csrf=True)
    def portal_control_obra_cost_approval(self, task_id, approval_id, action, **post):
        portal_project = request.env['portal.project']
        task = portal_project._get_portal_task(task_id)
        if not task:
            return request.redirect('/my')
        approval = request.env['portal.project.cost.approval'].sudo().search([
            ('id', '=', approval_id), ('task_id', '=', task.id),
        ], limit=1)
        if not approval:
            return request.redirect('/my/control-obra/%s' % task.id)
        user_map = portal_project._get_portal_user_map(active_only=True)
        can_supervisor = bool(
            approval.state == 'supervisor_review' and user_map
            and user_map.role == 'client_supervisor' and user_map.portal_role == 'authorizer'
        )
        can_purchase = bool(
            approval.state == 'purchase_review' and user_map
            and user_map.role == 'purchases_user' and user_map.portal_role == 'authorizer'
        )
        if not (can_supervisor or can_purchase):
            return request.redirect('/my/control-obra/%s' % task.id)
        note = (post.get('note') or '').strip()
        if action == 'approve':
            if can_supervisor:
                approval.action_portal_approve_supervisor(request.env.user, note)
            else:
                approval.action_portal_approve_purchase(request.env.user, note)
        elif action == 'reject' and note:
            approval.action_portal_reject(request.env.user, note)
        return request.redirect('/my/control-obra/%s' % task.id)

    @http.route([
        '/my/control-obra/<int:task_id>/costos',
        '/my/control-obra/<int:task_id>/costos/<string:category_key>',
    ], type='http', auth='user', website=True)
    def portal_control_obra_task_costs(self, task_id, category_key=None, **kw):
        portal_project = request.env['portal.project']
        task = portal_project._get_portal_task(task_id)
        if not task:
            return request.redirect('/my')
        cost_values = self._get_task_cost_category_values(task)
        selected_category = cost_values['categories_by_key'].get(category_key) if category_key else False
        if category_key and not selected_category:
            return request.redirect('/my/control-obra/%s/costos' % task.id)
        context_params = dict(kw)
        for category in cost_values['categories']:
            category['url'] = self._portal_project_url(
                '/my/control-obra/%s/costos/%s' % (task.id, category['key']),
                **context_params
            )
        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'portal_project_task_costs',
            'task': task,
            'cost_values': cost_values,
            'selected_category': selected_category,
            'task_detail_url': self._portal_project_url(
                '/my/control-obra/%s' % task.id, **context_params
            ),
            'cost_summary_url': self._portal_project_url(
                '/my/control-obra/%s/costos' % task.id, **context_params
            ),
        })
        return request.render('portal_project.portal_task_cost_breakdown', values)

    @http.route(['/my/control-obra/<int:task_id>/message'], type='http', auth='user', methods=['POST'], website=True)
    def portal_control_obra_task_message(self, task_id, **post):
        portal_project = request.env['portal.project']
        task = portal_project._get_portal_task(task_id)
        if not task:
            return request.redirect('/my')

        body = self._portal_project_message_body(post.get('body'))
        if body:
            task.sudo().message_post(
                body=body,
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
                author_id=request.env.user.partner_id.id,
            )
        return request.redirect('/my/control-obra/%s' % task.id)

    @http.route(['/my/control-obra/<int:task_id>/approval/<string:approval_role>'],
                type='http', auth='user', methods=['POST'], website=True)
    def portal_control_obra_task_approval(self, task_id, approval_role, **post):
        portal_project = request.env['portal.project']
        task = portal_project._get_portal_task(task_id)
        if not task:
            return request.redirect('/my')
        if not portal_project._portal_can_approve_task(task, approval_role):
            return request.redirect('/my/control-obra/%s' % task.id)

        approval = portal_project._get_task_approval(task, approval_role)
        if approval and approval.state != 'approved':
            note = (post.get('note') or '').strip() or False
            approval.action_portal_approve(request.env.user, note=note)
        return request.redirect('/my/control-obra/%s' % task.id)

    @http.route(['/my/control-obra/<int:task_id>/<string:section>/<int:record_id>'],
                type='http', auth='user', website=True)
    def portal_control_obra_record_page(self, task_id, section, record_id, **kw):
        portal_project = request.env['portal.project']
        task, record = portal_project._get_portal_record(task_id, section, record_id)
        if not task:
            return request.redirect('/my')
        if not record:
            return request.redirect('/my/control-obra/%s' % task.id)

        record_templates = {
            'advance': 'portal_project.portal_advance_page',
            'expense': 'portal_project.portal_expense_page',
            'purchase-line': 'portal_project.portal_purchase_line_page',
            'labor': 'portal_project.portal_labor_page',
            'stock-move': 'portal_project.portal_stock_move_page',
        }
        values = self._prepare_portal_layout_values()
        values.update(portal_project._get_record_detail_values(task, section, record))
        values.setdefault('task_back_url', '/my/control-obra/tareas')
        return request.render(record_templates[section], values)

    @http.route(['/my/control-obra/<int:task_id>/<string:section>/<int:record_id>/message'],
                type='http', auth='user', methods=['POST'], website=True)
    def portal_control_obra_record_message(self, task_id, section, record_id, **post):
        portal_project = request.env['portal.project']
        task, record = portal_project._get_portal_record(task_id, section, record_id)
        if not task:
            return request.redirect('/my')
        if not record:
            return request.redirect('/my/control-obra/%s' % task.id)
        conversation_target = portal_project._get_record_conversation_target(section, record)
        if 'message_ids' not in conversation_target._fields:
            return request.redirect('/my/control-obra/%s/%s/%s' % (task.id, section, record.id))

        body = self._portal_project_message_body(post.get('body'))
        if body:
            conversation_target.sudo().message_post(
                body=body,
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
                author_id=request.env.user.partner_id.id,
            )
        return request.redirect('/my/control-obra/%s/%s/%s' % (task.id, section, record.id))

    @http.route(['/my/control-obra/<int:task_id>/<string:section>/<int:record_id>/attachment/<int:attachment_id>'],
                type='http', auth='user', website=True)
    def portal_control_obra_attachment(self, task_id, section, record_id, attachment_id, **kw):
        portal_project = request.env['portal.project']
        task, record, attachment = portal_project._get_portal_attachment(task_id, section, record_id, attachment_id)
        if not task:
            return request.redirect('/my')
        if not record or not attachment:
            return request.redirect('/my/control-obra/%s' % task.id)

        data = base64.b64decode(attachment.datas or b'')
        return request.make_response(
            data,
            headers=[
                ('Content-Type', attachment.mimetype or 'application/octet-stream'),
                ('Content-Disposition', 'attachment; filename="%s"' % (attachment.name or 'attachment')),
            ],
        )
