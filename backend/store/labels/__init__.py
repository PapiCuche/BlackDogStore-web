"""
LABEL-03 — thermal labels as a PURE package (ADR `docs/adr-inventory-labels.md`, D5, D10).

    data (already resolved) + layout  →  plan  →  real-scale PDF

It never reads a model, never writes a row and never prints. The caller
resolves the company's data (codes, prices, the logo, the IMEI profile and the
caller's permission) and hands it over through the closed `LabelData`
contract; the package decides what fits, what goes to review and draws it.

Not here yet: `LabelTemplate` / `LabelBatch` rows (LABEL-03b),
SVG output, thermal commands (LABEL-04).
"""
from .compose import (
    READY, REVIEW, LabelPlan, Logo, SensitiveLabelRefused, plan_accessory, plan_device, plan_labels,
)
from .data import AccessoryData, DeviceData, DeviceOptions, LabelCode, LabelDataError
from .layout import (
    ACCESSORY_30X20, BUILTIN_LAYOUTS, DEVICE_COMMERCIAL_50X25, DEVICE_TECHNICAL_60X40, Layout, LayoutError,
)
from .pdf import NothingToRender, render_pdf

__all__ = [
    'ACCESSORY_30X20', 'BUILTIN_LAYOUTS', 'DEVICE_COMMERCIAL_50X25', 'DEVICE_TECHNICAL_60X40',
    'AccessoryData', 'DeviceData', 'DeviceOptions', 'LabelCode', 'LabelDataError', 'LabelPlan', 'Layout',
    'LayoutError', 'Logo', 'NothingToRender', 'READY', 'REVIEW', 'SensitiveLabelRefused',
    'plan_accessory', 'plan_device', 'plan_labels', 'render_pdf',
]
