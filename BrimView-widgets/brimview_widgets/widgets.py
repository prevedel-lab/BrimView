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
