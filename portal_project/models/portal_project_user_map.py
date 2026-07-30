# -*- coding: utf-8 -*-
from odoo import fields, models


class PortalProjectUserMap(models.Model):
    _name = 'portal.project.user.map'
    _description = 'Mapeo de usuario portal control de obra'
    _rec_name = 'partner_id'

    active = fields.Boolean(
        default=True,
        help='Desactive el mapeo para bloquear el acceso de este contacto al portal Control de Obra sin eliminar la configuración.',
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Contacto portal',
        required=True,
        ondelete='cascade',
        index=True,
        help='Contacto del portal al que se aplicará esta configuración de tipo de usuario, rol y visibilidad.',
    )
    role = fields.Selection(
        [
            ('client_admin', 'Cliente / Responsable'),
            ('purchases_user', 'Compras Cliente'),
            ('client_supervisor', 'Supervisor Cliente'),
        ],
        string='Tipo de usuario',
        required=True,
        default='client_admin',
        help=(
            'Define el alcance funcional del contacto en el portal. '
            'Cliente / Responsable: ve las actividades relacionadas con su cliente o proyectos permitidos. '
            'Compras Cliente: ve el mismo alcance base del cliente y puede consultar información financiera/costos. '
            'Supervisor Cliente: limita la visibilidad a actividades asignadas al supervisor cliente que coincida con el contacto portal.'
        ),
    )
    portal_role = fields.Selection(
        [
            ('authorizer', 'Autorizador'),
            ('requester', 'Solicitador'),
            ('viewer', 'Visor'),
        ],
        string='Rol',
        required=True,
        default='viewer',
        help=(
            'Define las acciones permitidas para el contacto dentro del alcance de su tipo de usuario. '
            'Autorizador: puede dar VoBo en las actividades que le correspondan. '
            'Solicitador: puede crear solicitudes de servicio/actividades. '
            'Visor: solo puede consultar información.'
        ),
    )
    follow_visibility_scope = fields.Selection(
        [
            ('global', 'Global'),
            ('individual', 'Actividad Individual'),
        ],
        string='Visibilidad de seguimientos',
        required=True,
        default='global',
        help=(
            'Controla cómo se interpretan los seguimientos del contacto. '
            'Global: si el contacto está invitado o como seguidor del proyecto, puede ver todas las actividades del proyecto según su tipo de usuario. '
            'Actividad Individual: aunque el contacto sea seguidor del proyecto, solo ve las actividades donde esté como seguidor directo.'
        ),
    )
    company_ids = fields.Many2many(
        'res.company',
        'portal_project_user_map_company_rel',
        'map_id',
        'company_id',
        string='Compañías permitidas',
        help='Si se seleccionan compañías, el portal solo mostrará actividades relacionadas con esas compañías. Si se deja vacío, no agrega restricción por compañía.',
    )
    supervisor_interno_ids = fields.Many2many(
        'hr.employee',
        'portal_project_user_map_supervisor_interno_rel',
        'map_id',
        'employee_id',
        string='Supervisores Internos permitidos',
        domain=[('supervisa', '=', True)],
        help='Si se seleccionan supervisores internos, el portal solo mostrará información vinculada a esos supervisores. Si se deja vacío, no restringe por supervisor interno.',
    )
    supervisor_interno_id = fields.Many2one(
        'hr.employee',
        string='Supervisor Interno',
        domain=[('supervisa', '=', True)],
        help='Campo heredado para compatibilidad con configuraciones anteriores de Supervisor Interno. Ya no se muestra como tipo de usuario seleccionable en el mapeo.',
    )
    note = fields.Text(
        string='Notas',
        help='Notas internas para documentar por qué se configuró este acceso o cualquier consideración administrativa.',
    )

    _sql_constraints = [
        (
            'partner_unique',
            'unique(partner_id)',
            'Solo puede existir un mapeo de control de obra por contacto portal.',
        ),
    ]

    def init(self):
        self.env.cr.execute("""
            UPDATE portal_project_user_map
               SET follow_visibility_scope = 'global'
             WHERE follow_visibility_scope IS NULL
        """)
        self.env.cr.execute("""
            UPDATE portal_project_user_map
               SET portal_role = 'viewer'
             WHERE portal_role IS NULL
        """)
