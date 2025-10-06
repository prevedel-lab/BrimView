import panel as pn
import param
import pandas as pd

from panel.widgets.base import WidgetBase
from panel.custom import PyComponent

import brimfile as bls

from .bls_file_input import BlsFileInput

from .utils import catch_and_notify
from .logging import logger

class BlsMetadata(WidgetBase, PyComponent):
    """
        A widget to display the metadata stored in the brim files.

        For Pyodide (ie `panel convert`) reasons, we use a side-effect way to
        update the tabulator's data.
    """
    value = param.ClassSelector(
        class_= bls.Data, default=None, allow_refs=True,
        doc="The names of the features selected and their set values",
    )

    def __init__(self, **params):
        self.tabulator = pn.widgets.Tabulator(show_index=False, disabled=True, groupby=['Group'], hidden_columns=['Group'])
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
            self.tabulator.value = None
            return 
        self.title.object = "## Metadata of the file"

        rows = []
        for meta_type, parameters in self.value.get_metadata().all_to_dict().items():
            for name, item in parameters.items():
                rows.append(
                    {
                        "Parameter": name,
                        "Value": item.value,
                        "Unit": item.units,
                        "Group": meta_type,
                    }
                )

        df = pd.DataFrame(rows, columns=["Parameter", "Value", "Unit", "Group"])
        self.tabulator.value = df

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
        return pn.Column(
            self.title,
            self.tabulator
        )
    