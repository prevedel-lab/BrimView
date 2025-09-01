import asyncio
from enum import Enum
import tempfile
import pandas as pd
import panel as pn
import param
import holoviews as hv
from holoviews import streams
import numpy as np
import yaml

import time
import brimfile as bls
from .bls_data_visualizer import BlsDataVisualizer

from panel.widgets.base import WidgetBase
from panel.custom import PyComponent
from .types import bls_param


def _convert_numpy(obj):
    """
    Utility function to convert a Dict with numpy object into a Dict with "pure" python object.
    Usefull if you plan on serializing / dumping the dict.
    """
    if isinstance(obj, dict):
        return {k: _convert_numpy(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_convert_numpy(v) for v in obj]
    elif isinstance(obj, np.generic):
        return obj.item()  # Convert NumPy scalar to Python scalar
    else:
        return obj


class BlsSpectrumVisualizer(WidgetBase, PyComponent):
    """Class to display a spectrum from a pixel in the image."""

    text = param.String(
        default="Click on the image to get pixel coordinates", precedence=-1
    )

    dataset_zyx_coord = param.NumericTuple(
        default=None, length=3, allow_refs=True, doc=""
    )
    busy = param.Boolean(default=False, doc="Is the widget busy?")

    def get_coordinates(self) -> tuple[int, int, int]:
        """
        Returns:
            (z, y, x): as int/pixel coordinates
        """
        z = self.dataset_zyx_coord[0]
        y = self.dataset_zyx_coord[1]
        x = self.dataset_zyx_coord[2]
        return (z, y, x)

    class Fits(Enum):
        Lorentzian = "Lorentzian"
        Gaussian = "Gaussian"
        PseudoVoigt50 = "Pseudo Voigt (50%)"

    display_fit = param.Boolean(
        default=True, label="Compute and display fit over raw data"
    )
    fit_type = param.Selector(default=Fits.Lorentzian, objects=Fits)

    # bls_file = param.ClassSelector(class_=bls.File, default=None, allow_refs=True)
    # bls_data = param.ClassSelector(
    #     class_=bls.Data, default=None, allow_refs=True, precedence=-1
    # )
    # bls_analysis = param.ClassSelector(
    #     class_=bls.Data.AnalysisResults, allow_refs=True, precedence=-1
    # )

    value = param.ClassSelector(
        class_=bls_param,
        default=None,
        precedence=-1,
        doc="BLS file/data/analysis",
        allow_refs=True,
    )

    results_at_point = param.Dict(label="Result values at this point", precedence=-1)

    def __init__(self, result_plot: BlsDataVisualizer, **params):
        self.quantity_tabulator = pn.widgets.Tabulator(
            show_index=False,
            disabled=True,
            groupby=["Quantity"],
            hidden_columns=["Quantity"],
            configuration={
                "groupStartOpen": False  # This makes all groups collapsed initially
            },
        )
        self.spinner = pn.indicators.LoadingSpinner(value=True, size=20, name='Loading...', visible=True)
        self.bls_spectrum_in_image = None
        super().__init__(**params)
        # Watch tap stream updates

        # Reference to the "main" plot_click
        self.dataset_zyx_coord = result_plot.param.dataset_zyx_click
        # self.bls_file: bls.File = result_plot.param.bls_file
        # self.bls_data: bls.Data = result_plot.param.bls_data
        # self.bls_analysis: bls.Data.AnalysisResults = result_plot.param.bls_analysis

        # Test
        self.value: bls_param = bls_param(
            file=result_plot.param.bls_file,
            data=result_plot.param.bls_data,
            analysis=result_plot.param.bls_analysis,
        )
        
        # Because we're not a pn.Viewer anymore, by default we lost the "card" display
        # so despite us returning a card from __panel__, the shown card didn't match
        # the card display (background color, shadows)
        self.css_classes.append("card")

    def _compute_fitted_curves(self, x_range: np.ndarray, z, y, x):
        if not self.display_fit:
            return []

        def lorentzian(x, x0, w):
            return 1 / (1 + ((x - x0) / (w / 2)) ** 2)

        def real_lorentzian(x, shift, width, amplitude, offset):
            return amplitude * lorentzian(x, shift, width) + offset

        def real_gaussian(x, shift, width, amplitude, offset):
            return (
                amplitude * np.exp(-4 * np.log(2) * ((x - shift) / width) ** 2) + offset
            )

        def pseudo_voigt(x, shift, width, amplitude, offset, eta):
            """
            eta: Mixing parameter, 0 <= eta <= 1
                eta = 1: pure Lorentzian
                eta = 0: pure Gaussian
            """
            g = np.exp(-4 * np.log(2) * ((x - shift) / width) ** 2)
            l = 1 / (1 + ((x - shift) / (width / 2)) ** 2)
            return amplitude * (eta * l + (1 - eta) * g) + offset

        fits = {}
        qts = self.value.analysis.get_all_quantities_in_image((z,y,x))
        for peak in self.value.analysis.list_existing_peak_types():
            try:
                width = qts[bls.Data.AnalysisResults.Quantity.Width.name][peak.name].value
            except Exception as e:
                print(f"Error getting width for peak {peak.name}: {e}")
                width = None
            try:
                shift = qts[bls.Data.AnalysisResults.Quantity.Shift.name][peak.name].value
            except Exception as e:
                print(f"Error getting shift for peak {peak.name}: {e}")
                shift = None
            try:
                amplitude = qts[bls.Data.AnalysisResults.Quantity.Amplitude.name][peak.name].value
            except Exception as e:
                print(f"Error getting amplitude for peak {peak.name}: {e}")
                amplitude = None
            try:
                offset = qts[bls.Data.AnalysisResults.Quantity.Offset.name][peak.name].value
            except Exception as e:
                print(f"Error getting offset for peak {peak.name}: {e}")
                offset = None

            if width is None or shift is None or amplitude is None or offset is None:
                print(
                    f"Skipping peak {peak.name} due to missing parameters: "
                    f"width={width}, shift={shift}, amplitude={amplitude}, offset={offset}"
                )
                continue
            match self.fit_type:
                case self.Fits.Lorentzian:
                    y_values = real_lorentzian(x_range, shift, width, amplitude, offset)
                case self.Fits.Gaussian:
                    y_values = real_gaussian(x_range, shift, width, amplitude, offset)
                case self.Fits.PseudoVoigt50:
                    y_values = pseudo_voigt(
                        x_range, shift, width, amplitude, offset, 0.5
                    )
            fits[peak.name] = y_values
        return fits
    
    @pn.depends("loading", watch=True)
    def loading_spinner(self):
        """
            Controls an additional spinner UI. 
            This goes on top of the `loading` param that comes with panel widgets.

            This is especially usefull in the `panel convert` case, 
            because some UI elements can't updated easily (or at least in the same way as `panel serve`).
            In particular, the visible toggle is not always working, and elements inside Rows and Columns sometimes 
            don't get updated.
        """
        if self.loading:
            self.spinner.value = True
            self.spinner.name = "Loading..."
            self.spinner.visible = True 
        else:
            self.spinner.value = False
            self.spinner.name = "Done"
            self.spinner.visible = True


    @param.depends("display_fit", "fit_type")
    def fitted_curves(self, x_range: np.ndarray, z, y, x):
        print(f"Computing fitted curves at ({time.time()})")
        fits = self._compute_fitted_curves(x_range, z, y, x)
        curves = []
        for fit in fits:
            curves.append(
                hv.Curve((x_range, fits[fit]), label=f"Fitted lorentzian ({fit})").opts(
                    axiswise=True
                )
            )

        return curves

    @pn.depends("dataset_zyx_coord", watch=True, on_init=False)
    def retrieve_point_rawdata(self):
        self.loading = True
        now = time.time()
        print(f"retrieve_point_rawdata at {now:.4f} seconds")    
       
        (z, y, x) = self.get_coordinates()
        if self.value is not None and self.value.data is not None:

            self.bls_spectrum_in_image, self.results_at_point = self.value.data.get_spectrum_and_all_quantities_in_image(
                self.value.analysis, (z, y, x)
            )
        else:
            self.bls_spectrum_in_image = None

        #self.loading = False
        now = time.time()
        print(f"retrieve_point_rawdata at {now:.4f} seconds [done]")
        self.loading = False

    @param.depends("results_at_point", watch=True)
    def result_widget(self):

        if self.results_at_point is None:
            self.quantity_tabulator.value = None
            return

        rows = []
        for quantity_name, quantities in self.results_at_point.items():
            for name, metadata_item in quantities.items():
                rows.append(
                    {
                        "Parameter": name,
                        "Value": metadata_item.value,
                        "Unit": metadata_item.units,
                        "Quantity": quantity_name,
                    }
                )
        df = pd.DataFrame(rows, columns=["Parameter", "Value", "Unit", "Quantity"])
        self.quantity_tabulator.value = df



    # TODO watch=true for side effect ?
    @pn.depends("results_at_point", "fitted_curves", "value", on_init=False)
    def plot_spectrum(self):
        self.loading = True
        now = time.time()
        print(f"plot_spectrum at {now:.4f} seconds")
        (z, y, x) = self.get_coordinates()
        # Generate a fake spectrum for demonstration purposes
        if (
            self.value is not None
            and self.value.data is not None
            and self.bls_spectrum_in_image is not None
        ):
            (PSD, frequency, PSD_units, frequency_units) = self.bls_spectrum_in_image
            x_range = np.arange(np.nanmin(frequency), np.nanmax(frequency), 0.1)
            curves = self.fitted_curves(x_range, z, y, x)
        else:
            print("Warning: No BLS data available. Cannot plot spectrum.")
            # If no data is available, we create empty values
            (PSD, frequency, PSD_units, frequency_units) = ([], [], "", "")
            curves = []
        print(f"Retrieving spectrum took {time.time() - now:.4f} seconds")
        # Get and plot raw spectrum
        h = [
            hv.Points(
                (frequency, PSD),
                kdims=[
                    hv.Dimension("Frequency", unit=frequency_units),
                    hv.Dimension("PSD", unit=PSD_units),
                ],
                label=f"Acquired points",
            ).opts(
                color="black",
                axiswise=True,
            )
            * hv.Curve((frequency, PSD), label=f"interpolation").opts(
                color="black",
                axiswise=True,
            )
        ]

        h.extend(curves)

        print(f"Creating holoview object took {time.time() - now:.4f} seconds")
        self.loading = False

        return hv.Overlay(h).opts(
            axiswise=True,
            legend_position="bottom",
            responsive=True,
            title=f"Spectrum at index (z={z}, y={y}, x={x})",
        )

    def _export_experiment_metadata(self) -> str:
        full_metadata = {}
        for type_name, type_dict in (
            self.value.data.get_metadata().all_to_dict().items()
        ):
            full_metadata[type_name] = {}
            # metadata_dict = metadata.to_dict(type)
            for parameter, item in type_dict.items():
                full_metadata[type_name][parameter] = {}
                full_metadata[type_name][parameter]["value"] = item.value
                full_metadata[type_name][parameter]["units"] = item.units

        metadata_dict = {
            "filename": self.value.file.filename,
            "dataset": {
                "name": self.value.data.get_name(),
                "metadata": full_metadata,
            },
        }
        return yaml.dump(metadata_dict, default_flow_style=False, sort_keys=False)

    def _csv_export_header(self):
        metadata = _convert_numpy(self.results_at_point)
        (z, y, x) = self.get_coordinates()
        header = f"Spectrum from a single point (z={z}, y={y}, x={x}).\n"
        header += " ==== Experiment Metadata ==== \n"
        header += self._export_experiment_metadata()
        header += " ==== Spectrum Metadata ==== \n"
        header += yaml.dump(metadata, default_flow_style=False, sort_keys=False)
        header += "\n"
        header = "\n".join(f"# {line}" for line in header.splitlines())
        return header

    def csv_export(self):
        """
        Create a (temporary) CSV file, with the data from the current plot. This file can then be downloaded.

        The file had a header part (in comment style #), with all the metadata regarding this specific acquisition point.

        Rough stucture:
        ```
        # Spectrum from (z, y, x)
        #
        # {Metadata from the bls file}
        #
        # {Metadata from the specific spectrum}
        frequency, PSD, [fits, ...]
        -5.086766652931395,705.0,537.789088340407,1035.9203244463108
        -5.245067426251495,995.0,537.681849973285,1206.9780168102159
        -5.403368199571595,1372.0,537.5790197104791,1473.234854548628
        ```

        """
        (z, y, x) = self.get_coordinates()

        # Get spectrum data
        if self.value.data is not None:
            PSD, frequency, PSD_unit, freq_unit = self.value.data.get_spectrum_in_image(
                (z, y, x)
            )
            fits = self._compute_fitted_curves(frequency, z, y, x)
        else:
            PSD, frequency = np.array([]), np.array([])
            fits = {}

        # Prepare DataFrame
        df = pd.DataFrame(
            {
                "Frequency": frequency,
                "PSD": PSD,
            }
        )
        for fit in fits:
            df[fit] = fits[fit]

        # Create temporary file
        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".csv", mode="w", newline=""
        )
        tmp.write(self._csv_export_header())
        tmp.write("\n")  # Starting at a new line
        # Write CSV
        df.to_csv(tmp, index=False, mode="a")

        # Important: flush so the file is ready
        tmp.flush()
        tmp.seek(0)

        return tmp.name

    def __panel__(self):
        display_options = pn.Card(
            pn.FlexBox(
                pn.widgets.Checkbox.from_param(self.param.display_fit),
                pn.widgets.Select.from_param(self.param.fit_type, width=150),
            ),
            title="Curve options",
            collapsed=True,
            collapsible=True,
            sizing_mode="stretch_height",
            margin=5,
        )
        coordinates = pn.widgets.LiteralInput.from_param(
            self.param.dataset_zyx_coord, disabled=True
        )

        return pn.Card(
            pn.Row(pn.HSpacer(), self.spinner, pn.HSpacer()),      
            pn.pane.HoloViews(
                self.plot_spectrum,
                height=300,  # Not the greatest solution
                sizing_mode="stretch_width",
            ),
            pn.Column(
                pn.layout.Divider(),
                self.quantity_tabulator,
                sizing_mode="stretch_width",
            ),
            coordinates,
            pn.widgets.FileDownload(callback=self.csv_export, filename="raw_data.csv"),
            display_options,
            sizing_mode="stretch_height",
            title="Spectrum visualization"
        )
