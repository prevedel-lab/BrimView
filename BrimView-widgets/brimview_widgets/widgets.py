from typing import Callable
import panel as pn
import param


class HorizontalEditableIntSlider(pn.widgets.EditableIntSlider):
    """
    The rendering of the EditableIntSlider was not how we wanted it in the app.
    So we're changing a bit how it's displayed (everything in a row),
    and also at the same time adding a tooltip icon at the end.

    Everything else (and the general behaviour of the widget) should be the same,
    so you can consider this a drop-in replacement for the EditableIntSlider
    """

    tooltip_text = param.String(default="", doc="Tooltip text to show on hover.")
    tooltip_range_or_fixed_range = param.Boolean(
        default=False,
        doc="Whether to show the range or the fixed range in the tooltip. True=range, False=fixed range.",
    )

    def __init__(self, **params):
        super().__init__(**params)

        # We're overwritting some of the default of the "base" classe: <_EditableContinuousSlider>
        self._composite = pn.Row()
        self._label.align = "center"
        self._value_edit.align = "center"
        self._slider.align = "center"

        self._tooltip = pn.widgets.TooltipIcon()
        self.tooltip_update()
        self._composite.extend(
            [pn.Row(self._label, self._value_edit), self._slider, self._tooltip]
        )

        # Definition found in Widget
        self.margin = (5, 10)  # (vertical, horizontal) or (top, right, bottom, left)

    @pn.depends("fixed_start", "fixed_end", "start", "end", "tooltip_range_or_fixed_range", "tooltip_text", watch=True)
    def tooltip_update(self):
        if self.tooltip_range_or_fixed_range:
            start = self.start
            end = self.end
        else:
            start = self.fixed_start
            end = self.fixed_end
        self._tooltip.value = f"{self.tooltip_text} ([{start} ; {end}])"

class SwitchWithLabels(pn.viewable.Viewer):
    label_true = param.String(default="On", doc="Label when switch is True")
    label_false = param.String(default="Off", doc="Label when switch is False")
    value = param.Boolean(default=False, doc="Switch value")
    
    def __init__(self, **params):
        super().__init__(**params)

        self._label_true = pn.pane.Markdown(self.label_true)
        self._label_false = pn.pane.Markdown(self.label_false)
        self._switch = pn.widgets.Switch.from_param(self.param.value)

        self._layout = pn.Row(self.label_false, self._switch, self.label_true, align="center")

    @pn.depends("label_true", watch=True)
    def _update_label_true(self):
        self._label_true.object = self.label_true

    @pn.depends("label_false", watch=True)
    def _update_label_true(self):
        self._label_false.object = self.label_false

    def __panel__(self):
        return self._layout

    
