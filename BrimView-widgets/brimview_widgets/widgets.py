import panel as pn

class HorizontalEditableIntSlider(pn.widgets.EditableIntSlider):
    """
        The rendering of the EditableIntSlider was not how we wanted it in the app.
        So we're changing a bit how it's displayed (everything in a row), 
        and also at the same time adding a tooltip icon at the end.

        Everything else (and the general behaviour of the widget) should be the same,
        so you can consider this a drop-in replacement for the EditableIntSlider
    """
    def __init__(self, **params):
        super().__init__(**params)

        #We're overwritting some of the default of the "base" classe: <_EditableContinuousSlider>
        self._composite = pn.Row()
        self._label.align = "center"
        self._value_edit.align = "center"
        self._slider.align = "center"

        self._composite.extend([pn.Row(self._label, self._value_edit), self._slider, self.tooltip])
        
        # Definition found in Widget
        self.margin = (5, 10) # (vertical, horizontal) or (top, right, bottom, left)

    @pn.depends("fixed_start", "fixed_end")
    def tooltip(self) -> pn.widgets.TooltipIcon:
        return pn.widgets.TooltipIcon(value=f"Change which slice is displayed ([{self.fixed_start} ; {self.fixed_end}])")    
