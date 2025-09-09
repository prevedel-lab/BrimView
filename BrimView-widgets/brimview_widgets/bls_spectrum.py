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
import scipy
import inspect
import re

import time
import brimfile as bls
from .models import BlsProcessingModels, MultiPeakModel
from .bls_data_visualizer import BlsDataVisualizer

from .utils import catch_and_notify

from panel.widgets.base import WidgetBase
from panel.custom import PyComponent
from .bls_types import bls_param
from .widgets import SwitchWithLabels


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


class FitParam(pn.viewable.Viewer):
    """
    Storing as a sub-parameterized to avoid polluting the main param space
    """

    # TODO: move this as it's own widget ?

    process = param.Boolean(
        default=True,
        label="Auto re-fit when clicking on a new pixel",
        doc="If enabled, the fit will be recomputed when clicking on a new pixel. If disabled, the previous fit will be used.",
        allow_refs=True,
    )

    model = param.Selector(
        objects=BlsProcessingModels.to_param_dict(),
        doc="Select which processing model to use",
        instantiate=True,
        allow_refs=True,
    )

    fitted_parameters = param.Dict(
        default=None,
        doc="""Parameters from the fit. This is expected to be in the form:
        {
            "peak_name": {"param1": value1, "param2": value2, ...}, 
            "peak_name2": {...},
            ...
        }""",
    )

    lower_bounds = param.Dict(
        default=None,
        doc="""Lower_bounds of the fit. This is expected to be in the form:
        {
            "peak_name": {"param1": value1, "param2": value2, ...}, 
            "peak_name2": {...},
            ...
        }""",
    )

    starting_value = param.Dict(
        default=None,
        doc="""Starting value of the fit. This is expected to be in the form:
        {
            "peak_name": {"param1": value1, "param2": value2, ...}, 
            "peak_name2": {...},
            ...
        }""",
    )

    upper_bound = param.Dict(
        default=None,
        doc="""Upper bound of the fit. This is expected to be in the form:
        {
            "peak_name": {"param1": value1, "param2": value2, ...}, 
            "peak_name2": {...},
            ...
        }""",
    )

    def __init__(self, **params):
        super().__init__(**params)
        # Creating some widget
        self._process_switch = SwitchWithLabels(
            name="",
            value=True,
            label_true="Enable",
            label_false="Disable",
        )
        self.process = self._process_switch.param.value

        self._model_dropdown = pn.widgets.Select.from_param(self.param.model, width=200)

        self._table = pn.widgets.Tabulator(
            show_index=False,
            disabled=True,
            groupby=["Peak"],
            hidden_columns=["Peak"],
            configuration={
                "groupStartOpen": False  # This makes all groups collapsed initially
            },
        )

        # For type annotation
        self.model: BlsProcessingModels
        self.fitted_parameters: dict[str, float] | None
        self.process: bool

    def _update_model_widget(self):
        print(self.param.model.objects)
        if len(self.param.model.objects) == 1:
            self._model_dropdown.disabled = True
            self._model_dropdown.description = "Assumed to be this model"
        else:
            self._model_dropdown.disabled = False
            self._model_dropdown.description = self.param.model.doc

    @pn.depends("fitted_parameters", watch=True)
    def _update_table(self):

        if self.fitted_parameters is None:
            self._table.value = None
            return

        rows = []
        for name, value in self.fitted_parameters.items():
            for param_name, param_value in value.items():
                rows.append(
                    {"Peak": name, "Value": param_value, "Parameter": param_name}
                )
        df = pd.DataFrame(rows, columns=["Parameter", "Value", "Peak"])
        self._table.value = df

    def __panel__(self):
        return pn.Card(
            self._process_switch,
            self._model_dropdown,
            self._table,

            title=self.name,
            collapsible=False,
            margin=5,
        )


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

    saved_fit = FitParam(name="Saved fit")
    auto_refit = FitParam(name="Auto re-fit")

    @pn.depends("auto_refit.model", watch=True)
    def _test_remodel_fit(self):
        print(f"Model fit changed to {self.auto_refit.model}")

    @pn.depends("auto_refit.process", watch=True)
    def _test_model_fit(self):
        print(self.auto_refit.process)

    value = param.ClassSelector(
        class_=bls_param,
        default=None,
        precedence=-1,
        doc="BLS file/data/analysis",
        allow_refs=True,
    )

    results_at_point = param.Dict(label="Result values at this point", precedence=-1)

    def __init__(self, result_plot: BlsDataVisualizer, **params):
        self.spinner = pn.indicators.LoadingSpinner(
            value=False, size=20, name="Idle", visible=True
        )
        self.bls_spectrum_in_image = None
        params["name"] = "Spectrum visualization"
        super().__init__(**params)
        # Watch tap stream updates

        # Reference to the "main" plot_click
        self.dataset_zyx_coord = result_plot.param.dataset_zyx_click

        # Test
        self.value: bls_param = bls_param(
            file=result_plot.param.bls_file,
            data=result_plot.param.bls_data,
            analysis=result_plot.param.bls_analysis,
        )

        self.saved_fit.param.model.objects = {
            "Lorentzian": BlsProcessingModels.Lorentzian
        }
        self.saved_fit._update_model_widget()

        # Because we're not a pn.Viewer anymore, by default we lost the "card" display
        # so despite us returning a card from __panel__, the shown card didn't match
        # the card display (background color, shadows)
        self.css_classes.append("card")

        # Annoation help
        self.model_fit: BlsProcessingModels

    @catch_and_notify(prefix="<b>Compute fitted curves: </b>")
    def _compute_fitted_curves(self, x_range: np.ndarray, z, y, x):
        if self.saved_fit.process is False:
            return []

        fits = {}
        qts = self.results_at_point
        fit_params = {}
        for peak in self.value.analysis.list_existing_peak_types():
            width = qts[bls.Data.AnalysisResults.Quantity.Width.name][peak.name].value
            shift = qts[bls.Data.AnalysisResults.Quantity.Shift.name][peak.name].value
            amplitude = qts[bls.Data.AnalysisResults.Quantity.Amplitude.name][
                peak.name
            ].value
            offset = qts[bls.Data.AnalysisResults.Quantity.Offset.name][peak.name].value

            fit_params[peak.name] = {
                "width": width,
                "shift": shift,
                "amplitude": amplitude,
                "offset": offset,
            }

            if width is None or shift is None or amplitude is None or offset is None:
                pn.state.notifications.warning(
                    f"Skipping peak {peak.name} due to missing parameters: "
                    f"width={width}, shift={shift}, amplitude={amplitude}, offset={offset}"
                )
                continue
            try:
                y_values = self.saved_fit.model.func_with_bls_args(
                    x_range, shift, width, amplitude, offset
                )
                fits[peak.name] = y_values
            except Exception as e:
                pn.state.notifications.error(
                    f"Error computing fit for peak {peak.name}: {e}"
                )
                continue

        self.saved_fit.fitted_parameters = fit_params
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
            self.spinner.name = "Idle"
            self.spinner.visible = True

    def rewrite_card_header(self, card: pn.Card):
        """
        Changes a bit how the header of the card is displayed.
        We replace the default title by
            [{self.name}     {spinner}]

        With self.name to the left and spinner to the right
        """
        params = {
            "object": f"<h3>{self.name}</h3>" if self.name else "&#8203;",
            "css_classes": card.title_css_classes,
            "margin": (5, 0),
        }
        self.spinner.align = ("end", "center")
        self.spinner.margin = (10, 30)
        header = pn.FlexBox(
            pn.pane.HTML(**params),
            # self.spinner,
            # pn.Spacer(),  # pushes next item to the right
            self.spinner,
            align_content="space-between",
            align_items="center",  # Vertical-ish
            sizing_mode="stretch_width",
            justify_content="space-between",
        )
        # header.styles = {"place-content": "space-between"}
        card.header = header
        card._header_layout.styles = {"width": "inherit"}

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

    # TODO: rename to something better
    def auto_refit_and_plot(self, x_range, PSD, frequency, PSD_units, frequency_units):
        if self.auto_refit.process is False:
            return []

        print("Re-fitting curves...")
        # number of peaks
        n_peaks = len(self.value.analysis.list_existing_peak_types())
        previous_fits = {}
        qts = self.results_at_point
        i = 0
        for peak in self.value.analysis.list_existing_peak_types():
            width = qts[bls.Data.AnalysisResults.Quantity.Width.name][peak.name].value
            shift = qts[bls.Data.AnalysisResults.Quantity.Shift.name][peak.name].value
            amplitude = qts[bls.Data.AnalysisResults.Quantity.Amplitude.name][
                peak.name
            ].value
            offset = qts[bls.Data.AnalysisResults.Quantity.Offset.name][peak.name].value

            # Converting to HDF5_BLS_treat naming
            previous_fits[f"b{i}"] = offset
            previous_fits[f"a{i}"] = amplitude
            previous_fits[f"nu0{i}"] = shift
            previous_fits[f"gamma{i}"] = width
            i += 1

        print(f"Previous fits: {previous_fits}")
        # the base model function (e.g. Lorentzian, DHO, etc.)

        multi_peak_model = MultiPeakModel(
            base_model=self.auto_refit.model, n_peaks=n_peaks
        )

        # TODO: define sensible initial guess (p0) and bounds!
        # here just as placeholders
        p0 = multi_peak_model._flatten_kwargs(previous_fits)

        # You could try to make something smart here to block the multiple offsets
        lower_bound = [-np.inf] * len(p0)
        upper_bound = [np.inf] * len(p0)
        bounds = (lower_bound, upper_bound)

        # perform fit
        popt, pcov = scipy.optimize.curve_fit(
            multi_peak_model.function_flat,
            frequency,
            PSD,
            p0=p0,
            bounds=bounds,
        )
        y_fit = multi_peak_model.function_flat(x_range, *popt)

        print("Fitted args:", popt)
        # print("As kwargs:", multi_peak_model._unflatten_args(popt))
        # compute fitted curve for plotting

        self.auto_refit.fitted_parameters = multi_peak_model.unflatten_args_grouped(
            popt
        )

        return [
            hv.Curve((x_range, y_fit), label=f"{multi_peak_model.label}").opts(
                axiswise=True, line_dash="dotted"
            )
        ]

    @pn.depends("dataset_zyx_coord", watch=True, on_init=False)
    @catch_and_notify(prefix="<b>Retrieve data: </b>")
    def retrieve_point_rawdata(self):
        self.loading = True
        now = time.time()
        print(f"retrieve_point_rawdata at {now:.4f} seconds")

        (z, y, x) = self.get_coordinates()
        if self.value is not None and self.value.data is not None:

            self.bls_spectrum_in_image, self.results_at_point = (
                self.value.data.get_spectrum_and_all_quantities_in_image(
                    self.value.analysis, (z, y, x)
                )
            )
        else:
            self.bls_spectrum_in_image = None

        # self.loading = False
        now = time.time()
        print(f"retrieve_point_rawdata at {now:.4f} seconds [done]")
        self.loading = False

    # TODO watch=true for side effect ?
    @pn.depends(
        "results_at_point",
        "saved_fit.process",
        "saved_fit.model",
        "auto_refit.process",
        "auto_refit.model",
        "value",
        on_init=False,
    )
    @catch_and_notify(prefix="<b>Plot spectrum: </b>")
    def plot_spectrum(self):
        self.loading = True
        now = time.time()
        print(f"plot_spectrum at {now:.4f} seconds")
        (z, y, x) = self.get_coordinates()
        # Generate a fake spectrum for demonstration purposes
        curves = []
        if (
            self.value is not None
            and self.value.data is not None
            and self.bls_spectrum_in_image is not None
        ):
            (PSD, frequency, PSD_units, frequency_units) = self.bls_spectrum_in_image
            x_range = np.arange(np.nanmin(frequency), np.nanmax(frequency), 0.1)

            if self.saved_fit.process:
                saved_curves = self.fitted_curves(x_range, z, y, x)
                curves.extend(saved_curves)

            if self.auto_refit.process:
                refit_curves = self.auto_refit_and_plot(
                    x_range, PSD, frequency, PSD_units, frequency_units
                )
                curves.extend(refit_curves)
        else:
            print("Warning: No BLS data available. Cannot plot spectrum.")
            # If no data is available, we create empty values
            (PSD, frequency, PSD_units, frequency_units) = ([], [], "", "")
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
                marker='+',
                size=10
            )
            # * hv.Curve((frequency, PSD), label=f"interpolation").opts(
            #     color="black",
            #     axiswise=True,
            # )
        ]

        h.extend(curves)

        print(f"Creating holoview object took {time.time() - now:.4f} seconds")
        self.loading = False

        return hv.Overlay(h).opts(
            axiswise=True,
            legend_position="bottom",
            legend_cols=3,
            responsive=True,
            title=f"Spectrum at index (z={z}, y={y}, x={x})",
        )

    @catch_and_notify(prefix="<b>Export metadata: </b>")
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

    @catch_and_notify(prefix="<b>Export CSV: </b>")
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
            PSD, frequency, PSD_unit, freq_unit = self.bls_spectrum_in_image
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

        card = pn.Card(
            pn.pane.HoloViews(
                self.plot_spectrum,
                height=300,  # Not the greatest solution
                sizing_mode="stretch_width",
            ),
            pn.widgets.FileDownload(callback=self.csv_export, filename="raw_data.csv"),
            pn.FlexBox(self.saved_fit, self.auto_refit),
            sizing_mode="stretch_height",
        )

        self.rewrite_card_header(card)
        return card
