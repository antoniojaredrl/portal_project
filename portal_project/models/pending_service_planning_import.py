import base64
import io
import math
import re
import zipfile
from xml.etree import ElementTree

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import xlsxwriter


XLSX_MIMETYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
MATERIAL_SHEET = "Materiales y Equipos"
LABOR_SHEET = "Mano de obra"
CATALOG_SHEET = "Catalogos"
MATERIAL_HEADERS = (
    "Actividad",
    "Tipo",
    "Código recurso",
    "Cantidad planeada",
    "Valor unitario",
)
LABOR_HEADERS = (
    "Actividad",
    "Identificador empleado",
    "Descripción",
    "Horas planeadas",
    "Valor unitario",
)
RESOURCE_TYPES = {
    "material": "material",
    "equipo_herramienta": "equipo_herramienta",
    "equipo": "equipo_herramienta",
    "herramienta": "equipo_herramienta",
}


def _column_name(cell_reference):
    return re.match(r"[A-Z]+", cell_reference or "").group(0)


def _xlsx_rows(content):
    """Return workbook values without requiring openpyxl on the Odoo server."""
    if len(content) > 10 * 1024 * 1024:
        raise UserError(_("El archivo XLSX no puede exceder 10 MB."))
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, TypeError):
        raise UserError(_("El archivo no es un libro XLSX válido."))

    spreadsheet_ns = (
        "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    )
    relationship_ns = (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    )
    package_relationship_ns = (
        "http://schemas.openxmlformats.org/package/2006/relationships"
    )
    ns = {"x": spreadsheet_ns}
    try:
        if sum(item.file_size for item in archive.infolist()) > 50 * 1024 * 1024:
            raise UserError(
                _("El contenido descomprimido del XLSX excede el límite permitido.")
            )
        shared_strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("x:si", ns):
                shared_strings.append(
                    "".join(
                        node.text or ""
                        for node in item.iter("{%s}t" % spreadsheet_ns)
                    )
                )

        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        relationships = ElementTree.fromstring(
            archive.read("xl/_rels/workbook.xml.rels")
        )
        relationship_map = {
            item.attrib["Id"]: item.attrib["Target"]
            for item in relationships.findall(
                "{%s}Relationship" % package_relationship_ns
            )
        }
        result = {}
        for sheet in workbook.findall("x:sheets/x:sheet", ns):
            relationship_id = sheet.attrib[
                "{%s}id" % relationship_ns
            ]
            target = relationship_map[relationship_id].lstrip("/")
            if not target.startswith("xl/"):
                target = "xl/%s" % target
            sheet_root = ElementTree.fromstring(archive.read(target))
            rows = []
            for row in sheet_root.findall(".//x:sheetData/x:row", ns):
                values = {}
                for cell in row.findall("x:c", ns):
                    reference = cell.attrib.get("r", "")
                    column = _column_name(reference)
                    cell_type = cell.attrib.get("t")
                    value_node = cell.find("x:v", ns)
                    value = value_node.text if value_node is not None else ""
                    if cell_type == "s" and value != "":
                        value = shared_strings[int(value)]
                    elif cell_type == "inlineStr":
                        inline = cell.find("x:is", ns)
                        value = (
                            "".join(
                                node.text or ""
                                for node in inline.iter(
                                    "{%s}t" % spreadsheet_ns
                                )
                            )
                            if inline is not None
                            else ""
                        )
                    values[column] = value
                rows.append(values)
                if len(rows) > 10001:
                    raise UserError(
                        _("Una hoja de la plantilla excede 10,000 renglones.")
                    )
            result[sheet.attrib["name"]] = rows
        return result
    except (
        ElementTree.ParseError,
        KeyError,
        ValueError,
        IndexError,
        zipfile.BadZipFile,
    ):
        raise UserError(_("No fue posible interpretar la estructura del XLSX."))
    finally:
        archive.close()


class PendingService(models.Model):
    _inherit = "pending.service"

    def _planning_activity_token(self, service_line):
        return "P%02d" % service_line.partida

    def _planning_employee_token(self, employee):
        return "ID:%s" % employee.id

    def _planning_template_content(self):
        self.ensure_one()
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(
            output,
            {
                "in_memory": True,
                "strings_to_formulas": False,
                "strings_to_urls": False,
            },
        )
        header = workbook.add_format(
            {
                "bold": True,
                "font_color": "white",
                "bg_color": "#44546A",
                "border": 1,
            }
        )
        note = workbook.add_format(
            {"italic": True, "font_color": "#666666"}
        )
        money = workbook.add_format({"num_format": '#,##0.00'})
        quantity = workbook.add_format({"num_format": "0.00"})

        material_sheet = workbook.add_worksheet(MATERIAL_SHEET)
        labor_sheet = workbook.add_worksheet(LABOR_SHEET)
        catalog_sheet = workbook.add_worksheet(CATALOG_SHEET)
        catalog_sheet.hide()

        for worksheet, headers in (
            (material_sheet, MATERIAL_HEADERS),
            (labor_sheet, LABOR_HEADERS),
        ):
            worksheet.freeze_panes(1, 0)
            worksheet.autofilter(0, 0, 1000, len(headers) - 1)
            for column, title in enumerate(headers):
                worksheet.write(0, column, title, header)
            worksheet.set_column(0, 0, 18)
            worksheet.set_column(1, 1, 25)
            worksheet.set_column(2, 2, 45)
            worksheet.set_column(3, 4, 20)

        activities = self.service_line_ids.sorted(
            key=lambda line: (line.sequence, line.id)
        )
        material_products = self.env["product.product"].search(
            [("default_code", "!=", False)],
            order="default_code, name",
        )
        employees = self.env["hr.employee"].search(
            [("company_id", "in", (False, self.company_id.id))],
            order="name",
        )

        catalog_sheet.write_row(
            0,
            0,
            (
                "Actividad",
                "Descripción actividad",
                "Código recurso",
                "Descripción recurso",
                "Tipo sugerido",
                "Empleado",
                "Nombre empleado",
            ),
            header,
        )
        max_catalog_rows = max(
            len(activities), len(material_products), len(employees), 1
        )
        for index in range(max_catalog_rows):
            if index < len(activities):
                line = activities[index]
                catalog_sheet.write(
                    index + 1, 0, self._planning_activity_token(line)
                )
                catalog_sheet.write(index + 1, 1, line.display_name)
            if index < len(material_products):
                product = material_products[index]
                catalog_sheet.write(index + 1, 2, product.default_code or "")
                catalog_sheet.write(index + 1, 3, product.display_name)
                resource_type = (
                    "equipo_herramienta"
                    if product.product_tmpl_id.portal_movement_category
                    == "equipment_tools"
                    else "material"
                )
                catalog_sheet.write(index + 1, 4, resource_type)
            if index < len(employees):
                employee = employees[index]
                catalog_sheet.write(
                    index + 1,
                    5,
                    self._planning_employee_token(employee),
                )
                catalog_sheet.write(index + 1, 6, employee.name)

        activity_last_row = max(len(activities) + 1, 2)
        product_last_row = max(len(material_products) + 1, 2)
        employee_last_row = max(len(employees) + 1, 2)
        material_sheet.data_validation(
            1,
            0,
            1000,
            0,
            {
                "validate": "list",
                "source": "=%s!$A$2:$A$%d"
                % (CATALOG_SHEET, activity_last_row),
            },
        )
        material_sheet.data_validation(
            1,
            1,
            1000,
            1,
            {
                "validate": "list",
                "source": ["material", "equipo_herramienta"],
            },
        )
        material_sheet.data_validation(
            1,
            2,
            1000,
            2,
            {
                "validate": "list",
                "source": "=%s!$C$2:$C$%d"
                % (CATALOG_SHEET, product_last_row),
            },
        )
        labor_sheet.data_validation(
            1,
            0,
            1000,
            0,
            {
                "validate": "list",
                "source": "=%s!$A$2:$A$%d"
                % (CATALOG_SHEET, activity_last_row),
            },
        )
        labor_sheet.data_validation(
            1,
            1,
            1000,
            1,
            {
                "validate": "list",
                "source": "=%s!$F$2:$F$%d"
                % (CATALOG_SHEET, employee_last_row),
            },
        )

        material_rows = self.planned_material_ids.sorted(
            key=lambda line: (line.sequence, line.id)
        )
        for row_index, line in enumerate(material_rows, 1):
            material_sheet.write(
                row_index,
                0,
                self._planning_activity_token(line.service_line_id)
                if line.service_line_id
                else "",
            )
            material_sheet.write(row_index, 1, line.tipo_recurso)
            material_sheet.write(
                row_index, 2, line.product_id.default_code or ""
            )
            material_sheet.write(
                row_index, 3, line.qty_planned, quantity
            )
            material_sheet.write(row_index, 4, line.cost_unit, money)

        labor_rows = self.planned_labor_ids.sorted(
            key=lambda line: (line.sequence, line.id)
        )
        for row_index, line in enumerate(labor_rows, 1):
            labor_sheet.write(
                row_index,
                0,
                self._planning_activity_token(line.service_line_id)
                if line.service_line_id
                else "",
            )
            labor_sheet.write(
                row_index,
                1,
                self._planning_employee_token(line.employee_id),
            )
            labor_sheet.write(row_index, 2, line.description or "")
            labor_sheet.write(
                row_index, 3, line.hours_planned, quantity
            )
            labor_sheet.write(row_index, 4, line.cost_unit, money)

        material_start = max(len(material_rows) + 1, 1)
        labor_start = max(len(labor_rows) + 1, 1)
        for row_index in range(material_start, material_start + 5):
            material_sheet.write(
                row_index,
                0,
                "",
                note,
            )
        for row_index in range(labor_start, labor_start + 5):
            labor_sheet.write(row_index, 0, "", note)

        workbook.close()
        return output.getvalue()

    def action_download_planning_template(self):
        self.ensure_one()
        self.check_access_rights("read")
        self.check_access_rule("read")
        wizard = self.env["pending.service.planning.download"].create(
            {
                "service_id": self.id,
                "filename": "planeacion_%s.xlsx"
                % re.sub(r"[^A-Za-z0-9_-]+", "_", self.display_name),
                "file_data": base64.b64encode(
                    self._planning_template_content()
                ),
            }
        )
        return {
            "type": "ir.actions.act_url",
            "url": (
                "/web/content?model=pending.service.planning.download"
                "&id=%d&field=file_data&filename_field=filename&download=true"
            )
            % wizard.id,
            "target": "self",
        }

    def action_open_planning_import(self):
        self.ensure_one()
        self.check_access_rights("write")
        self.check_access_rule("write")
        return {
            "name": _("Importar planeación"),
            "type": "ir.actions.act_window",
            "res_model": "pending.service.planning.import",
            "view_mode": "form",
            "target": "new",
            "context": {"default_service_id": self.id},
        }

    def action_clear_planning_import(self):
        for service in self:
            service.check_access_rights("write")
            service.check_access_rule("write")
            service.planned_material_ids.unlink()
            service.planned_labor_ids.unlink()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Carga de planeación eliminada"),
                "message": _(
                    "Se eliminaron los materiales, equipos/herramientas y "
                    "líneas de mano de obra planeada."
                ),
                "type": "success",
                "sticky": False,
            },
        }


