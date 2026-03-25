import panel as pn
import param
import pandas as pd

from panel.widgets.base import WidgetBase
from panel.custom import PyComponent

import brimfile as bls

from bokeh.models.widgets.tables import HTMLTemplateFormatter

from .utils import catch_and_notify
from .logging import logger


class BlsMetadata(WidgetBase, PyComponent):
    """
    A widget to display the metadata stored in the brim files.

    For Pyodide (ie `panel convert`) reasons, we use a side-effect way to
    update the tabulator's data.
    """

    value = param.ClassSelector(
        class_=bls.Data,
        default=None,
        allow_refs=True,
        doc="The names of the features selected and their set values",
    )

    def __init__(self, **params):
        self.tabulator = pn.widgets.Tabulator(
            show_index=False,
            disabled=True,
            groupby=["Group"],
            hidden_columns=["Group"],
            formatters={
                "Validity": HTMLTemplateFormatter(
                    template="""
<%
const v = value;

let bg = "#e74c3c";   // default red
let icon = "✖";

if (v === "valid") {
    bg = "#2ecc71";
    icon = "✔";
}
else if (v === "unknown field" || v === "likely typo") {
    bg = "#f39c12";
    icon = "⚠";
}
%>
<span style="
    background-color:<%= bg %>;
    color:white;
    padding:3px 10px;
    border-radius:10px;
    font-size:12px;
    font-weight:600;
    white-space:nowrap;
">
<%= icon %> <%= v %>
</span>
"""
                )
            },
        )
        self.title = pn.pane.Markdown("## Metadata of the file \n Please load a file")
        super().__init__(**params)

        logger.info("BlsMetadata initialized")

        # Explicit annotation, because param and type hinting is not working properly
        self.value: bls.Data

    @param.depends("value", watch=True)
    @catch_and_notify(prefix="<b>Update metadata: </b>")
    def _update_tabulator(self):
        logger.info("Updating metadata tabulator")
        if self.value is None:
            self.title.object = "## Metadata of the file \n Please load a file"
            self.tabulator_visibility = False
            #self.tabulator.value = None // <- This can be an issue, if the tabulator is currently trying to order a column
            return
        self.title.object = "## Metadata of the file"

        rows = []
        for meta_type, parameters in (
            self.value.get_metadata()
            .all_to_dict(validate=True, include_missing=True)
            .items()
        ):
            for name, item in parameters.items():
                name: str
                item: bls.metadata.Metadata.Item
                rows.append(
                    {
                        "Parameter": name,
                        "Value": item.value,
                        "Unit": item.units,
                        "Validity": item.get_validity().value,
                        "Group": meta_type,
                    }
                )

        df = pd.DataFrame(
            rows, columns=["Parameter", "Value", "Unit", "Group", "Validity"]
        )
        self.tabulator.value = df
        self.tabulator_visibility = True

    @property
    def tabulator_visibility(self):
        """
        Visibility of the tabulator widget.

        This is to allow a workaroung that works in both Pyodide and normal Python:
        **Bug**: the tabulator gets populated, is invisible (ie not in the active tab) but
        is still *above* the other widgets, making them unclickable.

        Potentially related to: https://github.com/holoviz/panel/issues/8053 and https://github.com/holoviz/panel/issues/8103
        """
        return self.tabulator.visible

    @tabulator_visibility.setter
    def tabulator_visibility(self, value: bool):
        self.tabulator.visible = value

    def __panel__(self):
        return pn.Column(self.title, self.tabulator)
