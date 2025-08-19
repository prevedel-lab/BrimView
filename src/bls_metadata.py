import panel as pn
import param
import pandas as pd

import brimfile as bls

from bls_panel_app_widgets.bls_file_input import BlsFileInput

class BlsMetadata(pn.viewable.Viewer):
    """
        A widget to display the metadata stored in the brim files.

        For Pyodide (ie `panel convert`) reasons, we use a side-effect way to
        update the tabulator's data.
    """
    bls_data = param.ClassSelector(
        class_= bls.Data, default=None, allow_refs=True, precedence=-1
    )

    def __init__(self, Bh5file: BlsFileInput, **params):
        self.tabulator = pn.widgets.Tabulator(show_index=False, disabled=True, groupby=['Group'], hidden_columns=['Group'])
        self.title = pn.pane.Markdown("## Metadata of the file \n Please load a file")
        super().__init__(**params)

        # Explicit annotation, because param and type hinting is not working properly
        self.bls_data: bls.Data = Bh5file.param.data

    @param.depends("bls_data", watch=True)
    def _update_tabulator(self):
        if self.bls_data is None:
            self.title.object = "## Metadata of the file \n Please load a file"
            return 
        self.title.object = "## Metadata of the file"

        rows = []
        for meta_type, parameters in self.bls_data.get_metadata().all_to_dict().items():
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

    def __panel__(self):
        return pn.Column(
            self.title,
            self.tabulator
        )