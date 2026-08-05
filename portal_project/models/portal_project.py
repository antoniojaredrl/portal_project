from odoo import _, fields
from odoo import models
from odoo.osv.expression import AND, OR
from odoo.tools.misc import formatLang, format_date, format_datetime


class PortalProject(models.AbstractModel):
    _name = 'portal.project'
    _description = 'Portal Control de Obra Helpers'

    def _get_portal_commercial_partner(self):
        partner = self.env.user.partner_id
        return partner.commercial_partner_id if partner else self.env['res.partner']

    def _get_portal_client_ids(self):
        commercial_partner = self._get_portal_commercial_partner()
        if not commercial_partner:
            return []
        return self.env['res.partner'].sudo().search([
            ('id', 'child_of', commercial_partner.id),
        ]).ids

    def _get_portal_user_map(self, active_only=True):
        partner = self.env.user.partner_id
        if not partner:
            return self.env['portal.project.user.map']
        domain = [
            ('partner_id', '=', partner.id),
        ]
        if active_only:
            domain.append(('active', '=', True))
        UserMap = self.env['portal.project.user.map'].sudo()
        if not active_only:
            UserMap = UserMap.with_context(active_test=False)
        return UserMap.search(domain, limit=1)

    def _get_portal_company_domain(self, user_map):
        if user_map and user_map.company_ids:
            company_ids = user_map.company_ids.ids
            return OR([
                [('company_id', 'in', company_ids)],
                [('project_id.company_id', 'in', company_ids)],
                [('sale_order_id.company_id', 'in', company_ids)],
                [('sale_line_id.company_id', 'in', company_ids)],
            ])
        return []

    def _get_portal_ayasa_supervisor_domain(self, user_map):
        if user_map and user_map.supervisor_interno_ids:
            return [('supervisor_interno', 'in', user_map.supervisor_interno_ids.ids)]
        return []

    def _get_portal_client_supervisor_records(self, user_map=None, commercial_partner=None):
        partner = user_map.partner_id if user_map else self.env.user.partner_id
        if not partner:
            return self.env['supervisor.area']
        commercial_partner = commercial_partner or partner.commercial_partner_id
        if not commercial_partner:
            return self.env['supervisor.area']

        SupervisorArea = self.env['supervisor.area'].sudo()
        base_domain = [('cliente', 'child_of', commercial_partner.id)]
        names = {partner.name, partner.display_name}
        names = [name.strip() for name in names if name and name.strip()]
        if not names:
            return SupervisorArea

        exact_domain = OR([[('name', '=ilike', name)] for name in names])
        supervisors = SupervisorArea.search(AND([base_domain, exact_domain]))
        if supervisors:
            return supervisors

        partial_domain = OR([[('name', 'ilike', name)] for name in names])
        return SupervisorArea.search(AND([base_domain, partial_domain]))

    def _get_portal_role(self):
        user_map = self._get_portal_user_map(active_only=False)
        return user_map.role if user_map and user_map.active else False

    def _get_portal_action_role(self):
        user_map = self._get_portal_user_map(active_only=False)
        return user_map.portal_role if user_map and user_map.active else False

    def _portal_can_create_service_request(self):
        return self._get_portal_action_role() == 'requester'

    def _portal_can_authorize(self):
        return self._get_portal_action_role() == 'authorizer'

    def _portal_can_view_financial_summary(self):
        return self._get_portal_role() == 'purchases_user'

    def _get_task_approval_roles(self):
        return [
            {'key': 'client', 'label': _('Cliente'), 'sequence': 10},
            {'key': 'client_supervisor', 'label': _('Supervisor Cliente'), 'sequence': 20},
            {'key': 'purchases', 'label': _('Área de compras cliente'), 'sequence': 30},
        ]

    def _get_task_approval(self, task, approval_role):
        approval_roles = {item['key']: item for item in self._get_task_approval_roles()}
        role_data = approval_roles.get(approval_role)
        if not task or not role_data:
            return self.env['portal.project.task.approval']

        Approval = self.env['portal.project.task.approval'].sudo()
        approval = Approval.search([
            ('task_id', '=', task.id),
            ('approval_role', '=', approval_role),
        ], limit=1)
        if not approval:
            approval = Approval.create({
                'task_id': task.id,
                'approval_role': approval_role,
                'sequence': role_data['sequence'],
            })
        return approval

    def _portal_can_approve_task(self, task, approval_role):
        if not task or approval_role not in {item['key'] for item in self._get_task_approval_roles()}:
            return False
        if not self._portal_can_authorize():
            return False
        portal_role = self._get_portal_role()
        allowed_roles = {
            'client_admin': {'client'},
            'client_supervisor': {'client_supervisor'},
            'purchases_user': {'purchases'},
        }
        return approval_role in allowed_roles.get(portal_role, set())

    def _get_task_approval_values(self, task):
        values = []
        for role_data in self._get_task_approval_roles():
            approval = self._get_task_approval(task, role_data['key'])
            values.append({
                'record': approval,
                'role': role_data['key'],
                'label': role_data['label'],
                'approved': approval.state == 'approved',
                'partner': approval.partner_id,
                'approved_date': approval.approved_date,
                'note': approval.note,
                'can_approve': (
                    approval.state != 'approved'
                    and self._portal_can_approve_task(task, role_data['key'])
                ),
                'post_url': '/my/control-obra/%s/approval/%s' % (task.id, role_data['key']),
            })
        return values

    def _get_portal_client_task_domain(self, commercial_partner):
        return OR([
            [('partner_id', 'child_of', commercial_partner.id)],
            [('project_id.partner_id', 'child_of', commercial_partner.id)],
            [('sale_line_id.order_partner_id', 'child_of', commercial_partner.id)],
            [('sale_order_id.partner_id', 'child_of', commercial_partner.id)],
        ])

    def _get_portal_invited_project_ids(self):
        Project = self.env['project.project']
        if not hasattr(Project, '_portal_project_invited_projects_for_user'):
            return []
        return Project.sudo()._portal_project_invited_projects_for_user(self.env.user).ids

    def _get_portal_project_visibility_key(self):
        Project = self.env['project.project']
        if hasattr(Project, '_portal_project_invited_visibility_key'):
            return Project._portal_project_invited_visibility_key()
        return 'invited_internal_portal'

    def _is_portal_user(self):
        user = self.env.user
        return user.has_group('base.group_portal') and not user.has_group('base.group_user')

    def _get_portal_related_project_access_domain(self, project_field, fallback_domain):
        invited_project_ids = self._get_portal_invited_project_ids()
        invited_project_domain = (
            [(project_field, 'in', invited_project_ids)]
            if invited_project_ids
            else [(project_field, '=', 0)]
        )

        if self._is_portal_user():
            return AND([invited_project_domain, fallback_domain])

        visibility_key = self._get_portal_project_visibility_key()
        regular_project_domain = OR([
            [(project_field, '=', False)],
            [('%s.privacy_visibility' % project_field, '!=', visibility_key)],
        ])
        access_domains = [
            AND([regular_project_domain, fallback_domain]),
        ]
        if invited_project_ids:
            access_domains.append(AND([invited_project_domain, fallback_domain]))
        return OR(access_domains)

    def _get_portal_task_follower_domain(self):
        partner = self.env.user.partner_id
        Task = self.env['project.task']
        if not partner or 'message_partner_ids' not in Task._fields:
            return [('id', '=', 0)]
        return [('message_partner_ids', 'in', [partner.id])]

    def _use_individual_task_follow_visibility(self, user_map):
        return (
            self._is_portal_user()
            and user_map
            and user_map.follow_visibility_scope == 'individual'
        )

    def _get_portal_task_access_domain(self, role_domain, user_map):
        if self._use_individual_task_follow_visibility(user_map):
            return AND([
                role_domain,
                self._get_portal_task_follower_domain(),
            ])
        return self._get_portal_related_project_access_domain('project_id', role_domain)

    def _get_portal_task_domain(self):
        commercial_partner = self._get_portal_commercial_partner()
        if not commercial_partner:
            return [('id', '=', 0)]

        base_domain = [
            ('active', '=', True),
            ('is_control_obra', '=', True),
            ('state', '!=', '1_canceled'),
        ]
        client_domain = self._get_portal_client_task_domain(commercial_partner)
        user_map = self._get_portal_user_map(active_only=False)
        if self._is_portal_user() and (not user_map or not user_map.active):
            return [('id', '=', 0)]
        company_domain = self._get_portal_company_domain(user_map)
        ayasa_supervisor_domain = self._get_portal_ayasa_supervisor_domain(user_map)

        portal_role = self._get_portal_role()

        if portal_role == 'internal_supervisor':
            if not user_map or not user_map.supervisor_interno_id:
                return [('id', '=', 0)]
            return AND([
                base_domain,
                company_domain,
                ayasa_supervisor_domain,
                self._get_portal_task_access_domain(
                    [('supervisor_interno', '=', user_map.supervisor_interno_id.id)],
                    user_map,
                ),
            ])
        if portal_role == 'client_supervisor':
            client_supervisors = self._get_portal_client_supervisor_records(
                user_map=user_map,
                commercial_partner=commercial_partner,
            )
            if not client_supervisors:
                return [('id', '=', 0)]
            return AND([
                base_domain,
                company_domain,
                ayasa_supervisor_domain,
                self._get_portal_task_access_domain(
                    [('supervisor_cliente', 'in', client_supervisors.ids)],
                    user_map,
                ),
            ])
        return AND([
            base_domain,
            company_domain,
            ayasa_supervisor_domain,
            self._get_portal_task_access_domain(client_domain, user_map),
        ])

    def _get_task_currency(self, task):
        return task.currency_id or task.company_id.currency_id or self.env.company.currency_id

    def _format_amount(self, amount, currency):
        return formatLang(self.env, amount or 0.0, currency_obj=currency)

    def _get_portal_task(self, task_id):
        return self.env['project.task'].sudo().search(AND([
            [('id', '=', task_id)],
            self._get_portal_task_domain(),
        ]), limit=1)

    def _get_labor_lines(self, task):
        if 'compensation.line' not in self.env.registry.models:
            return self.env['account.analytic.line']
        return self.env['compensation.line'].sudo().search([
            ('task_id', '=', task.id),
        ])
        # return self.env['compensation.line'].sudo().search([
        #     ('task_id', '=', task.id),
        #     ('compensation_id.state', 'in', ('approved', 'applied')),
        # ])

    def _selection_label(self, record, field_name):
        if field_name not in record._fields:
            return '-'
        field = record._fields[field_name]
        selection = field._description_selection(self.env) if hasattr(field, '_description_selection') else field.selection
        return dict(selection).get(record[field_name], record[field_name] or '-')

    def _detail_row(self, label, value):
        return {'label': label, 'value': value if value not in (False, None, '') else '-'}

    def _amount_row(self, label, amount, currency):
        return self._detail_row(label, self._format_amount(amount, currency))

    def _sum_field(self, records, field_name):
        if not records or field_name not in records._fields:
            return 0.0
        return sum(records.mapped(field_name))

    def _display_field_value(self, record, field_name):
        if field_name not in record._fields:
            return False
        value = record[field_name]
        field = record._fields[field_name]
        if field.type == 'many2one':
            return value.display_name
        if field.type in ('many2many', 'one2many'):
            return ', '.join(value.mapped('display_name'))
        if field.type == 'selection':
            return self._selection_label(record, field_name)
        if field.type == 'boolean':
            return _('Sí') if value else _('No')
        if field.type == 'date':
            return format_date(self.env, value)
        if field.type == 'datetime':
            return format_datetime(self.env, value, tz=self.env.user.tz or self.env.context.get('tz'))
        return value

    def _field_detail_row(self, record, field_name, label=None):
        if field_name not in record._fields:
            return False
        return self._detail_row(label or record._fields[field_name].string, self._display_field_value(record, field_name))

    def _clean_rows(self, rows):
        return [row for row in rows if row and row.get('value') != '-']

    def _detail_section(self, title, rows):
        return {'title': title, 'rows': self._clean_rows(rows)}

    def _get_advance_delivered_amount(self, advance):
        if 'sale_current' in advance._fields:
            return advance.sale_current or 0.0
        if hasattr(advance, '_get_price_for_calculation'):
            return (advance.unit_progress or 0.0) * advance._get_price_for_calculation()
        return (advance.unit_progress or 0.0) * (advance.precio_unidad or 0.0)

    def _get_purchase_line_currency(self, line, fallback_currency):
        return line.order_id.currency_id or line.currency_id or fallback_currency

    def _get_partner_specific_pricelist(self, partner):
        if not partner:
            return self.env['product.pricelist']
        property_record = self.env['ir.property'].sudo().search([
            ('name', '=', 'property_product_pricelist'),
            ('res_id', '=', 'res.partner,%s' % partner.id),
            ('company_id', 'in', [self.env.company.id, False]),
        ], order='company_id desc', limit=1)
        if not property_record or not property_record.value_reference:
            return self.env['product.pricelist']
        model_name, record_id = property_record.value_reference.split(',')
        if model_name != 'product.pricelist':
            return self.env['product.pricelist']
        return self.env['product.pricelist'].sudo().browse(int(record_id))

    def _get_portal_pricelist(self, partner=None, project=None):
        if project and project.sudo().open_book_pricelist_id:
            return project.sudo().open_book_pricelist_id.sudo()
        partner = self.env.user.partner_id or partner
        if not partner:
            return self.env['product.pricelist']
        commercial_partner = partner.commercial_partner_id
        specific_pricelist = self._get_partner_specific_pricelist(partner)
        if specific_pricelist:
            return partner.property_product_pricelist
        if commercial_partner:
            return commercial_partner.property_product_pricelist
        return partner.property_product_pricelist

    def _task_uses_open_book_costs(self, task):
        return bool(
            task
            and 'is_open_book' in task.project_id._fields
            and task.project_id.is_open_book
        )

    def _get_task_open_book_cost_lines(self, task, date_from=False, date_to=False):
        """Return the MOB snapshots which are the source of truth for portal cost."""
        if not self._task_uses_open_book_costs(task):
            return self.env['project.open.book.activity.line']
        domain = [
            ('task_id', '=', task.id),
            ('activity_id.state', '!=', 'cancelled'),
        ]
        if date_from:
            domain.append(('date', '>=', date_from))
        if date_to:
            domain.append(('date', '<=', date_to))
        return self.env['project.open.book.activity.line'].sudo().search(domain)

    def _get_pricelist_price(
        self, product, qty, partner=None, project=None, uom=None, date=None,
        fallback=0.0, require_rule=False,
    ):
        partner = self.env.user.partner_id or partner
        pricelist = self._get_portal_pricelist(partner, project)
        if not product or not pricelist:
            return fallback
        qty = qty or 1.0
        pricelist = pricelist.sudo()
        product = product.sudo()
        currency = project.currency_id if project else None
        if uom and product.uom_id.category_id != uom.category_id:
            return fallback
        if require_rule and not pricelist._get_product_rule(
            product, qty, currency=currency, uom=uom, date=date,
        ):
            return fallback
        return pricelist._get_product_price(
            product, qty, currency=currency, uom=uom, date=date,
        )

    def _convert_amount(self, amount, from_currency, to_currency, company=None, date=None):
        if not from_currency or not to_currency or from_currency == to_currency:
            return amount or 0.0
        return from_currency._convert(
            amount or 0.0,
            to_currency,
            company or self.env.company,
            date or fields.Date.context_today(self),
        )

    def _get_purchase_line_pricelist_amounts(self, line, target_currency=None):
        partner = self.env.user.partner_id
        project = line.task_id.project_id
        pricelist = self._get_portal_pricelist(partner, project)
        purchase_currency = self._get_purchase_line_currency(line, self.env.company.currency_id)
        sale_currency = project.currency_id or pricelist.currency_id or target_currency or purchase_currency
        purchase_unit = line.price_unit or 0.0
        purchase_subtotal = line.price_subtotal or (purchase_unit * (line.product_qty or 0.0))
        if not self._task_uses_open_book_costs(line.task_id):
            converted_subtotal = self._convert_amount(
                purchase_subtotal, purchase_currency,
                target_currency or purchase_currency,
                line.company_id or line.order_id.company_id or self.env.company,
                fields.Date.to_date(line.order_id.date_order or fields.Date.context_today(self)),
            )
            return {
                'price_unit': purchase_unit,
                'subtotal': purchase_subtotal,
                'currency': purchase_currency,
                'purchase_unit': purchase_unit,
                'purchase_subtotal': purchase_subtotal,
                'purchase_currency': purchase_currency,
                'subtotal_converted': converted_subtotal,
                'price_unit_text': self._format_amount(purchase_unit, purchase_currency),
                'subtotal_text': self._format_amount(purchase_subtotal, purchase_currency),
                'purchase_unit_text': self._format_amount(purchase_unit, purchase_currency),
                'purchase_subtotal_text': self._format_amount(purchase_subtotal, purchase_currency),
            }
        sale_unit = self._get_pricelist_price(
            line.product_id, line.product_qty, partner, project,
            line.product_uom, line.order_id.date_order, purchase_unit,
        )
        sale_subtotal = sale_unit * (line.product_qty or 0.0)
        company = line.company_id or line.order_id.company_id or self.env.company
        price_date = fields.Date.to_date(
            line.order_id.date_order or line.order_id.date_approve or fields.Date.context_today(self)
        )
        converted_subtotal = self._convert_amount(sale_subtotal, sale_currency, target_currency or sale_currency, company, price_date)
        return {
            'price_unit': sale_unit,
            'subtotal': sale_subtotal,
            'currency': sale_currency,
            'purchase_unit': purchase_unit,
            'purchase_subtotal': purchase_subtotal,
            'purchase_currency': purchase_currency,
            'subtotal_converted': converted_subtotal,
            'price_unit_text': self._format_amount(sale_unit, sale_currency),
            'subtotal_text': self._format_amount(sale_subtotal, sale_currency),
            'purchase_unit_text': self._format_amount(purchase_unit, purchase_currency),
            'purchase_subtotal_text': self._format_amount(purchase_subtotal, purchase_currency),
        }

    def _get_purchase_line_pricelist_subtotal(self, line, quantity=None, target_currency=None):
        partner = self.env.user.partner_id
        project = line.task_id.project_id
        pricelist = self._get_portal_pricelist(partner, project)
        purchase_currency = self._get_purchase_line_currency(line, self.env.company.currency_id)
        if not self._task_uses_open_book_costs(line.task_id):
            discount = line.discount if 'discount' in line._fields else 0.0
            subtotal = (
                (line.price_unit or 0.0)
                * (1.0 - (discount or 0.0) / 100.0)
                * (quantity if quantity is not None else (line.product_qty or 0.0))
            )
            return self._convert_amount(
                subtotal, purchase_currency, target_currency or purchase_currency,
                line.company_id or line.order_id.company_id or self.env.company,
                fields.Date.to_date(line.order_id.date_order or fields.Date.context_today(self)),
            )
        sale_currency = project.currency_id or pricelist.currency_id or target_currency or purchase_currency
        pricing_qty = line.product_qty or quantity or 0.0
        sale_unit = self._get_pricelist_price(
            line.product_id, pricing_qty, partner, project,
            line.product_uom, line.order_id.date_order, line.price_unit,
        )
        sale_subtotal = sale_unit * (quantity if quantity is not None else (line.product_qty or 0.0))
        company = line.company_id or line.order_id.company_id or self.env.company
        price_date = fields.Date.to_date(
            line.order_id.date_order or line.order_id.date_approve or fields.Date.context_today(self)
        )
        return self._convert_amount(
            sale_subtotal,
            sale_currency,
            target_currency or sale_currency,
            company,
            price_date,
        )

    def _get_product_pricelist_subtotal(
        self, product, quantity, target_currency=None, company=None, date=None,
        project=None, uom=None, fallback=0.0, require_rule=False,
    ):
        partner = self.env.user.partner_id
        pricelist = self._get_portal_pricelist(partner, project)
        base_currency = company.currency_id if company and company.currency_id else self.env.company.currency_id
        sale_currency = (
            project.currency_id if project else False
        ) or pricelist.currency_id or target_currency or base_currency
        quantity = quantity or 0.0
        sale_unit = self._get_pricelist_price(
            product, quantity, partner, project, uom, date, fallback,
            require_rule,
        ) if product else fallback
        sale_subtotal = sale_unit * quantity
        return self._convert_amount(
            sale_subtotal,
            sale_currency,
            target_currency or sale_currency,
            company or self.env.company,
            date or fields.Date.context_today(self),
        )

    def _get_expense_pricelist_amounts(self, expense, target_currency=None):
        company = expense.company_id or self.env.company
        project = expense.task_id.project_id
        expense_currency = (
            company.currency_id
            if expense.total_amount
            else expense.currency_id or target_currency or company.currency_id
        )
        quantity = expense.quantity if 'quantity' in expense._fields else 1.0
        quantity = quantity or 1.0
        purchase_subtotal = expense.total_amount or expense.total_amount_currency or 0.0
        purchase_unit = purchase_subtotal / quantity if quantity else purchase_subtotal
        sale_currency = expense_currency
        sale_unit = purchase_unit
        sale_subtotal = purchase_subtotal
        price_date = fields.Date.to_date(expense.date or fields.Date.context_today(self))
        if not self._task_uses_open_book_costs(expense.task_id):
            converted = self._convert_amount(
                purchase_subtotal, expense_currency,
                target_currency or expense_currency, company, price_date,
            )
            return {
                'quantity': quantity,
                'purchase_unit': purchase_unit,
                'purchase_subtotal': purchase_subtotal,
                'purchase_currency': expense_currency,
                'purchase_subtotal_converted': converted,
                'purchase_unit_text': self._format_amount(purchase_unit, expense_currency),
                'purchase_subtotal_text': self._format_amount(purchase_subtotal, expense_currency),
                'sale_unit': purchase_unit,
                'sale_subtotal': purchase_subtotal,
                'sale_currency': expense_currency,
                'sale_subtotal_converted': converted,
                'sale_unit_text': self._format_amount(purchase_unit, expense_currency),
                'sale_subtotal_text': self._format_amount(purchase_subtotal, expense_currency),
            }
        pricelist = self._get_portal_pricelist(project=project)
        if pricelist:
            sale_currency = project.currency_id or pricelist.currency_id or expense_currency
            sale_unit = self._get_pricelist_price(
                expense.product_id, quantity, project=project,
                uom=expense.product_uom_id, date=price_date,
                fallback=purchase_unit, require_rule=True,
            )
            sale_subtotal = sale_unit * quantity
        purchase_subtotal_converted = self._convert_amount(
            purchase_subtotal,
            expense_currency,
            target_currency or expense_currency,
            company,
            price_date,
        )
        sale_subtotal_converted = self._convert_amount(
            sale_subtotal,
            sale_currency,
            target_currency or sale_currency,
            company,
            price_date,
        )
        return {
            'quantity': quantity,
            'purchase_unit': purchase_unit,
            'purchase_subtotal': purchase_subtotal,
            'purchase_currency': expense_currency,
            'purchase_subtotal_converted': purchase_subtotal_converted,
            'purchase_unit_text': self._format_amount(purchase_unit, expense_currency),
            'purchase_subtotal_text': self._format_amount(purchase_subtotal, expense_currency),
            'sale_unit': sale_unit,
            'sale_subtotal': sale_subtotal,
            'sale_currency': sale_currency,
            'sale_subtotal_converted': sale_subtotal_converted,
            'sale_unit_text': self._format_amount(sale_unit, sale_currency),
            'sale_subtotal_text': self._format_amount(sale_subtotal, sale_currency),
        }

    def _get_stock_move_cost(self, move):
        quantity = move.quantity or move.product_uom_qty or 0.0
        fallback = move.price_unit or move.product_id.standard_price or 0.0
        if not self._task_uses_open_book_costs(move.task_id):
            return fallback * quantity
        return self._get_product_pricelist_subtotal(
            move.product_id, quantity,
            target_currency=move.company_id.currency_id,
            company=move.company_id,
            date=move.date,
            project=move.task_id.project_id,
            uom=move.product_uom,
            fallback=fallback,
        )

    def _get_labor_pricelist_subtotal(self, labor, target_currency=None):
        quantity = labor.regular_hours or 0.0
        fallback = labor.total_cost or 0.0
        if not self._task_uses_open_book_costs(labor.task_id):
            return fallback
        fallback_unit = fallback / quantity if quantity else fallback
        employee = labor.employee_id
        product = (
            employee.job_id.sudo().open_book_labor_product_id
            if employee and employee.job_id else False
        )
        project = labor.task_id.project_id
        return self._get_product_pricelist_subtotal(
            product, quantity,
            target_currency=target_currency or labor.currency_id,
            company=project.company_id,
            date=labor.date,
            project=project,
            uom=product.uom_id if product else None,
            fallback=fallback_unit,
            require_rule=True,
        ) if quantity else fallback

    def _get_purchase_lines_to_cost(self, purchase_lines, stock_moves):
        consumed_purchase_lines = (
            stock_moves.mapped('purchase_line_id')
            if stock_moves and 'purchase_line_id' in stock_moves._fields
            else purchase_lines.browse()
        )
        if not consumed_purchase_lines:
            consumed_purchase_lines = purchase_lines.filtered(
                lambda line: bool(line.move_ids & stock_moves) if 'move_ids' in line._fields else False
            )
        return purchase_lines - consumed_purchase_lines

    def _get_portal_partner_setting(self):
        commercial_partner = self._get_portal_commercial_partner().sudo()
        if not commercial_partner:
            return self.env['portal.project.partner.setting']
        return self.env['portal.project.partner.setting'].sudo().search([
            ('partner_id', '=', commercial_partner.id),
        ], limit=1)

    def _get_portal_profit_percentage(self):
        setting = self._get_portal_partner_setting()
        return setting.profit_percentage if setting else 0.0

    def _get_task_filter_records(self, domain, field_name):
        Task = self.env['project.task'].sudo()
        if field_name not in Task._fields:
            return self.env['project.task']
        field = Task._fields[field_name]
        if field.type != 'many2one':
            return self.env['project.task']
        groups = Task.read_group(
            AND([domain, [(field_name, '!=', False)]]),
            [field_name],
            [field_name],
            lazy=False,
        )
        record_ids = [
            group[field_name][0]
            for group in groups
            if group.get(field_name)
        ]
        return self.env[field.comodel_name].sudo().browse(record_ids).sorted('name')

    def _get_portal_filter_options(self, domain):
        SaleOrder = self.env['sale.order']
        invoice_status_field = SaleOrder._fields.get('invoice_status')
        invoice_status_selection = (
            invoice_status_field._description_selection(self.env)
            if invoice_status_field and hasattr(invoice_status_field, '_description_selection')
            else []
        )
        return {
            'supervisors': self._get_task_filter_records(domain, 'supervisor_interno'),
            'client_supervisors': self._get_task_filter_records(domain, 'supervisor_cliente'),
            'projects': self._get_task_filter_records(domain, 'project_id'),
            'plants': self._get_task_filter_records(domain, 'planta_trabajo'),
            'sale_orders': self._get_task_filter_records(domain, 'sale_order_id'),
            'invoice_status_options': [
                {'value': value, 'label': label}
                for value, label in invoice_status_selection
            ],
        }

    def _get_record_attachments(self, record):
        if not record:
            return self.env['ir.attachment']
        Attachment = self.env['ir.attachment'].sudo()
        domain = [
            ('res_model', '=', record._name),
            ('res_id', '=', record.id),
            ('type', '=', 'binary'),
        ]
        if 'portal_project_visible' in Attachment._fields:
            domain.append(('portal_project_visible', '=', True))
        else:
            domain.append(('public', '=', True))
        return Attachment.search(domain, order='create_date desc')

    def _get_record_messages(self, record):
        if not record or 'message_ids' not in record._fields:
            return self.env['mail.message']
        return record.message_ids.sudo().filtered(
            lambda message: (
                message.message_type == 'comment'
                and (not message.subtype_id or not message.subtype_id.internal)
            )
        ).sorted('date')

    def _get_record_conversation_target(self, section, record):
        if section == 'purchase-line' and record and record.order_id:
            return record.order_id
        return record

    def _get_portal_attachment(self, task_id, section, record_id, attachment_id):
        task, record = self._get_portal_record(task_id, section, record_id)
        if not record:
            return task, record, self.env['ir.attachment']
        attachment = self._get_record_attachments(record).filtered(lambda item: item.id == attachment_id)
        return task, record, attachment[:1]

    def _set_portal_profit_percentage(self, percentage):
        commercial_partner = self._get_portal_commercial_partner().sudo()
        if not commercial_partner:
            return 0.0
        percentage = max(0.0, min(percentage, 100.0))
        setting = self._get_portal_partner_setting()
        if setting:
            setting.write({'profit_percentage': percentage})
        else:
            self.env['portal.project.partner.setting'].sudo().create({
                'partner_id': commercial_partner.id,
                'profit_percentage': percentage,
            })
        return percentage

    def _get_advance_detail_sections(self, task, advance, currency):
        advance_currency = advance.currency_id or currency
        price_unit = advance._get_price_for_calculation() if hasattr(advance, '_get_price_for_calculation') else advance.precio_unidad
        return [
            self._detail_section(_('Datos internos del proyecto'), [
                self._field_detail_row(advance, 'name', _('ID Avance')),
                self._field_detail_row(advance, 'avances_state', _('Estado del avance')),
                self._field_detail_row(advance, 'state', _('Estatus factura')),
                self._field_detail_row(advance, 'sale_order_id', _('Orden de venta')),
                self._field_detail_row(advance, 'project_id', _('Proyecto')),
                self._field_detail_row(advance, 'task_id', _('Partida / tarea')),
                # self._field_detail_row(advance, 'update_id', _('Actualización de proyecto')),
                self._field_detail_row(advance, 'cliente_project', _('Cliente')),
                self._field_detail_row(advance, 'especialidad', _('Especialidad')),
            ]),
            self._detail_section(_('Información producto a entregar'), [
                self._field_detail_row(advance, 'producto', _('Producto')),
                self._field_detail_row(advance, 'especialidad_producto', _('Especialidad del servicio')),
                self._field_detail_row(advance, 'unidad_medida', _('Unidad')),
                self._amount_row(_('Precio unitario'), price_unit, advance_currency),
            ]),
            self._detail_section(_('Datos generales del avance'), [
                self._field_detail_row(advance, 'date', _('Fecha del trabajo')),
                self._field_detail_row(advance, 'oc_pedido', _('OC/Pedido')),
                self._field_detail_row(advance, 'ct', _('Centro de trabajo')),
                self._field_detail_row(advance, 'or_rfq', _('OR/RFQ')),
                self._field_detail_row(advance, 'no_cotizacion', _('No. cotización')),
            ]),
            self._detail_section(_('Descripción detallada del avance'), [
                self._field_detail_row(advance, 'planta', _('Planta')),
                self._field_detail_row(advance, 'area_equipo', _('Área / tag equipo')),
                self._field_detail_row(advance, 'hora_inicio', _('Hora inicio')),
                self._field_detail_row(advance, 'hora_termino', _('Hora término')),
                self._field_detail_row(advance, 'supervisorplanta', _('Supervisor Cliente')),
                self._field_detail_row(advance, 'responsible_id', _('Supervisor Interno')),
                self._field_detail_row(advance, 'licencia', _('Licencia/OM')),
            ]),
            self._detail_section(_('Avance actual'), [
                self._field_detail_row(advance, 'virtual_quant_progress', _('Unidades entregadas')),
                self._field_detail_row(advance, 'quant_total', _('Unidades a entregar')),
                self._amount_row(_('Valor entregada'), self._get_advance_delivered_amount(advance), advance_currency),
                self._field_detail_row(advance, 'missing_quant', _('Unidades faltantes')),
                self._detail_row(_('Avance porcentual entregado'), '%.2f%%' % (advance.actual_progress or 0.0) if 'actual_progress' in advance._fields else False),
                self._detail_row(_('Progreso total'), '%.2f%%' % (advance.virtual_total_progress or 0.0) if 'virtual_total_progress' in advance._fields else False),
            ]),
        ]

    def _get_expense_detail_sections(self, task, expense, currency):
        expense_currency = expense.currency_id or currency
        pricelist_amounts = self._get_expense_pricelist_amounts(expense, currency)
        return [
            self._detail_section(_('Datos del gasto'), [
                self._field_detail_row(expense, 'date', _('Fecha del gasto')),
                self._field_detail_row(expense, 'name', _('Concepto')),
                self._field_detail_row(expense, 'employee_id', _('Empleado')),
                self._field_detail_row(expense, 'product_id', _('Producto')),
                self._field_detail_row(expense, 'description', _('Descripción')),
                self._detail_row(_('Cantidad'), pricelist_amounts['quantity']),
                self._amount_row(_('Valor unitario'), pricelist_amounts['sale_unit'], pricelist_amounts['sale_currency']),
                self._amount_row(_('Monto total'), pricelist_amounts['sale_subtotal'], pricelist_amounts['sale_currency']),
            ]),
            self._detail_section(_('Validación'), [
                self._field_detail_row(expense, 'sheet_id', _('Hoja de gastos')),
                self._detail_row(_('Estado'), self._selection_label(expense.sheet_id, 'state') if expense.sheet_id else '-'),
            ]),
        ]

    def _get_purchase_detail_sections(self, task, line, currency):
        pricelist_amounts = self._get_purchase_line_pricelist_amounts(line, currency)
        return [
            self._detail_section(_('Orden de compra'), [
                self._field_detail_row(line, 'order_id', _('Orden')),
                self._detail_row(_('Proveedor'), line.order_id.partner_id.name),
                self._detail_row(_('Estado'), self._selection_label(line.order_id, 'state')),
                self._field_detail_row(line.order_id, 'date_approve', _('Fecha compromiso')),
            ]),
            self._detail_section(_('Línea de compra'), [
                self._field_detail_row(line, 'product_id', _('Producto')),
                self._field_detail_row(line, 'name', _('Descripción')),
                self._field_detail_row(line, 'product_qty', _('Cantidad')),
                self._field_detail_row(line, 'product_uom', _('Unidad')),
                self._amount_row(_('Valor unitario'), pricelist_amounts['price_unit'], pricelist_amounts['currency']),
                self._amount_row(_('Monto total'), pricelist_amounts['subtotal'], pricelist_amounts['currency']),
            ]),
        ]

    def _get_stock_detail_sections(self, task, move, currency):
        stock_cost = self._get_stock_move_cost(move)
        return [
            self._detail_section(_('Movimiento'), [
                self._detail_row(_('Referencia'), move.reference or move.picking_id.name or move.name),
                self._field_detail_row(move, 'state', _('Estado')),
                self._field_detail_row(move, 'date', _('Fecha consumo')),
                self._field_detail_row(move, 'picking_id', _('Transferencia')),
            ]),
            self._detail_section(_('Producto y almacén'), [
                self._field_detail_row(move, 'product_id', _('Producto')),
                self._field_detail_row(move, 'quantity', _('Cantidad hecha')),
                self._field_detail_row(move, 'product_uom', _('Unidad')),
                self._field_detail_row(move, 'location_id', _('Origen')),
                self._field_detail_row(move, 'location_dest_id', _('Destino')),
                self._amount_row(_('Costo estimado'), stock_cost, currency),
            ]),
        ]

    def _get_labor_detail_sections(self, task, labor, currency):
        labor_currency = labor.currency_id or currency
        labor_total = self._get_labor_pricelist_subtotal(labor, currency)
        return [
            self._detail_section(_('Compensación'), [
                self._field_detail_row(labor, 'compensation_id', _('Compensación')),
                self._field_detail_row(labor, 'date', _('Fecha trabajada')),
                self._detail_row(_('Estado'), self._selection_label(labor.compensation_id, 'state') if labor.compensation_id else '-'),
                self._field_detail_row(labor, 'justification', _('Justificación')),
            ]),
            self._detail_section(_('Empleado y horas'), [
                self._field_detail_row(labor, 'employee_id', _('Empleado')),
                self._field_detail_row(labor, 'department_id', _('Departamento')),
                self._field_detail_row(labor, 'check_in', _('Entrada')),
                self._field_detail_row(labor, 'check_out', _('Salida')),
                self._field_detail_row(labor, 'regular_hours', _('Horas normales')),
                self._field_detail_row(labor, 'extra_hours', _('Horas extra')),
                self._field_detail_row(labor, 'lost_hours', _('Horas perdidas')),
            ]),
            self._detail_section(_('Costos'), [
                self._amount_row(_('Costo horas normales'), labor.normal_hour_cost, labor_currency),
                self._amount_row(_('Costo horas extra'), labor.extra_hour_cost, labor_currency),
                self._amount_row(_('Compensación monetaria'), labor.monetary_compensation, labor_currency),
                self._amount_row(_('Costo total'), labor_total, currency),
            ]),
        ]

    def _get_task_detail_sections(self, task):
        denominator = 0.0
        if 'total_pieces' in task._fields:
            denominator = task.total_pieces or 0.0
        if not denominator and 'piezas_pendientes' in task._fields:
            denominator = task.piezas_pendientes or 0.0
        delivered_qty = task.quant_progress or 0.0
        pending_qty = max(denominator - delivered_qty, 0.0)
        progress = max(0.0, min(task.progress or 0.0, 100.0))
        progress_row = self._detail_row(_('Avance'), '%.2f%%' % (task.progress or 0.0))
        progress_row.update({'progress': progress})

        return [
            self._detail_section(_('Datos generales'), [
                self._field_detail_row(task, 'planned_date_begin', _('Fecha inicio')),
                self._field_detail_row(task, 'date_deadline', _('Fecha termino')),
                self._field_detail_row(task, 'centro_trabajo', _('Centro de trabajo')),
                self._field_detail_row(task, 'planta_trabajo', _('Planta')),
                self._field_detail_row(task, 'supervisor_cliente', _('Supervisor Cliente')),
                self._field_detail_row(task, 'supervisor_interno', _('Supervisor Interno')),
                self._field_detail_row(task, 'state', _('Estado Ejecución')),
                # self._field_detail_row(task, 'approval_state', _('Estado de autorización')),
            ]),
            self._detail_section(_('Avance'), [
                self._detail_row(_('Unidades a entregar'), denominator),
                self._field_detail_row(task, 'quant_progress', _('Unidades entregadas')),
                self._detail_row(_('Unidades por entregar'), pending_qty),
                self._field_detail_row(task, 'qty_invoiced', _('Unidades facturadas')),
                progress_row,
            ]),
        ]

    def _get_task_portal_values(self, task):
        approved_expenses = task.expense_ids.sudo()
        confirmed_purchase_lines = task.purchase_line_ids.sudo()
        stock_moves = task.stock_move_ids.sudo()
        advances = task.sub_update_ids.sudo()
        labor_lines = self._get_labor_lines(task).sudo()
        currency = self._get_task_currency(task)
        stock_moves_to_cost = stock_moves.filtered(lambda move: not move.purchase_line_id)
        purchase_lines_to_cost = confirmed_purchase_lines
        purchase_line_cost_map = {
            line.id: line in purchase_lines_to_cost
            for line in confirmed_purchase_lines
        }
        purchase_line_pricelist_map = {
            line.id: self._get_purchase_line_pricelist_amounts(line, currency)
            for line in confirmed_purchase_lines
        }
        expense_pricelist_map = {
            expense.id: self._get_expense_pricelist_amounts(expense, currency)
            for expense in approved_expenses
        }
        labor_pricelist_map = {
            labor.id: self._get_labor_pricelist_subtotal(labor, currency)
            for labor in labor_lines
        }
        movement_category_rows = {
            'materials': [],
            'labor': [],
            'equipment_tools': [],
            'external_services': [],
        }

        def add_product_movement(category, values):
            category = category if category in movement_category_rows else 'materials'
            movement_category_rows[category].append(values)

        for expense in approved_expenses:
            product = expense.product_id
            add_product_movement(product.portal_movement_category if product else False, {
                'origin': _('Gasto'),
                'date': expense.date,
                'reference': expense.name or expense.display_name,
                'product': product,
                'quantity': expense.quantity if 'quantity' in expense._fields else 1.0,
                'amount_text': expense_pricelist_map[expense.id]['sale_subtotal_text'],
                'url': '/my/control-obra/%s/expense/%s' % (task.id, expense.id),
            })

        for line in confirmed_purchase_lines:
            product = line.product_id
            add_product_movement(product.portal_movement_category if product else False, {
                'origin': _('Compra'),
                'date': line.order_id.date_approve or line.order_id.date_order,
                'reference': line.order_id.name or line.name,
                'product': product,
                'quantity': line.product_qty,
                'amount_text': purchase_line_pricelist_map[line.id]['subtotal_text'],
                'url': '/my/control-obra/%s/purchase-line/%s' % (task.id, line.id),
            })

        for move in stock_moves_to_cost:
            product = move.product_id
            add_product_movement(product.portal_movement_category if product else False, {
                'origin': _('Almacén'),
                'date': move.date,
                'reference': move.reference or move.picking_id.name or move.name,
                'product': product,
                'quantity': move.quantity,
                'amount_text': self._format_amount(self._get_stock_move_cost(move), currency),
                'url': '/my/control-obra/%s/stock-move/%s' % (task.id, move.id),
            })

        for category_rows in movement_category_rows.values():
            category_rows.sort(
                key=lambda row: fields.Datetime.to_string(fields.Datetime.to_datetime(row['date'])) or '',
                reverse=True,
            )

        if self._task_uses_open_book_costs(task):
            open_book_lines = self._get_task_open_book_cost_lines(task)
            expense_total = sum(open_book_lines.filtered(
                lambda line: line.source_type == 'expense'
            ).mapped('subtotal'))
            purchase_total = sum(open_book_lines.filtered(
                lambda line: line.source_type == 'purchase'
            ).mapped('subtotal'))
            stock_total = sum(open_book_lines.filtered(
                lambda line: line.source_type == 'stock'
            ).mapped('subtotal'))
            labor_total = sum(open_book_lines.filtered(
                lambda line: line.source_type == 'labor'
            ).mapped('subtotal'))
            concept_impact_total = sum(open_book_lines.filtered(
                lambda line: line.source_type == 'concept_impact'
            ).mapped('subtotal'))
        else:
            open_book_lines = self.env['project.open.book.activity.line']
            expense_total = sum(
                expense_pricelist_map[expense.id]['sale_subtotal_converted']
                for expense in approved_expenses
            )
            purchase_total = sum(
                purchase_line_pricelist_map[line.id]['subtotal_converted']
                for line in purchase_lines_to_cost
            )
            stock_total = sum(self._get_stock_move_cost(move) for move in stock_moves_to_cost)
            labor_total = sum(labor_pricelist_map.values())
            concept_impact_total = 0.0
        total_cost = (
            expense_total + purchase_total + stock_total + labor_total
            + concept_impact_total
        )
        delivered_total = sum(self._get_advance_delivered_amount(advance) for advance in advances)
        billable_base_total = delivered_total + total_cost

        profit_percentage = self._get_portal_profit_percentage()
        profit_base_total = billable_base_total - purchase_total
        profit_amount = profit_base_total * profit_percentage / 100.0
        grand_total = billable_base_total + profit_amount

        return {
            'approved_expenses': approved_expenses,
            'confirmed_purchase_lines': confirmed_purchase_lines,
            'purchase_lines_to_cost': purchase_lines_to_cost,
            'purchase_line_cost_map': purchase_line_cost_map,
            'purchase_line_pricelist_map': purchase_line_pricelist_map,
            'expense_pricelist_map': expense_pricelist_map,
            'labor_pricelist_map': labor_pricelist_map,
            'stock_moves': stock_moves_to_cost,
            'movement_category_rows': movement_category_rows,
            'advances': advances,
            'labor_lines': labor_lines,
            'open_book_lines': open_book_lines,
            'expense_total': expense_total,
            'purchase_total': purchase_total,
            'stock_total': stock_total,
            'labor_total': labor_total,
            'concept_impact_total': concept_impact_total,
            'total_cost': total_cost,
            'delivered_total': delivered_total,
            'billable_base_total': billable_base_total,
            'profit_base_total': profit_base_total,
            'profit_percentage': profit_percentage,
            'profit_amount': profit_amount,
            'grand_total': grand_total,
            'currency': currency,
            'expense_total_text': self._format_amount(expense_total, currency),
            'purchase_total_text': self._format_amount(purchase_total, currency),
            'stock_total_text': self._format_amount(stock_total, currency),
            'labor_total_text': self._format_amount(labor_total, currency),
            'concept_impact_total_text': self._format_amount(
                concept_impact_total, currency
            ),
            'total_cost_text': self._format_amount(total_cost, currency),
            'delivered_total_text': self._format_amount(delivered_total, currency),
            'billable_base_total_text': self._format_amount(billable_base_total, currency),
            'profit_base_total_text': self._format_amount(profit_base_total, currency),
            'profit_amount_text': self._format_amount(profit_amount, currency),
            'grand_total_text': self._format_amount(grand_total, currency),
            'format_amount': self._format_amount,
        }

    def _get_task_export_rows(self, task):
        values = self._get_task_portal_values(task)
        return [
            [_('Concepto'), _('Importe')],
            [_('Producción entregada'), values['delivered_total_text']],
            [_('Gastos'), values['expense_total_text']],
            [_('Compras'), values['purchase_total_text']],
            [_('Material consumido de almacén'), values['stock_total_text']],
            [_('Mano de obra'), values['labor_total_text']],
            [_('Impactos de concepto'), self._format_amount(
                values['concept_impact_total'], values['currency']
            )],
            [_('Costo antes de Fee'), values['total_cost_text']],
            [_('Base cobrable'), values['billable_base_total_text']],
            [_('Base para utilidad'), values['profit_base_total_text']],
            [_('Utilidad %'), '%.2f%%' % values['profit_percentage']],
            [_('Utilidad importe'), values['profit_amount_text']],
            [_('Total estimado a cobrar'), values['grand_total_text']],
        ]

    def _get_task_portal_rows(self, tasks):
        rows = []

        approval_model = self.env['portal.project.cost.approval'].sudo()
        state_labels = dict(
            approval_model._fields['state']._description_selection(self.env)
        )

        approvals = approval_model.search([
            ('task_id','in', tasks.ids),
            ('state','!=', 'cancelled'),
        ], order='task_id, version desc, id desc')

        latest_approval_by_task = {}

        for approval in approvals:
            # El primer registro encontrado es el corte más reciente de la tarea.
            latest_approval_by_task.setdefault(approval.task_id.id, approval)

        for task in tasks:
            sale_order = task.sale_order_id
            cost_approval = latest_approval_by_task.get(task.id)

            rows.append({
                'task': task,
                'invoice_status_text': (
                    self._selection_label(sale_order, 'invoice_status')
                    if sale_order else _('Sin OS')
                ),
                'cost_approval': cost_approval,
                'cost_approval_state': (
                    cost_approval.state if cost_approval else 'not_submitted'
                ),
                'cost_approval_state_text': (
                    state_labels.get(cost_approval.state,_('Sin Estado'))
                    if cost_approval else _('Sin enviar')
                ),
            })
            
        return rows

    def _get_portal_record(self, task_id, section, record_id):
        task = self._get_portal_task(task_id)
        if not task:
            return task, False

        financial_sections = {
            'expense',
            'purchase-line',
            'stock-move',
            'labor',
        }
        if section in financial_sections and not self._portal_can_view_financial_summary():
            return task, False

        values = self._get_task_portal_values(task)
        allowed_records = {
            'advance': values['advances'],
            'expense': values['approved_expenses'],
            'purchase-line': values['confirmed_purchase_lines'],
            'stock-move': values['stock_moves'],
            'labor': values['labor_lines'],
        }.get(section)
        if allowed_records is None:
            return task, False

        record = allowed_records.filtered(lambda item: item.id == record_id)
        return task, record[:1]

    def _get_record_detail_values(self, task, section, record):
        currency = self._get_task_currency(task)
        back_url = '/my/control-obra/%s' % task.id
        title = record.display_name
        sections = []

        if section == 'advance':
            title = _('Avance: %s') % (record.name or record.display_name)
            sections = self._get_advance_detail_sections(task, record, currency)
        elif section == 'expense':
            title = _('Gasto: %s') % record.name
            sections = self._get_expense_detail_sections(task, record, currency)
        elif section == 'purchase-line':
            title = _('Compra: %s') % (record.order_id.name or record.display_name)
            sections = self._get_purchase_detail_sections(task, record, currency)
        elif section == 'stock-move':
            title = _('Movimiento de almacén: %s') % (record.reference or record.picking_id.name or record.name)
            sections = self._get_stock_detail_sections(task, record, currency)
        elif section == 'labor':
            title = _('Mano de obra: %s') % (record.employee_id.name or record.display_name)
            sections = self._get_labor_detail_sections(task, record, currency)

        conversation_target = self._get_record_conversation_target(section, record)
        return {
            'task': task,
            'record': record,
            'section': section,
            'record_title': title,
            'detail_sections': [detail_section for detail_section in sections if detail_section['rows']],
            'attachments': self._get_record_attachments(record),
            'messages': self._get_record_messages(conversation_target),
            'message_post_url': '/my/control-obra/%s/%s/%s/message' % (task.id, section, record.id),
            'show_conversation': 'message_ids' in conversation_target._fields,
            'back_url': back_url,
            'page_name': 'portal_project_work',
        }