class PendingServicePlanningDownload(models.TransientModel):
    _name = "pending.service.planning.download"
    _description = "Descarga de plantilla de planeación"

    service_id = fields.Many2one("pending.service", required=True)
    file_data = fields.Binary(readonly=True, required=True)
    filename = fields.Char(readonly=True, required=True)


class PendingServicePlanningImport(models.TransientModel):
    _name = "pending.service.planning.import"
    _description = "Importar planeación de servicio pendiente"

    service_id = fields.Many2one(
        "pending.service",
        string="Servicio pendiente",
        required=True,
        readonly=True,
    )
    file_data = fields.Binary(
        string="Archivo XLSX",
        required=True,
        attachment=False,
    )
    filename = fields.Char(string="Nombre del archivo")
    import_mode = fields.Selection(
        [
            ("update", "Actualizar y agregar"),
            ("add", "Solo agregar"),
            ("replace", "Reemplazar planeación"),
        ],
        string="Modo de importación",
        default="update",
        required=True,
    )

    @api.constrains("filename")
    def _check_filename(self):
        for wizard in self:
            if wizard.filename and not wizard.filename.lower().endswith(
                ".xlsx"
            ):
                raise ValidationError(
                    _("La plantilla debe tener extensión .xlsx.")
                )

    def _number(self, value, sheet, row, label):
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValidationError(
                _("%(sheet)s, fila %(row)s: %(label)s debe ser numérico.")
                % {
                    "sheet": sheet,
                    "row": row,
                    "label": label,
                }
            )
        if not math.isfinite(number) or number < 0:
            raise ValidationError(
                _("%(sheet)s, fila %(row)s: %(label)s no puede ser negativo.")
                % {
                    "sheet": sheet,
                    "row": row,
                    "label": label,
                }
            )
        return number

    def _activity_map(self):
        return {
            self.service_id._planning_activity_token(line): line
            for line in self.service_id.service_line_ids
        }

    def _parse_file(self):
        self.ensure_one()
        try:
            content = base64.b64decode(self.file_data)
        except (ValueError, TypeError):
            raise UserError(_("No fue posible leer el archivo cargado."))
        sheets = _xlsx_rows(content)
        missing = {
            MATERIAL_SHEET,
            LABOR_SHEET,
        } - set(sheets)
        if missing:
            raise ValidationError(
                _("Faltan las hojas requeridas: %s.")
                % ", ".join(sorted(missing))
            )
        activity_map = self._activity_map()
        products = self.env["product.product"].search(
            [("default_code", "!=", False)]
        )
        product_map = {}
        duplicate_product_codes = set()
        for product in products:
            code = product.default_code.strip().upper()
            if code in product_map:
                duplicate_product_codes.add(code)
            product_map[code] = product
        employees = self.env["hr.employee"].search(
            [("company_id", "in", (False, self.service_id.company_id.id))]
        )
        employee_map = {
            self.service_id._planning_employee_token(employee): employee
            for employee in employees
        }

        material_values = []
        material_keys = set()
        rows = sheets[MATERIAL_SHEET]
        headers = tuple(rows[0].get(chr(65 + index), "") for index in range(5))
        if headers != MATERIAL_HEADERS:
            raise ValidationError(
                _("La hoja '%s' fue modificada o no corresponde a la plantilla.")
                % MATERIAL_SHEET
            )
        for row_number, row in enumerate(rows[1:], 2):
            activity_token = str(row.get("A", "")).strip().upper()
            resource_type = str(row.get("B", "")).strip().lower()
            product_code = str(row.get("C", "")).strip().upper()
            if not any((activity_token, resource_type, product_code, row.get("D"), row.get("E"))):
                continue
            if activity_token not in activity_map:
                raise ValidationError(
                    _("%(sheet)s, fila %(row)s: la actividad '%(value)s' no pertenece al servicio.")
                    % {
                        "sheet": MATERIAL_SHEET,
                        "row": row_number,
                        "value": activity_token,
                    }
                )
            if resource_type not in RESOURCE_TYPES:
                raise ValidationError(
                    _("%(sheet)s, fila %(row)s: el tipo debe ser 'material' o 'equipo_herramienta'.")
                    % {"sheet": MATERIAL_SHEET, "row": row_number}
                )
            if product_code in duplicate_product_codes:
                raise ValidationError(
                    _("%(sheet)s, fila %(row)s: el código '%(value)s' está duplicado en el catálogo de productos.")
                    % {
                        "sheet": MATERIAL_SHEET,
                        "row": row_number,
                        "value": product_code,
                    }
                )
            product = product_map.get(product_code)
            if not product:
                raise ValidationError(
                    _("%(sheet)s, fila %(row)s: no existe el recurso con código '%(value)s'.")
                    % {
                        "sheet": MATERIAL_SHEET,
                        "row": row_number,
                        "value": product_code,
                    }
                )
            quantity = self._number(
                row.get("D"), MATERIAL_SHEET, row_number, _("Cantidad")
            )
            unit_cost = self._number(
                row.get("E"), MATERIAL_SHEET, row_number, _("Valor unitario")
            )
            if not quantity:
                raise ValidationError(
                    _("%(sheet)s, fila %(row)s: la cantidad debe ser mayor que cero.")
                    % {"sheet": MATERIAL_SHEET, "row": row_number}
                )
            normalized_type = RESOURCE_TYPES[resource_type]
            key = (
                activity_map[activity_token].id,
                normalized_type,
                product.id,
            )
            if key in material_keys:
                raise ValidationError(
                    _("%(sheet)s, fila %(row)s: el recurso está repetido para la misma actividad y tipo.")
                    % {"sheet": MATERIAL_SHEET, "row": row_number}
                )
            material_keys.add(key)
            material_values.append(
                {
                    "service_id": self.service_id.id,
                    "service_line_id": activity_map[activity_token].id,
                    "tipo_recurso": normalized_type,
                    "product_id": product.id,
                    "qty_planned": quantity,
                    "cost_unit": unit_cost,
                    "sequence": row_number * 10,
                }
            )

        labor_values = []
        labor_keys = set()
        rows = sheets[LABOR_SHEET]
        headers = tuple(rows[0].get(chr(65 + index), "") for index in range(5))
        if headers != LABOR_HEADERS:
            raise ValidationError(
                _("La hoja '%s' fue modificada o no corresponde a la plantilla.")
                % LABOR_SHEET
            )
        for row_number, row in enumerate(rows[1:], 2):
            activity_token = str(row.get("A", "")).strip().upper()
            employee_token = str(row.get("B", "")).strip().upper()
            description = str(row.get("C", "")).strip()
            if not any((activity_token, employee_token, description, row.get("D"), row.get("E"))):
                continue
            if activity_token not in activity_map:
                raise ValidationError(
                    _("%(sheet)s, fila %(row)s: la actividad '%(value)s' no pertenece al servicio.")
                    % {
                        "sheet": LABOR_SHEET,
                        "row": row_number,
                        "value": activity_token,
                    }
                )
            employee = employee_map.get(employee_token)
            if not employee:
                raise ValidationError(
                    _("%(sheet)s, fila %(row)s: no existe el empleado '%(value)s' en el catálogo.")
                    % {
                        "sheet": LABOR_SHEET,
                        "row": row_number,
                        "value": employee_token,
                    }
                )
            hours = self._number(
                row.get("D"), LABOR_SHEET, row_number, _("Horas")
            )
            unit_cost = self._number(
                row.get("E"), LABOR_SHEET, row_number, _("Valor unitario")
            )
            if not hours:
                raise ValidationError(
                    _("%(sheet)s, fila %(row)s: las horas deben ser mayores que cero.")
                    % {"sheet": LABOR_SHEET, "row": row_number}
                )
            key = (activity_map[activity_token].id, employee.id)
            if key in labor_keys:
                raise ValidationError(
                    _("%(sheet)s, fila %(row)s: el empleado está repetido para la misma actividad.")
                    % {"sheet": LABOR_SHEET, "row": row_number}
                )
            labor_keys.add(key)
            labor_values.append(
                {
                    "service_id": self.service_id.id,
                    "service_line_id": activity_map[activity_token].id,
                    "employee_id": employee.id,
                    "description": description,
                    "hours_planned": hours,
                    "cost_unit": unit_cost,
                    "sequence": row_number * 10,
                }
            )
        if not material_values and not labor_values:
            raise ValidationError(_("La plantilla no contiene líneas para importar."))
        return material_values, labor_values

    def _upsert(self, model_name, values_list, key_fields):
        model = self.env[model_name]
        for values in values_list:
            domain = [
                (field_name, "=", values[field_name])
                for field_name in key_fields
            ]
            existing = model.search(domain, limit=1)
            if self.import_mode == "add" or not existing:
                model.create(values)
            else:
                existing.write(values)

    def action_import(self):
        self.ensure_one()
        self.service_id.check_access_rights("write")
        self.service_id.check_access_rule("write")
        material_values, labor_values = self._parse_file()
        if self.import_mode == "replace":
            self.service_id.planned_material_ids.unlink()
            self.service_id.planned_labor_ids.unlink()
        self._upsert(
            "pending.service.planned.material",
            material_values,
            ("service_id", "service_line_id", "tipo_recurso", "product_id"),
        )
        self._upsert(
            "pending.service.planned.labor",
            labor_values,
            ("service_id", "service_line_id", "employee_id"),
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Planeación importada"),
                "message": _(
                    "Se procesaron %(materials)d recursos y %(labor)d líneas de mano de obra."
                )
                % {
                    "materials": len(material_values),
                    "labor": len(labor_values),
                },
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
